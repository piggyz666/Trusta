"""Anchor-only MetaMask loopback ingress. No wallet secrets, no automatic approval.

prepare is read-only. serve requires an independently approved exact fingerprint
and cannot reprice, renew, or replace it. A separate process/plan is needed for
commit. The isolated lab and its used EVM are never restarted by this script.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from backend.domain import Rejected, canonical, digest
from backend.persistence import Database
from backend.security.local_files import protect_secret, write_secret_atomic
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.metamask_ingress import SignedIngress
from backend.audit_trust.public_ingress import PublicDelivery, expected_runtime
from backend.audit_trust.transaction_safety import require_sepolia, prepare_transaction, plan_hash, load_anchor_artifact
from backend.audit_trust.records import verify_record
from scripts.estimate_sepolia_costs import load_local_environment

WALLET='0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE'
READS={'eth_chainId','eth_getCode','eth_getBalance','eth_getTransactionCount','eth_gasPrice',
    'eth_maxPriorityFeePerGas','eth_feeHistory','eth_estimateGas','eth_call','eth_getTransactionByHash',
    'eth_getTransactionReceipt','eth_getBlockByNumber','eth_blockNumber'}


def commit_data(bundle, pinned_key):
    from eth_utils import keccak
    if not bundle.get('receipt') or not verify_record(bundle,pinned_key):
        raise Rejected('ANCHOR_BUSINESS_RECEIPT_PROOF_REQUIRED')
    cp=bundle['checkpoint']
    identifier=hashlib.sha256(cp['checkpoint_id'].encode()).hexdigest()
    return ('0x'+keccak(text='commit(bytes32,bytes32,bytes32,uint64,uint32)')[:4].hex()
        +digest(cp)+cp['merkle_root']+identifier+f"{cp['last_event_sequence']:064x}"+f'{1:064x}')


def validate_plan_intent(plan, bundle=None, pinned_key=None):
    if plan['sender'].lower()!=WALLET.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    if plan['kind']=='deploy':
        if plan['transaction']['data']!='0x'+load_anchor_artifact()['bin']:
            raise Rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH')
    elif plan['kind']=='commit':
        if not bundle or not pinned_key or plan['transaction']['data']!=commit_data(bundle,pinned_key):
            raise Rejected('ANCHOR_CHECKPOINT_BINDING_INVALID')
    else:raise Rejected('ANCHOR_TRANSACTION_KIND_INVALID')


def quote(rpc, kind, db_path, bundle, pinned, contract):
    if kind=='deploy':return prepare_transaction(rpc,WALLET,'0x'+load_anchor_artifact()['bin'],'deploy')
    from eth_utils import keccak
    from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
    from backend.security.extensions import AnchorCommitment
    data=commit_data(bundle,pinned)
    os.environ['ANCHOR_CONTRACT_CODE_HASH']='0x'+keccak(hexstr=expected_runtime(WALLET)).hex()
    adapter=ExternalBlockchainAnchor(Database(db_path),rpc,contract)
    cp=bundle['checkpoint']
    result=adapter.prepare_commit(AnchorCommitment(cp['merkle_root']),digest(cp))
    if result['transaction']['data']!=data:raise Rejected('ANCHOR_CHECKPOINT_BINDING_INVALID')
    return result


class Gateway:
    def __init__(self, delivery, directory):
        self.delivery=delivery;self.plan=delivery.ingress.plan;self.wallet=self.plan['sender']
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);protect_secret(self.directory)
        self.token=secrets.token_hex(32);self.key=secrets.token_bytes(32)
        self.identity_address='0x'+secrets.token_hex(20);self.challenges={}
        self.lock=threading.RLock()
        self.port=None

    def network(self):
        if self.port is None:raise Rejected('ANCHOR_SESSION_NOT_BOUND')
        return {'chainId':'0xaa36a7','chainName':'TRUSTA Sepolia '+hashlib.sha256(self.token.encode()).hexdigest()[:8],
            'rpcUrls':[f'http://127.0.0.1:{self.port}/rpc/{self.token}'],
            # MetaMask's built-in test-network ticker is SepoliaETH, not ETH.
            'nativeCurrency':{'name':'Sepolia Ether','symbol':'SepoliaETH','decimals':18},
            'blockExplorerUrls':['https://sepolia.etherscan.io']}

    def event(self, fields):
        # No RPC URL, route token, raw transaction, signature or wallet secrets.
        with self.lock:
            path=self.directory/'route-events.jsonl'
            if path.exists() and path.stat().st_size>=8*1024*1024:return
            with path.open('a',encoding='utf-8') as stream:
                stream.write(canonical({'time':int(time.time()),**fields})+'\n')

    def info(self):
        approved=(os.getenv('ANCHOR_ALLOW_SIGNING')=='true' and os.getenv('ANCHOR_ALLOW_BROADCAST')=='true'
            and os.getenv('ANCHOR_APPROVED_PLAN_HASH')==plan_hash(self.plan))
        return {'plan':self.plan,'plan_hash':plan_hash(self.plan),'scope':'ETHEREUM_SEPOLIA_PUBLIC','approved':approved,
            'metamask_network':self.network()}

    def challenge(self):
        with self.lock:
            self.challenges={n:v for n,v in self.challenges.items() if time.monotonic()<v['expiry']}
            if len(self.challenges)>=64:raise Rejected('ANCHOR_CHALLENGE_LIMIT')
            nonce='0x'+secrets.token_hex(32)
            self.challenges[nonce]={'expiry':time.monotonic()+30}
            return {'nonce':nonce,'to':self.identity_address,'wallet':self.wallet}

    def verify_route(self, body):
        with self.lock:
            if not isinstance(body,dict) or set(body)!={'nonce','proof'}:raise Rejected('ANCHOR_ROUTE_INVALID')
            item=self.challenges.pop(body['nonce'],None)
            if (not item or time.monotonic()>=item['expiry'] or not item.get('observed')
                    or not isinstance(body['proof'],str) or not hmac.compare_digest(item['proof'],body['proof'])):
                self.event({'event':'route_rejected','observed_at_ingress':bool(item and item.get('observed')),
                    'expired':bool(item and time.monotonic()>=item['expiry']),
                    'challenge_known':item is not None,'proof_is_empty':body.get('proof')=='0x'})
                raise Rejected('ANCHOR_ROUTE_UNVERIFIED')
            self.event({'event':'route_verified','plan_hash':plan_hash(self.plan)})
            return {'verified':True,'scope':'ETHEREUM_SEPOLIA_PUBLIC','plan_hash':plan_hash(self.plan)}

    def request(self, method, params):
        if not isinstance(params,list):raise Rejected('RPC_REQUEST_INVALID')
        self.event({'event':'ingress_request','method':method[:128]})
        if method=='eth_call' and params and isinstance(params[0],dict) and params[0].get('to','').lower()==self.identity_address.lower():
            with self.lock:
                tx=params[0];nonce=tx.get('data');item=self.challenges.get(nonce)
                if not item or time.monotonic()>=item['expiry'] or tx.get('from','').lower()!=self.wallet.lower():
                    raise Rejected('ANCHOR_ROUTE_INVALID')
                proof='0x'+hmac.new(self.key,(nonce+self.wallet.lower()).encode(),hashlib.sha256).hexdigest()
                item.update(observed=True,proof=proof);return proof
        if method=='eth_sendRawTransaction':
            if len(params)!=1:raise Rejected('RPC_REQUEST_INVALID')
            return self.delivery.send(params[0])
        if method=='net_version' and not params:
            require_sepolia(self.delivery.rpc);return '11155111'
        if method=='web3_clientVersion' and not params:return 'TRUSTA-SEPOLIA-EXACT-PLAN-INGRESS'
        if method not in READS:raise Rejected('ANCHOR_RPC_METHOD_FORBIDDEN')
        return self.delivery.rpc.call(method,params)


PAGE='''<!doctype html><meta charset="utf-8"><title>TRUSTA Sepolia Operator</title>
<h1>TRUSTA — 公共 Sepolia 逐笔操作</h1><p>这会消耗测试 ETH。只有已批准的准确计划能通过网关。</p>
<button id="connect">检查钱包与受控 RPC</button><button id="send" disabled>签署已批准交易</button><pre id="out"></pre><script src="/operator.js"></script>'''
JS=r'''
const out=document.querySelector('#out'),send=document.querySelector('#send');let ready=false;
function wallet(){const injected=window.ethereum;const candidates=injected?.providers?.filter(p=>p.isMetaMask)??(injected?.isMetaMask?[injected]:[]);
 if(candidates.length!==1)throw Error('METAMASK_PROVIDER_AMBIGUOUS');return candidates[0]}
let provider;
const rpc=(method,params=[])=>provider.request({method,params});
async function control(path,body={}){const r=await fetch(path+'/__TOKEN__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error);return v}
async function setupNetwork(){provider=wallet();const info=await control('/plan');
 // Existing Sepolia must retain the wallet's native currency metadata. This
 // extension rejects both its long built-in ticker and a different short one
 // through wallet_addEthereumChain. Do not retry conflicting registrations.
 if(await rpc('eth_chainId')!==info.metamask_network.chainId){
  out.textContent='请在 MetaMask 确认切换已有 Sepolia 网络。不会签名或广播交易。';
  await rpc('wallet_switchEthereumChain',[{chainId:info.metamask_network.chainId}]);
 }
}
async function guard(){if(!provider)throw Error('METAMASK_REQUIRED');
 const accounts=await rpc('eth_requestAccounts');const info=await control('/plan');
 if(accounts[0]?.toLowerCase()!==info.plan.sender.toLowerCase())throw Error('WRONG_WALLET');
 if(await rpc('eth_chainId')!=='0xaa36a7')throw Error('WRONG_CHAIN');
 const c=await control('/challenge');const proof=await rpc('eth_call',[{from:accounts[0],to:c.to,data:c.nonce},'latest']);
 await control('/verify-route',{nonce:c.nonce,proof});
 if((await rpc('eth_accounts'))[0]?.toLowerCase()!==info.plan.sender.toLowerCase() || await rpc('eth_chainId')!=='0xaa36a7')throw Error('WALLET_CHANGED');
 return info;
}
document.querySelector('#connect').onclick=async()=>{ready=false;send.disabled=true;try{await setupNetwork();const i=await guard();ready=true;send.disabled=!i.approved||Date.now()/1000-i.plan.quoted_at>i.plan.quote_ttl;out.textContent=JSON.stringify(i,null,2)}catch(e){ready=false;send.disabled=true;out.textContent=e.message;
 if(e.message==='ANCHOR_ROUTE_UNVERIFIED'){try{const i=await control('/plan');out.textContent+='\n当前钱包请求未通过本次 ingress 身份验证。请在 MetaMask 已有 Sepolia 网络中选择以下活动 RPC（不要新建网络或更改货币符号）：\n'+i.metamask_network.rpcUrls[0]}catch{}}
}};
send.onclick=async()=>{send.disabled=true;try{if(!ready)throw Error('ROUTE_NOT_CHECKED');const i=await guard();
 if(!i.approved)throw Error('EXACT_PLAN_APPROVAL_REQUIRED');
 const p=i.plan;if(Date.now()/1000-p.quoted_at>p.quote_ttl)throw Error('EXACT_PLAN_EXPIRED');
 const tx={from:p.sender};for(const [k,v] of Object.entries(p.transaction))tx[k]=typeof v==='number'?'0x'+v.toString(16):v;
 const hash=await rpc('eth_sendTransaction',[tx]);out.textContent=JSON.stringify({transaction_hash:hash,plan_hash:i.plan_hash,scope:'ETHEREUM_SEPOLIA_PUBLIC',acceptance:'PENDING_INDEPENDENT_VERIFICATION'},null,2);
 }catch(e){out.textContent=e.message}}
'''


def make_server(gateway, port=18766):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def setup(self):super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass
        def allowed(self):
            return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and self.headers.get('Origin') in (
                None,f'http://127.0.0.1:{self.server.server_port}','chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn')
        def route(self):
            for route in ('rpc','plan','challenge','verify-route'):
                if hmac.compare_digest(self.path,'/'+route+'/'+gateway.token):return route
            return None
        def reply(self,status,body,content='application/json'):
            encoded=body.encode() if isinstance(body,str) else canonical(body).encode()
            self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(encoded)))
            self.send_header('Connection','close');self.close_connection=True;self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            if self.allowed() and self.headers.get('Origin'):
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin']);self.send_header('Vary','Origin')
            self.end_headers();self.wfile.write(encoded)
        def do_GET(self):
            if not self.allowed():return self.reply(403,{'error':'HOST_OR_ORIGIN_DENIED'})
            if self.path=='/':return self.reply(200,PAGE,'text/html; charset=utf-8')
            if self.path=='/operator.js':return self.reply(200,JS.replace('__TOKEN__',gateway.token),'application/javascript')
            return self.reply(404,{'error':'NOT_FOUND'})
        def do_OPTIONS(self):
            requested={v.strip().lower() for v in self.headers.get('Access-Control-Request-Headers','').split(',') if v.strip()}
            if (not self.allowed() or self.route()!='rpc' or not self.headers.get('Origin')
                    or self.headers.get('Access-Control-Request-Method')!='POST' or not requested<={'content-type'}
                    or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') not in (None,'0')):
                return self.reply(403,{'error':'PREFLIGHT_DENIED'})
            self.send_response(204);self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods','POST, OPTIONS');self.send_header('Access-Control-Allow-Headers','Content-Type')
            self.send_header('Vary','Origin, Access-Control-Request-Headers')
            if self.headers.get('Access-Control-Request-Private-Network')=='true':self.send_header('Access-Control-Allow-Private-Network','true')
            self.send_header('Content-Length','0');self.send_header('Connection','close');self.close_connection=True;self.end_headers()
        def do_POST(self):
            request_id=None;route=self.route()
            if (not self.allowed() or not route or self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Length',[]))!=1 or self.headers.get_content_type()!='application/json'
                    or route!='rpc' and self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}'):
                return self.reply(403,{'error':'INGRESS_DENIED'})
            try:
                from backend.llm.validation import strict_json
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=(131072 if route=='rpc' else 4096):raise Rejected('RPC_REQUEST_LIMIT')
                raw=self.rfile.read(length)
                if len(raw)!=length:raise Rejected('RPC_REQUEST_INVALID')
                body=strict_json(raw.decode())
                if route=='rpc':
                    if not isinstance(body,dict) or body.get('jsonrpc')!='2.0' or not isinstance(body.get('method'),str):
                        raise Rejected('RPC_REQUEST_INVALID')
                    request_id=body.get('id')
                    if request_id is not None and (type(request_id) not in (str,int) or isinstance(request_id,str) and len(request_id)>128):
                        raise Rejected('RPC_REQUEST_INVALID')
                    result=gateway.request(body['method'],body.get('params',[]))
                    return self.reply(200,{'jsonrpc':'2.0','id':request_id,'result':result})
                if route=='verify-route':result=gateway.verify_route(body)
                elif body!={}:raise Rejected('RPC_REQUEST_INVALID')
                elif route=='challenge':result=gateway.challenge()
                else:result=gateway.info()
                return self.reply(200,result)
            except Exception as exc:
                reason=exc.code if isinstance(exc,Rejected) else 'RPC_REQUEST_INVALID'
                if route=='rpc':return self.reply(200,{'jsonrpc':'2.0','id':request_id,'error':{'code':-32000,'message':reason}})
                return self.reply(403,{'error':reason})
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(16);super().__init__(*args,**kwargs)
        def process_request(self,request,address):
            if not self.slots.acquire(False):self.shutdown_request(request);return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
        def verify_request(self,request,address):return address[0]=='127.0.0.1'
    server=Server(('127.0.0.1',port),Handler)
    gateway.port=server.server_port
    return server


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=('prepare','serve','operator'))
    parser.add_argument('--kind',choices=('deploy','commit'),default='deploy')
    parser.add_argument('--directory',type=Path,default=Path('data/sepolia-public-acceptance'))
    parser.add_argument('--plan',type=Path);parser.add_argument('--approved-plan-hash')
    parser.add_argument('--bundle',type=Path);parser.add_argument('--public-key',type=Path);parser.add_argument('--contract')
    parser.add_argument('--db',type=Path)
    parser.add_argument('--open-page',action='store_true')
    parser.add_argument('--reuse-rpc-session',action='store_true',help='Reuse only the loopback RPC address; never reuse transaction approval')
    parser.add_argument('--port',type=int,default=18766);args=parser.parse_args()
    try:
        import sys
        if args.operation=='operator' and not sys.stdin.isatty():raise Rejected('ANCHOR_OPERATOR_TERMINAL_REQUIRED')
        load_local_environment();rpc=EthereumRPC(os.environ['ANCHOR_RPC_URL'],timeout=15);require_sepolia(rpc)
        secondary=EthereumRPC(os.environ['ANCHOR_VERIFY_RPC_URL'],timeout=15);require_sepolia(secondary)
        from urllib.parse import urlsplit
        if urlsplit(rpc.url).hostname==urlsplit(secondary.url).hostname:raise Rejected('ANCHOR_RPC_INDEPENDENCE_REQUIRED')
        if os.getenv('ANCHOR_WALLET_ADDRESS','').lower()!=WALLET.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
        bundle=json.loads(args.bundle.read_text(encoding='utf-8')) if args.bundle else None
        pinned=args.public_key.read_text(encoding='utf-8') if args.public_key else None
        db_path=args.db or Path(os.getenv('TRUSTA_DB','data/trusta.sqlite3'))
        if args.operation=='operator':
            import sys
            if not sys.stdin.isatty():raise Rejected('ANCHOR_OPERATOR_TERMINAL_REQUIRED')
            # Start discovery first, unapproved: operator can check its custom
            # network before the 120-second exact transaction clock starts.
            os.environ.update(ANCHOR_ALLOW_SIGNING='false',ANCHOR_ALLOW_BROADCAST='false',ANCHOR_APPROVED_PLAN_HASH='')
            data='0x'+load_anchor_artifact()['bin'] if args.kind=='deploy' else commit_data(bundle,pinned)
            if args.kind=='commit':
                from eth_utils import keccak
                os.environ['ANCHOR_CONTRACT_CODE_HASH']='0x'+keccak(hexstr=expected_runtime(WALLET)).hex()
            plan=quote(rpc,args.kind,db_path,bundle,pinned,args.contract);validate_plan_intent(plan,bundle,pinned)
            db=Database(db_path)
            delivery=PublicDelivery(SignedIngress(db,rpc,plan,plan_hash(plan)))
            gateway=Gateway(delivery,args.directory);server=make_server(gateway,args.port)
            if args.reuse_rpc_session:
                previous_path=args.directory/'public-operator-session.json'
                if previous_path.exists():
                    previous=json.loads(previous_path.read_text(encoding='utf-8'))
                    token=previous.get('ingress_rpc','').rsplit('/',1)[-1]
                    expected_page=f'http://127.0.0.1:{server.server_port}/'
                    if (previous.get('scope')!='ETHEREUM_SEPOLIA_PUBLIC' or previous.get('wallet','').lower()!=WALLET.lower()
                            or previous.get('page')!=expected_page or len(token)!=64
                            or any(c not in '0123456789abcdef' for c in token)
                            or previous.get('ingress_rpc')!=expected_page+'rpc/'+token
                            or previous.get('bypass_route') is not False):
                        server.server_close();raise Rejected('ANCHOR_RPC_SESSION_BINDING_INVALID')
                    # New identity challenge key and unapproved quote; only the
                    # already-selected loopback URL survives the restart.
                    gateway.token=token
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            session={'scope':'ETHEREUM_SEPOLIA_PUBLIC','page':f'http://127.0.0.1:{server.server_port}/',
                'ingress_rpc':f'http://127.0.0.1:{server.server_port}/rpc/{gateway.token}','wallet':WALLET,
                'approved':False,'production_compatible':False,'bypass_route':False,'metamask_network':gateway.network()}
            write_secret_atomic(args.directory/'public-operator-session.json',canonical(session).encode())
            if args.open_page:
                import webbrowser
                webbrowser.open(session['page'])
            print('Use public-operator-session.json in your own terminal. Do not use the disposable lab wallet.\n'
                  'Disable wallet Smart Transactions, select this custom Sepolia RPC, and check wallet/route on the page.\n'
                  'This preliminary connection grants no signing or spending permission.',flush=True)
            try:
                if input('Type PREPARE after wallet/network readiness (no secrets): ').strip()!='PREPARE':
                    raise Rejected('ANCHOR_OPERATOR_CANCELLED')
                plan=quote(rpc,args.kind,db_path,bundle,pinned,args.contract);validate_plan_intent(plan,bundle,pinned)
                target=args.plan or args.directory/(args.kind+'-exact-plan.json')
                write_secret_atomic(target,(canonical(plan)+'\n').encode())
                fingerprint=plan_hash(plan)
                review={'plan_hash':fingerprint,'plan':plan,'calldata_sha256':hashlib.sha256(bytes.fromhex(data[2:])).hexdigest(),
                    'scope':'ETHEREUM_SEPOLIA_PUBLIC','approval_expires_at':plan['quoted_at']+plan['quote_ttl']}
                print(json.dumps(review,indent=2),flush=True)
                typed=input('Approve ONLY these exact fields by typing the entire plan_hash (no wallet secrets): ').strip()
                if typed!=fingerprint:raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
                if not 0<=time.time()-plan['quoted_at']<=plan['quote_ttl']<=120:raise Rejected('ANCHOR_QUOTE_EXPIRED')
                fresh=quote(rpc,args.kind,db_path,bundle,pinned,args.contract)
                if (fresh['transaction']['nonce']!=plan['transaction']['nonce'] or fresh['transaction']['gas']>plan['transaction']['gas']
                        or fresh['transaction']['maxFeePerGas']>plan['transaction']['maxFeePerGas']):raise Rejected('ANCHOR_QUOTE_CHANGED')
                delivery=PublicDelivery(SignedIngress(db,rpc,plan,fingerprint))
                write_secret_atomic(args.directory/(args.kind+'-operator-approval.json'),canonical({
                    'plan_hash':fingerprint,'approved_at':int(time.time()),'method':'LOCAL_OPERATOR_TYPED_EXACT_HASH',
                    'transaction_kind':args.kind,'applies_to_other_transactions':False}).encode())
                # No stored key, hidden input, autonomous signing or fee renewal.
                with gateway.lock:
                    gateway.delivery=delivery;gateway.plan=plan
                    os.environ.update(ANCHOR_ALLOW_SIGNING='true',ANCHOR_ALLOW_BROADCAST='true',ANCHOR_APPROVED_PLAN_HASH=fingerprint)
                    session.update(approved=True,plan_hash=fingerprint)
                    write_secret_atomic(args.directory/'public-operator-session.json',canonical(session).encode())
                print('Exact plan activated. Recheck the page and approve the unchanged transaction in MetaMask within its remaining lifetime.\n'
                      'If MetaMask rewrites fields, the gateway rejects them; do not retry against another RPC.',flush=True)
                while True:time.sleep(1)
            finally:
                os.environ.update(ANCHOR_ALLOW_SIGNING='false',ANCHOR_ALLOW_BROADCAST='false',ANCHOR_APPROVED_PLAN_HASH='')
                server.shutdown();server.server_close()
        if args.operation=='prepare':
            data='0x'+load_anchor_artifact()['bin'] if args.kind=='deploy' else commit_data(bundle,pinned)
            if args.kind=='commit':
                from eth_utils import keccak
                os.environ['ANCHOR_CONTRACT_CODE_HASH']='0x'+keccak(hexstr=expected_runtime(WALLET)).hex()
            plan=quote(rpc,args.kind,db_path,bundle,pinned,args.contract)
            validate_plan_intent(plan,bundle,pinned)
            if not args.plan:raise Rejected('ANCHOR_PLAN_PATH_REQUIRED')
            args.plan.parent.mkdir(parents=True,exist_ok=True)
            write_secret_atomic(args.plan,(canonical(plan)+'\n').encode())
            print(json.dumps({'scope':'ETHEREUM_SEPOLIA_PUBLIC','plan_hash':plan_hash(plan),
                'plan':str(args.plan),'transaction':{k:v for k,v in plan['transaction'].items() if k!='data'},
                'calldata_sha256':hashlib.sha256(bytes.fromhex(data[2:])).hexdigest(),
                'worst_cost_wei':plan['worst_cost_wei'],'quote_ttl':plan['quote_ttl'],'broadcasts':0}))
            return
        if not args.plan or not args.approved_plan_hash:raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
        plan=json.loads(args.plan.read_text(encoding='utf-8'));validate_plan_intent(plan,bundle,pinned)
        if plan_hash(plan)!=args.approved_plan_hash:raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
        if not 0<=time.time()-plan['quoted_at']<=plan['quote_ttl']<=120:raise Rejected('ANCHOR_QUOTE_EXPIRED')
        if plan['kind']=='commit':
            from eth_utils import keccak
            os.environ['ANCHOR_CONTRACT_CODE_HASH']='0x'+keccak(hexstr=expected_runtime(WALLET)).hex()
        # Exact human approval only; these settings are confined to this process.
        os.environ.update(ANCHOR_ALLOW_SIGNING='true',ANCHOR_ALLOW_BROADCAST='true',ANCHOR_APPROVED_PLAN_HASH=args.approved_plan_hash)
        # Same default/env database as deploy_audit_anchor and the AnchorWorker.
        delivery=PublicDelivery(SignedIngress(Database(db_path),rpc,plan,args.approved_plan_hash))
        gateway=Gateway(delivery,args.directory);server=make_server(gateway,args.port)
        session={'scope':'ETHEREUM_SEPOLIA_PUBLIC','page':f'http://127.0.0.1:{server.server_port}/',
            'ingress_rpc':f'http://127.0.0.1:{server.server_port}/rpc/{gateway.token}',
            'wallet':WALLET,'plan_hash':args.approved_plan_hash,'production_compatible':False,
            'approved':True,'bypass_route':False,'metamask_network':gateway.network(),
            'note':'Compatibility/acceptance require actual receipt and independent verification; no bypass route.'}
        write_secret_atomic(args.directory/'public-operator-session.json',canonical(session).encode())
        print(json.dumps({k:v for k,v in session.items() if k!='ingress_rpc'}),flush=True)
        server.serve_forever()
    except Exception as exc:
        print(json.dumps({'error':exc.code if isinstance(exc,Rejected) else 'ANCHOR_OPERATOR_CONFIGURATION_INVALID','broadcasts_not_assumed':True}))
        raise SystemExit(1) from None

if __name__=='__main__':main()
