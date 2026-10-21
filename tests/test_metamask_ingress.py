"""Anchor ingress attacks: disposable accounts, local RPC/PyEVM, no .env."""
import copy
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from eth_account import Account
from backend.domain import Rejected
from backend.persistence import Database
from backend.audit_trust.metamask_ingress import SignedIngress, validate_signed_plan
from backend.audit_trust.transaction_safety import prepare_transaction, plan_hash
from tests.test_sepolia_readiness import PlanRPC, OWNER, DEPLOY, KEY, COMMIT, TARGET, CODEHASH
from scripts.metamask_anchor_lab import Lab, make_server


class IngressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/'outbox/db.sqlite3');self.rpc=PlanRPC()
        self.plan=prepare_transaction(self.rpc,OWNER,DEPLOY,'deploy');self.hash=plan_hash(self.plan)
        self.raw='0x'+Account.from_key(KEY).sign_transaction(self.plan['transaction']).raw_transaction.hex()

    def ingress(self,plan=None):
        plan=plan or self.plan;return SignedIngress(self.db,self.rpc,plan,plan_hash(plan))

    def test_capture_persists_nonce_and_raw_before_any_broadcast(self):
        g=self.ingress();r=g.capture(self.raw)
        self.assertEqual(r['status'],'CAPTURED_NOT_BROADCAST');self.assertEqual(r['broadcasts'],0)
        self.assertEqual(self.rpc.sent,[])
        self.assertEqual(self.db.one('SELECT tx_hash FROM external_anchor_nonces')['tx_hash'],r['tx_hash'])
        self.assertEqual(json.loads(self.db.one('SELECT body FROM anchor_ingress_outbox')['body'])['raw_tx'],self.raw)
        c=self.db.connect()
        try:self.assertEqual(c.execute('PRAGMA synchronous').fetchone()[0],2)
        finally:c.close()

    def test_restart_and_repeat_returns_same_bytes_not_new_execution(self):
        r=self.ingress().capture(self.raw)
        g=SignedIngress(Database(self.db.path),self.rpc,self.plan,self.hash)
        self.assertEqual(g.inspect(),r);self.assertEqual(g.capture(self.raw),r)
        self.assertEqual(len(self.db.rows('SELECT * FROM external_anchor_nonces')),1)
        self.assertEqual(self.rpc.sent,[])

    def test_every_rewritten_transaction_field_rejected(self):
        cases={'chainId':1,'nonce':1,'value':1,'data':'0x6000','gas':self.plan['transaction']['gas']-1,
               'maxFeePerGas':self.plan['transaction']['maxFeePerGas']+1,
               'maxPriorityFeePerGas':1,'to':TARGET,'accessList':[{'address':TARGET,'storageKeys':[]}]}
        for k,v in cases.items():
            with self.subTest(field=k):
                tx=copy.deepcopy(self.plan['transaction']);tx[k]=v
                raw='0x'+Account.from_key(KEY).sign_transaction(tx).raw_transaction.hex()
                with self.assertRaises(Rejected):self.ingress().capture(raw)
        self.assertEqual(self.db.rows('SELECT * FROM external_anchor_nonces'),[]);self.assertEqual(self.rpc.sent,[])

    def test_wrong_sender_unapproved_hash_expired_plan_and_wrong_rpc(self):
        raw='0x'+Account.create().sign_transaction(self.plan['transaction']).raw_transaction.hex()
        with self.assertRaises(Rejected):self.ingress().capture(raw)
        with self.assertRaises(Rejected):SignedIngress(self.db,self.rpc,self.plan,'f'*64)
        old=copy.deepcopy(self.plan);old['quoted_at']-=121
        with self.assertRaises(Rejected):self.ingress(old).capture(self.raw)
        self.rpc.chain=1
        with self.assertRaises(Rejected):self.ingress().capture(self.raw)
        self.assertEqual(self.rpc.sent,[])

    def test_malformed_large_raw_and_non_integer_plan_rejected(self):
        for raw in ['0x','0x01','0x02zz','0x02'+'ab'*50000,{},None]:
            with self.subTest(raw_type=type(raw).__name__),self.assertRaises(Rejected):self.ingress().capture(raw)
        for key,value in [('nonce',False),('value',0.0),('gas','0x123')]:
            p=copy.deepcopy(self.plan);p['transaction'][key]=value
            with self.assertRaises(Rejected):self.ingress(p).capture(self.raw)
        self.assertEqual(self.rpc.sent,[])

    def test_estimate_cost_balance_and_nonce_changes_reject(self):
        for field,value in [('nonce',1),('gas',5_000_000),('balance',0),('tip',100_000_001)]:
            with self.subTest(field=field):
                original=getattr(self.rpc,field);setattr(self.rpc,field,value)
                with self.assertRaises(Rejected):self.ingress().capture(self.raw)
                setattr(self.rpc,field,original)
        self.assertEqual(self.rpc.sent,[])

    def test_atomic_failure_rolls_back_nonce_as_well_as_outbox(self):
        g=self.ingress()
        with self.db.transaction() as c:c.execute("CREATE TRIGGER ingress_fail BEFORE INSERT ON anchor_ingress_outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(Exception):g.capture(self.raw)
        self.assertEqual(self.db.rows('SELECT * FROM external_anchor_nonces'),[])
        self.assertEqual(self.rpc.sent,[])

    def test_two_instances_compete_without_nonce_or_raw_overwrite(self):
        self.ingress();barrier=threading.Barrier(2)
        def submit():
            g=SignedIngress(Database(self.db.path),self.rpc,self.plan,self.hash);barrier.wait()
            try:return g.capture(self.raw)['tx_hash']
            except Rejected as e:return e.code
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(lambda _:submit(),range(2)))
        self.assertTrue(any(x.startswith('0x') for x in result));self.assertEqual(len(self.db.rows('SELECT * FROM anchor_ingress_outbox')),1)
        self.assertEqual(self.rpc.sent,[])

    def test_reserved_nonce_cannot_be_consumed_by_different_plan(self):
        self.ingress().capture(self.raw)
        other=copy.deepcopy(self.plan);other['transaction']['gas']+=1;other['worst_cost_wei']+=other['transaction']['maxFeePerGas']
        raw='0x'+Account.from_key(KEY).sign_transaction(other['transaction']).raw_transaction.hex()
        with self.assertRaises(Rejected) as e:self.ingress(other).capture(raw)
        self.assertEqual(e.exception.code,'ANCHOR_NONCE_RESERVED')

    def test_commit_existing_fields_publisher_and_code_pin(self):
        self.rpc.gas=356357
        with patch.dict('os.environ',{'ANCHOR_CONTRACT_CODE_HASH':CODEHASH}):
            p=prepare_transaction(self.rpc,OWNER,COMMIT,'commit',TARGET)
            raw='0x'+Account.from_key(KEY).sign_transaction(p['transaction']).raw_transaction.hex()
            r=self.ingress(p).capture(raw)
        self.assertEqual(self.db.one('SELECT checkpoint_hash FROM external_anchor_nonces')['checkpoint_hash'],COMMIT[10:74])
        self.assertEqual(r['broadcasts'],0)

    def test_two_processes_share_durable_nonce_boundary(self):
        self.ingress()
        code="""import json,sys
from backend.persistence import Database
from backend.audit_trust.metamask_ingress import SignedIngress
from backend.audit_trust.transaction_safety import plan_hash
from backend.domain import Rejected
from tests.test_sepolia_readiness import PlanRPC
p=json.load(sys.stdin)
try:
 r=SignedIngress(Database(p['db']),PlanRPC(),p['plan'],plan_hash(p['plan'])).capture(p['raw'])
 print(json.dumps({'hash':r['tx_hash'],'broadcasts':r['broadcasts']}))
except Rejected as e:print(json.dumps({'rejected':e.code}))
"""
        payload=json.dumps({'db':self.db.path,'plan':self.plan,'raw':self.raw})
        children=[subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        for c in children:c.stdin.write(payload);c.stdin.close();c.stdin=None
        outputs=[]
        for c in children:
            out,err=c.communicate(timeout=20);self.assertEqual(c.returncode,0,err);outputs.append(json.loads(out))
        self.assertTrue(any(o.get('hash') for o in outputs));self.assertEqual(len(self.db.rows('SELECT * FROM external_anchor_nonces')),1)
        self.assertTrue(all(o.get('broadcasts',0)==0 for o in outputs))

    def test_uncertain_network_nonce_does_not_reallocate_captured_transaction(self):
        g=self.ingress();record=g.capture(self.raw);self.rpc.nonce=1
        self.assertEqual(g.capture(self.raw),record);self.assertEqual(self.rpc.sent,[])
        with patch('backend.audit_trust.metamask_ingress.time.time',return_value=self.plan['quoted_at']+1000):
            self.assertEqual(g.capture(self.raw),record) # Read-back, never renewed send approval.
        tampered=self.raw[:-2]+('00' if self.raw[-2:]!='00' else '01')
        with self.assertRaises(Rejected):g.capture(tampered)


class LabE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.lab=Lab(Path(self.tmp.name)/'lab')
        # Only the intentionally disposable local fixture; never configured keys.
        secret=(self.lab.directory/'DISPOSABLE_TEST_WALLET.txt').read_text().splitlines()[-1]
        self.wallet=Account.from_key(secret)

    def test_real_local_evm_deploy_after_capture_and_duplicate(self):
        plan=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        h=self.lab.request('ingress','eth_sendRawTransaction',[raw]);receipt=self.lab.rpc.call('eth_getTransactionReceipt',[h])
        self.assertEqual(receipt['status'],'0x1');self.assertTrue(receipt['contractAddress'])
        self.assertTrue(self.lab.events[0]['persisted_before_execution'])
        self.assertEqual(self.lab.ingress.inspect()['tx_hash'],h)
        # Nonce now advanced; network inspection is used rather than a new signing request.
        self.assertEqual(self.lab.rpc.sent,[h]);self.assertEqual(len(self.lab.db.rows('SELECT * FROM external_anchor_nonces')),1)
        self.assertEqual(self.lab.request('ingress','eth_sendRawTransaction',[raw]),h)
        self.assertEqual(self.lab.rpc.sent,[h])

    def test_mined_receipt_serializes_over_http_and_publisher_reads_correctly(self):
        from eth_utils import keccak
        server=make_server(self.lab,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}/ingress/{self.lab.token}'
        def call(method,params):
            req=Request(url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
            with urlopen(req,timeout=3) as r:
                j=json.load(r);self.assertNotIn('error',j);return j['result']
        p=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(p['transaction']).raw_transaction.hex()
        h=call('eth_sendRawTransaction',[raw]);receipt=call('eth_getTransactionReceipt',[h])
        self.assertEqual(receipt['status'],'0x1');self.assertNotIn('stateRoot',receipt)
        self.assertGreater(int(receipt['gasUsed'],16),21000)
        code=call('eth_getCode',[receipt['contractAddress'],'latest']);self.assertNotEqual(code,'0x')
        publisher=call('eth_call',[{'from':self.wallet.address,'to':receipt['contractAddress'],'data':'0x'+keccak(text='publisher()')[:4].hex()},'latest'])
        self.assertEqual(publisher,'0x'+'0'*24+self.wallet.address[2:].lower())
        self.assertEqual(len(self.lab.rpc.sent),1)

    def test_simulated_response_loss_after_local_execution_no_second_execution(self):
        plan=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        call=self.lab.rpc.call
        def lose(method,params):
            result=call(method,params)
            if method=='eth_sendRawTransaction':raise TimeoutError('injected lost response')
            return result
        with patch.object(self.lab.rpc,'call',side_effect=lose):
            with self.assertRaises(TimeoutError):self.lab.request('ingress','eth_sendRawTransaction',[raw])
        captured=self.lab.ingress.inspect();self.assertEqual(len(self.lab.rpc.sent),1)
        self.assertEqual(self.lab.request('ingress','eth_sendRawTransaction',[raw]),captured['tx_hash'])
        self.assertEqual(len(self.lab.rpc.sent),1)

    def test_timeout_before_execution_stays_uncertain_without_automatic_retry(self):
        plan=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        call=self.lab.rpc.call
        def lose(method,params):
            if method=='eth_sendRawTransaction':raise TimeoutError('injected before send')
            return call(method,params)
        with patch.object(self.lab.rpc,'call',side_effect=lose):
            with self.assertRaises(TimeoutError):self.lab.request('ingress','eth_sendRawTransaction',[raw])
        self.assertEqual(self.lab.rpc.sent,[])
        with self.assertRaises(Rejected) as e:self.lab.request('ingress','eth_sendRawTransaction',[raw])
        self.assertEqual(e.exception.code,'LAB_UNCERTAIN_SEND_REQUIRES_INSPECTION')
        self.assertEqual(self.lab.rpc.sent,[])

    def test_used_evm_epoch_cannot_restart_with_fabricated_receipts(self):
        plan=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        self.lab.request('ingress','eth_sendRawTransaction',[raw])
        with self.assertRaises(Rejected) as e:Lab(self.lab.directory,resume_unused=True)
        self.assertEqual(e.exception.code,'LAB_USED_EPOCH_RESTART_DENIED')

    def test_rpc_switch_bypass_is_visible_and_not_claimed_compatible(self):
        plan=self.lab.prepare()['plan'];raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        h=self.lab.request('bypass','eth_sendRawTransaction',[raw])
        self.assertEqual(self.lab.rpc.call('eth_getTransactionReceipt',[h])['status'],'0x1')
        self.assertIsNone(self.lab.ingress.inspect());self.assertEqual(self.lab.db.rows('SELECT * FROM external_anchor_nonces'),[])
        self.assertFalse(json.loads((self.lab.directory/'lab-public.json').read_text())['production_compatible'])

    def test_unsigned_unlocked_sending_and_real_wallet_forbidden(self):
        with self.assertRaises(Rejected):self.lab.request('bypass','eth_sendTransaction',[{}])
        raw='0x'+Account.create().sign_transaction(self.lab.prepare()['plan']['transaction']).raw_transaction.hex()
        with self.assertRaises(Rejected):self.lab.request('bypass','eth_sendRawTransaction',[raw])
        self.assertEqual(self.lab.rpc.sent,[])

    def test_local_identity_is_single_use_route_bound_and_not_public_chain_id(self):
        def answer(c,route):return self.lab.request(route,'eth_call',[{'from':self.wallet.address,'to':c['to'],'data':c['nonce']},'latest'])
        c=self.lab.identity_challenge();proof=answer(c,'ingress')
        self.assertEqual(self.lab.verify_identity(c['nonce'],proof),{'verified':True,'route':'ingress'})
        with self.assertRaises(Rejected):self.lab.verify_identity(c['nonce'],proof)
        c=self.lab.identity_challenge();proof=answer(c,'bypass')
        with self.assertRaises(Rejected) as e:self.lab.verify_identity(c['nonce'],proof)
        self.assertEqual(e.exception.code,'LAB_IDENTITY_INGRESS_REQUIRED')
        c=self.lab.identity_challenge()
        with self.assertRaises(Rejected):self.lab.verify_identity(c['nonce'],'0x') # Public EOA eth_call response.
        c=self.lab.identity_challenge();answer(c,'ingress')
        with self.assertRaises(Rejected):self.lab.verify_identity(c['nonce'],'0x'+'00'*32)
        c=self.lab.identity_challenge()
        with patch('scripts.metamask_anchor_lab.time.monotonic',return_value=time.monotonic()+31):
            with self.assertRaises(Rejected):answer(c,'ingress')
        self.assertEqual(self.lab.rpc.sent,[]);self.assertIsNone(self.lab.ingress)

    def test_page_guard_via_http_with_metamask_static_client_version_and_bad_routes(self):
        import shutil
        from scripts.metamask_anchor_lab import JS
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node is required to execute the actual page guard')
        server=make_server(self.lab,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        # JS VM/provider fixture, not a claim of actual browser automation.
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
let input='';process.stdin.on('data',v=>input+=v);process.stdin.on('end',async()=>{
 const x=JSON.parse(input);let mode='ingress',versionCalls=0,writes=0;
 const provider={isMetaMask:true,on(){},async request(q){
  if(q.method==='eth_accounts')return [mode==='wallet'?'0x'+'11'.repeat(20):x.wallet];
  if(q.method==='eth_chainId')return mode==='chain'?'0x1':'0xaa36a7';
  if(q.method==='web3_clientVersion'){versionCalls++;return 'MetaMask/v13.50.0'}
  if(q.method==='eth_sendTransaction'){writes++;throw Error('No signing allowed')}
  if(mode==='public')return '0x';
  const r=await fetch(x.base+'/'+mode+'/'+x.token,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,...q})});
  const v=await r.json();if(v.error)throw Error(v.error.message);return v.result;
 }};
 const context=vm.createContext({window:{ethereum:provider},ethereum:provider,
  document:{querySelector:()=>({disabled:true,textContent:''})},
  fetch:(path,opts={})=>fetch(x.base+path,{...opts,headers:{...opts.headers,Origin:x.base}})});
 vm.runInContext(x.js,context);
 assert.equal(await vm.runInContext('guard()',context),x.wallet);
 for(mode of ['bypass','public','chain','wallet'])await assert.rejects(()=>vm.runInContext('guard()',context));
 assert.equal(versionCalls,0);assert.equal(writes,0);console.log('guard: ingress passed; static MetaMask version ignored; bypass/public/wrong-chain/wrong-wallet rejected; no signing');
}).on('error',()=>process.exit(1));
'''
        result=subprocess.run([node,'-e',harness],input=json.dumps({'js':JS.replace('__TOKEN__',self.lab.token),
                'base':base,'token':self.lab.token,'wallet':self.wallet.address}),capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('ingress passed',result.stdout)
        self.assertEqual(self.lab.rpc.sent,[])

    def test_identity_control_origin_token_capacity_and_wrong_sender(self):
        from urllib.error import HTTPError
        server=make_server(self.lab,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        for path,origin in [('/identity/challenge/'+self.lab.token,'https://attacker.example'),
                            ('/identity/challenge/wrong',base),
                            ('/identity/challenge/'+self.lab.token,'chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn')]:
            req=Request(base+path,data=b'{}',headers={'Content-Type':'application/json','Origin':origin})
            with self.assertRaises(HTTPError) as error:urlopen(req,timeout=3)
            self.assertEqual(error.exception.code,403);error.exception.close()
        c=self.lab.identity_challenge()
        with self.assertRaises(Rejected):self.lab.request('ingress','eth_call',[{'from':'0x'+'11'*20,'to':c['to'],'data':c['nonce']},'latest'])
        for _ in range(63):self.lab.identity_challenge()
        with self.assertRaises(Rejected) as error:self.lab.identity_challenge()
        self.assertEqual(error.exception.code,'LAB_IDENTITY_CHALLENGE_LIMIT')
        self.assertEqual(self.lab.rpc.sent,[])

    def test_http_host_origin_auth_size_batch_and_write_denied(self):
        server=make_server(self.lab,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        def send(path,body,headers=None):
            req=Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})})
            try:
                with urlopen(req,timeout=3) as r:return r.status,json.load(r)
            except Exception as e:
                return getattr(e,'code',None),None
        req={'jsonrpc':'2.0','id':1,'method':'eth_chainId','params':[]}
        path='/ingress/'+self.lab.token
        self.assertEqual(send(path,req)[1]['result'],'0xaa36a7')
        self.assertEqual(send('/ingress/wrong',req)[0],403)
        self.assertEqual(send(path,req,{'Origin':'https://attacker.example'})[0],403)
        self.assertEqual(send(path,req,{'Host':'attacker.example'})[0],403)
        self.assertIn('error',send(path,dict(req,method='eth_sendTransaction'))[1])
        self.assertIn('error',send(path,[req])[1])
        self.assertIn('error',send(path,dict(req,params=['x'*140000]))[1])
        self.assertEqual(send('/control/'+self.lab.token,{}, {'Origin':'https://attacker.example'})[0],403)
        self.assertEqual(self.lab.rpc.sent,[])

    def test_metamask_preflight_and_parameterless_reads(self):
        server=make_server(self.lab,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        origin='chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn'
        for route in ('ingress','bypass'):
            url=base+'/'+route+'/'+self.lab.token
            request=Request(url,method='OPTIONS',headers={'Origin':origin,
                'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type',
                'Access-Control-Request-Private-Network':'true'})
            with urlopen(request,timeout=3) as r:
                self.assertEqual(r.status,204);self.assertEqual(r.headers['Access-Control-Allow-Origin'],origin)
                self.assertIn('POST',r.headers['Access-Control-Allow-Methods'])
                self.assertEqual(r.headers['Access-Control-Allow-Private-Network'],'true')
            for method,expected in [('eth_chainId','0xaa36a7'),('net_version','11155111'),('eth_blockNumber','0x1')]:
                request=Request(url,data=json.dumps({'jsonrpc':'2.0','id':42,'method':method}).encode(),
                                headers={'Origin':origin,'Content-Type':'application/json'})
                with urlopen(request,timeout=3) as r:
                    self.assertEqual(r.headers['Access-Control-Allow-Origin'],origin)
                    self.assertEqual(json.load(r),{'jsonrpc':'2.0','id':42,'result':expected})
        self.assertEqual(self.lab.rpc.sent,[])
        self.assertIsNone(self.lab.ingress)

    def test_cors_never_allows_arbitrary_origin_path_headers_or_writes(self):
        from urllib.error import HTTPError
        server=make_server(self.lab,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        origin='chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn'
        path='/ingress/'+self.lab.token
        cases=[(path,'https://attacker.example','POST','content-type'),
               (path,'chrome-extension://other','POST','content-type'),
               ('/ingress/wrong',origin,'POST','content-type'),
               ('/control/'+self.lab.token,origin,'POST','content-type'),
               (path,origin,'DELETE','content-type'),(path,origin,'POST','authorization')]
        for target,source,verb,headers in cases:
            with self.subTest(target=target[:10],source=source,verb=verb):
                req=Request(base+target,method='OPTIONS',headers={'Origin':source,
                    'Access-Control-Request-Method':verb,'Access-Control-Request-Headers':headers})
                with self.assertRaises(HTTPError) as error:urlopen(req,timeout=3)
                self.assertEqual(error.exception.code,403)
                if source!=origin:self.assertIsNone(error.exception.headers.get('Access-Control-Allow-Origin'))
                error.exception.close()
        for method in ('eth_sendRawTransaction','eth_sendTransaction','eth_getBalance'):
            req=Request(base+path,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method}).encode(),
                        headers={'Origin':origin,'Content-Type':'application/json'})
            with urlopen(req,timeout=3) as r:self.assertIn('error',json.load(r))
        self.assertEqual(self.lab.rpc.sent,[])
        log=(self.lab.directory/'rpc-events.json').read_text()
        self.assertNotIn(self.lab.token,log)
        self.assertIn('PREFLIGHT_DENIED',log)


if __name__=='__main__':unittest.main()
