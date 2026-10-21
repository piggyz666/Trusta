"""No .env or real wallet. Public transport failures use synthetic/disposable RPCs."""
import copy
import json
import os
import tempfile
import threading
import time
import unittest
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from eth_account import Account
from eth_utils import keccak
from backend.persistence import Database
from backend.domain import Rejected, canonical, digest
from backend.audit_trust.metamask_ingress import SignedIngress
from backend.audit_trust.public_ingress import PublicDelivery, verify_deployment, expected_runtime, verify_signed_fields
from backend.audit_trust.transaction_safety import plan_hash, prepare_transaction
from scripts.sepolia_metamask_gateway import Gateway, make_server, commit_data, validate_plan_intent, quote
from scripts.metamask_anchor_lab import LocalEVM
from scripts.prepare_anchor_business_evidence import prepare
from scripts.verify_sepolia_acceptance import verify
from tests.test_sepolia_readiness import PlanRPC, OWNER, KEY, DEPLOY


class DeliveryRPC(PlanRPC):
    def __init__(self):super().__init__();self.known=False;self.lose=False;self.before_send=None
    def call(self,method,params):
        if method=='eth_getTransactionByHash':return {'hash':params[0]} if self.known else None
        if method=='eth_sendRawTransaction':
            if self.before_send:self.before_send()
            result=super().call(method,params);self.known=True
            if self.lose:raise Rejected('ANCHOR_RPC_UNAVAILABLE')
            return result
        return super().call(method,params)


class DeliveryFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.rpc=DeliveryRPC();self.db=Database(Path(self.tmp.name)/'outbox.sqlite3')
        self.plan=prepare_transaction(self.rpc,OWNER,DEPLOY,'deploy');self.hash=plan_hash(self.plan)
        self.raw='0x'+Account.from_key(KEY).sign_transaction(self.plan['transaction']).raw_transaction.hex()
        self.env=patch.dict(os.environ,{'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true',
            'ANCHOR_APPROVED_PLAN_HASH':self.hash,'ANCHOR_WALLET_PRIVATE_KEY':'','ANCHOR_KEYSTORE_PATH':''})
        self.env.start();self.addCleanup(self.env.stop)
        self.delivery=PublicDelivery(SignedIngress(self.db,self.rpc,self.plan,self.hash))


class PublicDeliveryTests(DeliveryFixture, unittest.TestCase):

    def test_nonce_raw_and_attempt_fsynced_before_sending(self):
        def before():
            d=self.delivery.inspect();self.assertEqual(d['status'],'SEND_IN_FLIGHT');self.assertEqual(d['external_broadcasts'],1)
            self.assertEqual(self.db.one('SELECT tx_hash FROM external_anchor_nonces')['tx_hash'],d['tx_hash'])
            self.assertEqual(json.loads(self.db.one('SELECT body FROM anchor_ingress_outbox')['body'])['raw_tx'],self.raw)
        self.rpc.before_send=before
        h=self.delivery.send(self.raw)
        self.assertEqual(h,self.delivery.inspect()['tx_hash']);self.assertEqual(len(self.rpc.sent),1)
        self.assertEqual(self.delivery.inspect()['status'],'SENT')

    def test_restart_and_duplicate_readback_do_not_resend(self):
        h=self.delivery.send(self.raw);self.rpc.nonce=1
        restarted=PublicDelivery(SignedIngress(Database(self.db.path),self.rpc,self.plan,self.hash))
        self.assertEqual(restarted.send(self.raw),h);self.assertEqual(len(self.rpc.sent),1)

    def test_read_only_recovery_with_dead_worker_claim(self):
        h=self.delivery.send(self.raw)
        with self.db.transaction() as c:
            c.execute('INSERT INTO graph_worker_claims VALUES(?,?,?)',('anchor-wallet:11155111:'+OWNER.lower(),'dead-worker',2147483647))
        restarted=PublicDelivery(SignedIngress(Database(self.db.path),self.rpc,self.plan,self.hash))
        self.assertEqual(restarted.reconcile()['tx_hash'],h)
        self.assertEqual(restarted.send(self.raw),h)
        self.assertEqual(len(self.rpc.sent),1)
        self.assertIsNotNone(self.db.one('SELECT * FROM graph_worker_claims'))

    def test_lost_response_retains_hash_and_reconciles_read_only(self):
        self.rpc.lose=True
        with self.assertRaises(Rejected):self.delivery.send(self.raw)
        self.assertEqual(self.delivery.inspect()['status'],'UNCERTAIN')
        h=self.delivery.send(self.raw);self.assertEqual(h,self.delivery.inspect()['tx_hash'])
        self.assertEqual(len(self.rpc.sent),1)

    def test_uncertain_not_found_never_allocates_nonce_or_resends(self):
        self.rpc.lose=True
        with self.assertRaises(Rejected):self.delivery.send(self.raw)
        self.rpc.known=False
        with self.assertRaisesRegex(Rejected,'ANCHOR_UNCERTAIN_SEND_REQUIRES_INSPECTION'):self.delivery.send(self.raw)
        self.assertEqual(len(self.rpc.sent),1);self.assertEqual(len(self.db.rows('SELECT * FROM external_anchor_nonces')),1)

    def test_global_flags_and_exact_approval_required_even_after_capture(self):
        self.delivery.ingress.capture(self.raw)
        for name,value in [('ANCHOR_ALLOW_SIGNING','false'),('ANCHOR_ALLOW_BROADCAST','false'),('ANCHOR_APPROVED_PLAN_HASH','f'*64)]:
            with patch.dict(os.environ,{name:value}),self.assertRaises(Rejected):self.delivery.send(self.raw)
        self.assertEqual(self.rpc.sent,[]);self.assertIsNone(self.delivery.inspect())

    def test_wrong_chain_and_field_rewrite_cannot_broadcast(self):
        self.rpc.chain=1
        with self.assertRaises(Rejected):self.delivery.send(self.raw)
        self.rpc.chain=11155111
        for key,value in [('value',1),('nonce',1),('gas',self.plan['transaction']['gas']+1),('maxFeePerGas',2_000_000_000)]:
            tx=self.plan['transaction']|{key:value}
            raw='0x'+Account.from_key(KEY).sign_transaction(tx).raw_transaction.hex()
            with self.subTest(key=key),self.assertRaises(Rejected):self.delivery.send(raw)
        self.assertEqual(self.rpc.sent,[])

    def test_expiry_after_capture_still_blocks_initial_send(self):
        self.delivery.ingress.capture(self.raw)
        with patch('backend.audit_trust.metamask_ingress.time.time',return_value=self.plan['quoted_at']+121),self.assertRaises(Rejected):
            self.delivery.send(self.raw)
        self.assertEqual(self.rpc.sent,[])

    def test_delivery_storage_failure_before_network_send(self):
        with self.db.transaction() as c:c.execute("CREATE TRIGGER fail_delivery BEFORE INSERT ON public_anchor_delivery BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(Exception):self.delivery.send(self.raw)
        self.assertEqual(self.rpc.sent,[]);self.assertIsNotNone(self.delivery.ingress.inspect())

    def test_concurrent_duplicate_has_at_most_one_send(self):
        def attempt(_):
            d=PublicDelivery(SignedIngress(Database(self.db.path),self.rpc,self.plan,self.hash))
            try:return d.send(self.raw)
            except Rejected:return 'BUSY'
        with ThreadPoolExecutor(4) as pool:results=list(pool.map(attempt,range(4)))
        self.assertEqual(len(self.rpc.sent),1);self.assertTrue(any(r.startswith('0x') for r in results))

    def test_low_tip_has_bounded_floor(self):
        self.rpc.tip=1;p=prepare_transaction(self.rpc,OWNER,DEPLOY,'deploy')
        self.assertEqual(p['transaction']['maxPriorityFeePerGas'],1_000_000)
        self.rpc.tip=100_000_001
        with self.assertRaises(Rejected):prepare_transaction(self.rpc,OWNER,DEPLOY,'deploy')

    def test_tampered_persisted_delivery_cannot_reconcile(self):
        self.delivery.send(self.raw)
        record=self.delivery.inspect();record['tx_hash']='0x'+'f'*64;self.delivery._save(record)
        with self.assertRaises(Rejected):self.delivery.reconcile()
        self.assertEqual(len(self.rpc.sent),1)


class PublicSignatureEncodingTests(unittest.TestCase):
    def fixture(self):
        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
        tx={'type':2,'chainId':11155111,'nonce':1,'value':0,'gas':427643,
            'maxFeePerGas':1000030,'maxPriorityFeePerGas':1000004,
            'to':Account.from_key(KEY).address,'data':'0xf336cff2'}
        signed=Account.from_key(KEY).sign_transaction(tx)
        decoded=TypedTransaction.from_bytes(HexBytes(signed.raw_transaction)).as_dict()
        rpc={k:hex(decoded[k]) for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas','v','r','s')}
        rpc.update(input=tx['data'],to=tx['to'].lower(),accessList=[],
            hash='0x'+keccak(signed.raw_transaction).hex(),**{'from':OWNER.lower()})
        return rpc,{'transaction':tx,'sender':OWNER}

    def test_lowercase_contract_address_recovers_exact_public_signature(self):
        rpc,plan=self.fixture()
        self.assertTrue(verify_signed_fields(rpc,plan,rpc['hash']))

    def test_address_or_signed_field_changes_still_rejected(self):
        rpc,plan=self.fixture()
        for field,value in [('to','0x'+'12'*20),('to','0x12'),('nonce','0x2'),
                ('chainId','0x1'),('input','0xf336cff200'),('gas','0x1'),
                ('maxFeePerGas','0x1'),('s','0x0'),('hash','0x'+'ab'*32)]:
            with self.subTest(field=field,value=value):
                self.assertFalse(verify_signed_fields(rpc|{field:value},plan,rpc['hash']))


class GatewayProtocolTests(DeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp();self.gateway=Gateway(self.delivery,Path(self.tmp.name)/'session')
        self.server=make_server(self.gateway,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'

    def post(self,route,body,origin=None):
        req=Request(self.origin+'/'+route+'/'+self.gateway.token,data=canonical(body).encode(),
            headers={'Content-Type':'application/json','Origin':origin or self.origin},method='POST')
        with urlopen(req,timeout=5) as response:return json.loads(response.read())

    def test_actual_http_chain_preflight_and_rejection(self):
        r=self.post('rpc',{'jsonrpc':'2.0','id':1,'method':'eth_chainId','params':[]})
        self.assertEqual(r['result'],'0xaa36a7')
        req=Request(self.origin+'/rpc/'+self.gateway.token,method='OPTIONS',headers={
            'Origin':'chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn','Access-Control-Request-Method':'POST',
            'Access-Control-Request-Headers':'content-type','Access-Control-Request-Private-Network':'true'})
        with urlopen(req,timeout=5) as response:self.assertEqual(response.status,204)
        with self.assertRaises(HTTPError):self.post('rpc',{},'https://evil.example')
        with self.assertRaises(HTTPError):self.post('bypass',{})
        for method in ('eth_sendTransaction','eth_sign','personal_sign','wallet_sendCalls','debug_traceTransaction'):
            with self.subTest(method=method):self.assertIn('error',self.post('rpc',{'jsonrpc':'2.0','id':2,'method':method,'params':[]}))
        self.assertEqual(self.rpc.sent,[])

    def test_actual_http_route_challenge_unforgeable_and_single_use(self):
        c=self.post('challenge',{})
        with self.assertRaises(HTTPError):self.post('verify-route',{'nonce':c['nonce'],'proof':'0x'+'0'*64})
        c=self.post('challenge',{})
        proof=self.post('rpc',{'jsonrpc':'2.0','id':1,'method':'eth_call','params':[{'from':OWNER,'to':c['to'],'data':c['nonce']},'latest']})['result']
        self.assertTrue(self.post('verify-route',{'nonce':c['nonce'],'proof':proof})['verified'])
        with self.assertRaises(HTTPError):self.post('verify-route',{'nonce':c['nonce'],'proof':proof})

    def test_actual_http_signed_send_and_serialized_plan(self):
        info=self.post('plan',{});self.assertEqual(info['plan_hash'],self.hash)
        r=self.post('rpc',{'jsonrpc':'2.0','id':'send','method':'eth_sendRawTransaction','params':[self.raw]})
        self.assertEqual(r['result'],self.delivery.inspect()['tx_hash']);self.assertEqual(len(self.rpc.sent),1)

    def test_session_network_is_bound_to_server_not_a_fake_chain(self):
        network=self.post('plan',{})['metamask_network']
        self.assertEqual(network['chainId'],'0xaa36a7')
        self.assertEqual(network['rpcUrls'],[self.origin+'/rpc/'+self.gateway.token])
        self.assertEqual(network['nativeCurrency'],{'name':'Sepolia Ether','symbol':'SepoliaETH','decimals':18})
        other=Gateway(self.delivery,Path(self.tmp.name)/'other');server=make_server(other,0)
        try:self.assertNotEqual(network['chainName'],other.network()['chainName'])
        finally:server.server_close()

    def test_failed_public_rpc_probe_is_diagnosed_without_secrets(self):
        c=self.post('challenge',{})
        with self.assertRaises(HTTPError):self.post('verify-route',{'nonce':c['nonce'],'proof':'0x'})
        text=(self.gateway.directory/'route-events.jsonl').read_text()
        event=json.loads(text.splitlines()[-1])
        self.assertEqual(event['event'],'route_rejected');self.assertFalse(event['observed_at_ingress'])
        self.assertTrue(event['proof_is_empty']);self.assertNotIn(self.gateway.token,text);self.assertNotIn(c['nonce'],text)

    def test_wallet_setup_fail_closed_even_if_add_network_is_ignored(self):
        import subprocess,shutil
        from scripts.sepolia_metamask_gateway import JS
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node required for wallet-page contract verification')
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{for(const mode of ['ok','ignored','rejected','ambiguous','switch']){
 const elements={'#out':{textContent:''},'#send':{disabled:true},'#connect':{}};const calls=[];let switched=false;
 const info={approved:true,plan_hash:'hash',plan:{sender:'0xabc',quoted_at:Date.now()/1000,quote_ttl:120},metamask_network:{chainId:'0xaa36a7',rpcUrls:['http://127.0.0.1:18766/rpc/session'],nativeCurrency:{name:'Sepolia Ether',symbol:'SepoliaETH',decimals:18}}};
 const provider={isMetaMask:true,request:async({method,params})=>{calls.push(method);
 if(method==='wallet_addEthereumChain')throw Error('Conflicting network registration must not be requested');
 if(method==='wallet_switchEthereumChain'){switched=true;return null}
 if(method==='eth_requestAccounts'||method==='eth_accounts'){if(mode==='rejected')throw Error('USER_REJECTED');return ['0xabc']}
 if(method==='eth_chainId')return mode==='switch'&&!switched?'0x1':'0xaa36a7';
 if(method==='eth_call')return mode==='ignored'?'0x':'proof';throw Error('Unexpected '+method)}};
 const injected=mode==='ambiguous'?{providers:[provider,provider]}:provider;
 const ctx={window:{ethereum:injected},document:{querySelector:s=>elements[s]},Date,
 fetch:async(path,req)=>{const body=JSON.parse(req.body);let value=info,ok=true;
 if(path.startsWith('/challenge'))value={to:'identity',nonce:'nonce'};
 if(path.startsWith('/verify-route')){ok=body.proof==='proof';value=ok?{verified:true}:{error:'ANCHOR_ROUTE_UNVERIFIED'}};
 return {ok,json:async()=>value}}};
 vm.runInNewContext(source,ctx);await elements['#connect'].onclick();
 assert.equal(elements['#send'].disabled,!['ok','switch'].includes(mode));assert.equal(calls.includes('eth_sendTransaction'),false);
 if(mode==='ignored'){assert.equal(elements['#out'].textContent.includes('ANCHOR_ROUTE_UNVERIFIED'),true);assert.equal(elements['#out'].textContent.includes(info.metamask_network.rpcUrls[0]),true)};
 assert.equal(calls.includes('wallet_addEthereumChain'),false);assert.equal(calls.includes('wallet_switchEthereumChain'),mode==='switch');
 if(mode==='rejected')assert.equal(calls.includes('eth_call'),false);
 }console.log('5 wallet page scenarios passed; simulated provider only')})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run([node,'-e',harness],input=json.dumps(JS),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)


class LocalPublicShapeRPC:
    """Actual PyEVM execution, RPC shaped to match a PUBLIC receipt. No network."""
    def __init__(self,wallet,url):self.local=LocalEVM(wallet);self.url=url;self.wallet=wallet.address
    def call(self,method,params):
        if method=='eth_call':params=[{'from':self.wallet}|params[0],*params[1:]]
        value=self.local.call(method,params)
        if method=='eth_getTransactionByHash' and value:
            value['input']=value.pop('data');value['chainId']='0xaa36a7'
        if method in {'eth_getTransactionByHash','eth_getTransactionReceipt'} and value and value.get('to')=='':
            value['to']=None  # PyEVM uses an empty string; public JSON-RPC uses null.
        return value


class LocalE2ETests(unittest.TestCase):
    def test_real_vm_deploy_commit_business_proof_and_third_party_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);summary=prepare(root/'business')
            bundle=json.loads((root/'business/business-record.json').read_text());pem=(root/'business/audit-public-key.pem').read_text()
            self.assertTrue(summary['record_verified']);self.assertEqual(prepare(root/'business')['root'],summary['root'])
            wallet=Account.create();rpc=LocalPublicShapeRPC(wallet,'https://primary.example')
            plan=prepare_transaction(rpc,wallet.address,DEPLOY,'deploy');raw='0x'+wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
            db=Database(root/'business/business.sqlite3')
            with patch.dict(os.environ,{'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true','ANCHOR_APPROVED_PLAN_HASH':plan_hash(plan)}):
                delivery=PublicDelivery(SignedIngress(db,rpc,plan,plan_hash(plan)));txhash=delivery.send(raw)
            rpc.local.evm.mine_blocks(2)
            deployment=verify_deployment(rpc,plan,txhash)
            self.assertTrue(deployment['verified'],deployment)
            self.assertEqual(rpc.call('eth_getCode',[deployment['contract'],'latest']),expected_runtime(wallet.address))
            data=commit_data(bundle,pem)
            with patch.dict(os.environ,{'ANCHOR_CONTRACT_CODE_HASH':deployment['code_hash']}):
                commit=prepare_transaction(rpc,wallet.address,data,'commit',deployment['contract'])
                with patch('scripts.sepolia_metamask_gateway.WALLET',wallet.address),patch.dict(os.environ,{'ANCHOR_WALLET_ADDRESS':wallet.address}):
                    self.assertEqual(quote(rpc,'commit',db.path,bundle,pem,deployment['contract'])['transaction']['data'],data)
                raw='0x'+wallet.sign_transaction(commit['transaction']).raw_transaction.hex()
                with patch.dict(os.environ,{'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true','ANCHOR_APPROVED_PLAN_HASH':plan_hash(commit)}):
                    commit_delivery=PublicDelivery(SignedIngress(db,rpc,commit,plan_hash(commit)));commit_hash=commit_delivery.send(raw)
            rpc.local.evm.mine_blocks(2)
            secondary=LocalPublicShapeRPC.__new__(LocalPublicShapeRPC);secondary.local=rpc.local;secondary.wallet=wallet.address;secondary.url='https://independent.example'
            result=verify(rpc,secondary,plan,txhash,commit_hash,bundle,pem,commit)
            self.assertTrue(result['external_blockchain_verified'],result)
            self.assertEqual(len(rpc.local.sent),2)
            self.assertEqual(delivery.inspect()['external_broadcasts'],1);self.assertEqual(commit_delivery.inspect()['external_broadcasts'],1)
            # These are local shape fixtures, NOT proof of actual provider independence.
            tampered=copy.deepcopy(bundle);tampered['receipt']['task_id']='other-task'
            self.assertFalse(verify(rpc,secondary,plan,txhash,commit_hash,tampered,pem,commit)['external_blockchain_verified'])
            bad_commit=copy.deepcopy(commit);bad_commit['transaction']['nonce']+=1
            self.assertFalse(verify(rpc,secondary,plan,txhash,commit_hash,bundle,pem,bad_commit)['external_blockchain_verified'])
            self.assertFalse(verify(rpc,secondary,plan,txhash,commit_hash,bundle,pem)['external_blockchain_verified'])
            from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
            self.assertTrue(ExternalBlockchainAnchor(db,rpc,deployment['contract']).inspect(commit_hash)['externally_verified'])
            for field,value in [('nonce',1),('value',1),('gas',plan['transaction']['gas']-1)]:
                altered=copy.deepcopy(plan);altered['transaction'][field]=value
                self.assertFalse(verify_deployment(rpc,altered,txhash)['verified'])
            with patch('scripts.sepolia_metamask_gateway.WALLET',wallet.address):
                validate_plan_intent(commit,bundle,pem)
                altered=copy.deepcopy(commit);altered['transaction']['data']=data[:74]+'f'*64+data[138:]
                with self.assertRaises(Rejected):validate_plan_intent(altered,bundle,pem)

    def test_same_provider_and_missing_confirmations_reject(self):
        wallet=Account.create();rpc=LocalPublicShapeRPC(wallet,'https://same.example')
        plan=prepare_transaction(rpc,wallet.address,DEPLOY,'deploy')
        raw='0x'+wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        txhash=rpc.local.call('eth_sendRawTransaction',[raw])
        self.assertFalse(verify_deployment(rpc,plan,txhash)['verified'])
        with self.assertRaises(Rejected):verify(rpc,rpc,plan,txhash,'0x'+'0'*64,{},'')

    def test_bad_rpc_signature_and_reorg_block_fail_verification(self):
        wallet=Account.create();rpc=LocalPublicShapeRPC(wallet,'https://fixture.example')
        plan=prepare_transaction(rpc,wallet.address,DEPLOY,'deploy')
        h=rpc.local.call('eth_sendRawTransaction',['0x'+wallet.sign_transaction(plan['transaction']).raw_transaction.hex()])
        rpc.local.evm.mine_blocks(2);original=rpc.call
        for method in ('eth_getTransactionByHash','eth_getBlockByNumber'):
            def corrupt(m,params):
                result=original(m,params)
                if m==method:
                    result=dict(result)
                    if m=='eth_getTransactionByHash':result['r']='0x1'
                    else:result['hash']='0x'+'f'*64
                return result
            with self.subTest(method=method),patch.object(rpc,'call',side_effect=corrupt):
                self.assertFalse(verify_deployment(rpc,plan,h)['verified'])


class OperatorTests(unittest.TestCase):
    def test_rpc_session_reuse_retains_url_but_never_old_approval(self):
        from scripts.sepolia_metamask_gateway import main
        for valid in (True,False):
            with self.subTest(valid=valid),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);plan_path=root/'plan.json';primary=DeliveryRPC();secondary=DeliveryRPC()
                primary.url='https://primary.example';secondary.url='https://secondary.example'
                def server_for(gateway,port):
                    server=make_server(gateway,port);page=f'http://127.0.0.1:{server.server_port}/'
                    previous={'scope':'ETHEREUM_SEPOLIA_PUBLIC','wallet':OWNER,'page':page,'ingress_rpc':page+'rpc/'+'a'*64,
                        'approved':True,'plan_hash':'b'*64,'bypass_route':False}
                    if not valid:previous['page']='http://evil.example/'
                    (root/'public-operator-session.json').write_text(json.dumps(previous))
                    return server
                def answer(prompt):
                    if 'PREPARE' in prompt:
                        current=json.loads((root/'public-operator-session.json').read_text())
                        self.assertFalse(current['approved']);self.assertTrue(current['ingress_rpc'].endswith('a'*64))
                        self.assertNotIn('plan_hash',current);return 'PREPARE'
                    return plan_hash(json.loads(plan_path.read_text()))
                with patch.dict(os.environ,{'ANCHOR_RPC_URL':primary.url,'ANCHOR_VERIFY_RPC_URL':secondary.url,'ANCHOR_WALLET_ADDRESS':OWNER}),\
                        patch('scripts.sepolia_metamask_gateway.WALLET',OWNER),patch('scripts.sepolia_metamask_gateway.load_local_environment'),\
                        patch('scripts.sepolia_metamask_gateway.EthereumRPC',side_effect=[primary,secondary]),\
                        patch('scripts.sepolia_metamask_gateway.make_server',side_effect=server_for),\
                        patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=answer),\
                        patch('sys.argv',['gateway','operator','--reuse-rpc-session','--directory',str(root),'--db',str(root/'db.sqlite3'),'--plan',str(plan_path),'--port','0']),\
                        patch('scripts.sepolia_metamask_gateway.time.sleep',side_effect=KeyboardInterrupt),patch('sys.stdout',new_callable=io.StringIO) as output:
                    with self.assertRaises(KeyboardInterrupt if valid else SystemExit):main()
                    if not valid:self.assertIn('ANCHOR_RPC_SESSION_BINDING_INVALID',output.getvalue())
                    self.assertEqual(primary.sent,[]);self.assertEqual(secondary.sent,[])

    def test_noninteractive_operator_rejected_before_configuration_or_rpc(self):
        from scripts.sepolia_metamask_gateway import main
        with patch('sys.argv',['gateway','operator']),patch('sys.stdin.isatty',return_value=False),\
                patch('scripts.sepolia_metamask_gateway.load_local_environment') as load,patch('sys.stdout',new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit):main()
            self.assertFalse(load.called);self.assertIn('ANCHOR_OPERATOR_TERMINAL_REQUIRED',output.getvalue())

    def test_actual_operator_exact_hash_flow_never_signs_automatically(self):
        from scripts.sepolia_metamask_gateway import main
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan_path=root/'plan.json';primary=DeliveryRPC();secondary=DeliveryRPC()
            primary.url='https://primary.example';secondary.url='https://secondary.example'
            def answer(prompt):
                return 'PREPARE' if 'PREPARE' in prompt else plan_hash(json.loads(plan_path.read_text()))
            with patch.dict(os.environ,{'ANCHOR_RPC_URL':primary.url,'ANCHOR_VERIFY_RPC_URL':secondary.url,'ANCHOR_WALLET_ADDRESS':OWNER}),\
                    patch('scripts.sepolia_metamask_gateway.WALLET',OWNER),patch('scripts.sepolia_metamask_gateway.load_local_environment'),\
                    patch('scripts.sepolia_metamask_gateway.EthereumRPC',side_effect=[primary,secondary]),\
                    patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=answer),\
                    patch('sys.argv',['gateway','operator','--directory',str(root),'--db',str(root/'db.sqlite3'),'--plan',str(plan_path),'--port','0']),\
                    patch('scripts.sepolia_metamask_gateway.time.sleep',side_effect=KeyboardInterrupt),patch('sys.stdout',new_callable=io.StringIO):
                with self.assertRaises(KeyboardInterrupt):main()
                approval=json.loads((root/'deploy-operator-approval.json').read_text())
                session=json.loads((root/'public-operator-session.json').read_text())
                self.assertTrue(session['approved']);self.assertFalse(session['bypass_route'])
                self.assertFalse(session['production_compatible'])
                self.assertEqual(approval['plan_hash'],plan_hash(json.loads(plan_path.read_text())))
                self.assertEqual(primary.sent,[]);self.assertEqual(secondary.sent,[])
                self.assertEqual(os.environ['ANCHOR_ALLOW_BROADCAST'],'false')


if __name__=='__main__':unittest.main()
