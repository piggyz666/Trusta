"""Actual extension lab: isolated PyEVM only, no external RPC or .env loading.

Two loopback routes demonstrate both controlled ingress and wallet RPC bypass.
NEVER run against an existing wallet/profile. No production activation switch.
"""
import argparse
import json
import secrets
import threading
import time
import hashlib
import hmac
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from eth_account import Account
from eth_tester import EthereumTester, PyEVMBackend
from eth_utils import keccak
from backend.domain import Rejected
from backend.persistence import Database
from backend.security.local_files import protect_secret, write_secret_atomic
from backend.audit_trust.transaction_safety import load_anchor_artifact, prepare_transaction, plan_hash
from backend.audit_trust.metamask_ingress import SignedIngress

REAL_WALLET = '0xf84c2e84f43460a94bb24ffeb6d4a61b75de86be'


def rpc_object(value):
    """eth-tester snake-case public output to Ethereum RPC wire format."""
    if isinstance(value, (bytes, bytearray)): return '0x'+bytes(value).hex()
    if isinstance(value, (list, tuple)): return [rpc_object(v) for v in value]
    if not isinstance(value, dict): return value
    result = {}
    for key, item in value.items():
        # eth-tester exposes a legacy byte status in state_root even for typed,
        # post-Byzantium receipts. RPC uses status, not that non-root sentinel.
        if key == 'state_root' and 'status' in value: continue
        name = key.split('_')[0]+''.join(w.title() for w in key.split('_')[1:])
        if type(item) is int: item = hex(item)
        result[name] = rpc_object(item)
    return result


class LocalEVM:
    local_simulation_only = True

    def __init__(self, wallet):
        if wallet.address.lower() == REAL_WALLET: raise Rejected('LAB_REAL_WALLET_DENIED')
        self.wallet = wallet.address
        backend = PyEVMBackend(genesis_parameters={'base_fee_per_gas': 1, 'gas_limit': 30_000_000})
        backend.chain.chain_id = 11155111
        self.evm = EthereumTester(backend)
        self.evm.send_transaction({'from': self.evm.get_accounts()[0], 'to': wallet.address,
                                  'gas': 21000, 'value': 10**20, 'max_fee_per_gas': 2,
                                  'max_priority_fee_per_gas': 1})
        self.sent = []
        self.lock = threading.RLock()

    def tx(self, incoming):
        names = {'from': 'from', 'to': 'to', 'data': 'data', 'value': 'value', 'gas': 'gas',
                 'gasPrice': 'gas_price', 'maxFeePerGas': 'max_fee_per_gas',
                 'maxPriorityFeePerGas': 'max_priority_fee_per_gas'}
        return {v: (int(incoming[k], 16) if k not in {'from', 'to', 'data'} else incoming[k])
                for k, v in names.items() if k in incoming and incoming[k] is not None}

    def call(self, method, params):
        with self.lock:
            if method == 'eth_chainId': return '0xaa36a7'
            if method == 'net_version': return '11155111'
            if method == 'web3_clientVersion': return 'TRUSTA-LOCAL-EVM-NO-EXTERNAL-RPC'
            if method == 'eth_blockNumber': return hex(self.evm.get_block_by_number('latest')['number'])
            if method == 'eth_getBlockByNumber':
                tag = params[0]; tag = int(tag, 16) if tag.startswith('0x') else tag
                return rpc_object(self.evm.get_block_by_number(tag, full_transactions=bool(params[1])))
            if method == 'eth_getBalance': return hex(self.evm.get_balance(params[0]))
            if method == 'eth_getTransactionCount': return hex(self.evm.get_nonce(params[0]))
            if method == 'eth_getCode': return self.evm.get_code(params[0])
            if method == 'eth_gasPrice': return hex(1_000_002)
            if method == 'eth_maxPriorityFeePerGas': return hex(1_000_000)
            if method == 'eth_feeHistory':
                count = min(int(params[0], 16), 20)
                return {'oldestBlock': '0x0', 'baseFeePerGas': ['0x1']*(count+1),
                        'gasUsedRatio': [0.0]*count, 'reward': [['0xf4240']*len(params[2]) for _ in range(count)]}
            if method == 'eth_estimateGas': return hex(self.evm.estimate_gas(self.tx(params[0])))
            if method == 'eth_call': return self.evm.call(self.tx(params[0]))
            if method == 'eth_getTransactionReceipt':
                try: return rpc_object(self.evm.get_transaction_receipt(params[0]))
                except Exception: return None
            if method == 'eth_getTransactionByHash':
                try: return rpc_object(self.evm.get_transaction_by_hash(params[0]))
                except Exception: return None
            if method == 'eth_sendRawTransaction':
                recovered = Account.recover_transaction(params[0])
                if recovered.lower() != self.wallet.lower(): raise Rejected('LAB_REAL_WALLET_DENIED')
                result = self.evm.send_raw_transaction(params[0]); self.sent.append(result); return result
            raise Rejected('ANCHOR_RPC_METHOD_FORBIDDEN')


class Lab:
    """Only ephemeral laboratory signing is permitted, never the real wallet."""
    def __init__(self, directory, resume_unused=False):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        protect_secret(self.directory)
        exists = (self.directory/'lab-public.json').exists()
        if exists:
            # Only an UNEXECUTED test epoch can resume. Never reset a used
            # simulator and present its old receipts as canonical evidence.
            if not resume_unused: raise Rejected('LAB_EXISTING_EPOCH_REQUIRES_NEW_DIRECTORY')
            info=json.loads((self.directory/'lab-public.json').read_text())
            previous=Database(self.directory/'ingress.sqlite3')
            events=json.loads((self.directory/'rpc-events.json').read_text()) if (self.directory/'rpc-events.json').exists() else []
            has_reservations=(previous.one("SELECT name FROM sqlite_master WHERE name='external_anchor_nonces'")
                              and previous.rows('SELECT * FROM external_anchor_nonces'))
            if (info['scope']!='LOCAL_PYEVM_NOT_REAL_SEPOLIA' or info['real_wallet_used']
                    or has_reservations
                    or any(e.get('attempted') or e.get('method')=='eth_sendRawTransaction' for e in events)):
                raise Rejected('LAB_USED_EPOCH_RESTART_DENIED')
            fixture=(self.directory/'DISPOSABLE_TEST_WALLET.txt').read_text().splitlines()
            if fixture[0]!='LOCAL PY-EVM ONLY. NEVER USE OR FUND ON ANY PUBLIC NETWORK.':raise Rejected('LAB_FIXTURE_INVALID')
            wallet=Account.from_key(fixture[-1])
            if wallet.address!=info['test_wallet']:raise Rejected('LAB_FIXTURE_INVALID')
            self.token=json.loads((self.directory/'operator-session.json').read_text())['ingress_rpc'].rsplit('/',1)[-1]
        else:
            self.token = secrets.token_hex(32)
            Account.enable_unaudited_hdwallet_features()
            wallet, mnemonic = Account.create_with_mnemonic()
        self.rpc = LocalEVM(wallet)
        # Disposable fixture for operator import in a NEW isolated profile only.
        if not exists:
            write_secret_atomic(self.directory/'DISPOSABLE_TEST_WALLET.txt',
                ('LOCAL PY-EVM ONLY. NEVER USE OR FUND ON ANY PUBLIC NETWORK.\n'
                 +wallet.address+'\nDISPOSABLE MNEMONIC:\n'+mnemonic+'\nDISPOSABLE PRIVATE KEY:\n'+wallet.key.hex()+'\n').encode())
        self.wallet = wallet.address
        self.db = Database(self.directory/'ingress.sqlite3')
        self.ingress = None; self.events = events if exists else []; self.lock = threading.RLock()
        # Laboratory identity only; never a wallet key or a signing authority.
        self.identity_key = secrets.token_bytes(32)
        self.identity_address = '0x'+secrets.token_hex(20)
        self.identity_challenges = {}
        self.directory.joinpath('lab-public.json').write_text(json.dumps({
            'scope': 'LOCAL_PYEVM_NOT_REAL_SEPOLIA', 'test_wallet': self.wallet,
            'real_wallet_used': False, 'external_rpc_calls': 0,
            'production_compatible': False}), encoding='utf-8')

    def prepare(self):
        with self.lock:
            plan = prepare_transaction(self.rpc, self.wallet, '0x'+load_anchor_artifact()['bin'], 'deploy')
            self.ingress = SignedIngress(self.db, self.rpc, plan, plan_hash(plan))
            return {'plan': plan, 'plan_hash': plan_hash(plan), 'wallet': self.wallet}

    def request(self, route, method, params):
        if (method == 'eth_call' and params and isinstance(params[0], dict)
                and params[0].get('to', '').lower() == self.identity_address.lower()):
            with self.lock:
                nonce = params[0].get('data', '')
                item = self.identity_challenges.get(nonce)
                if (not item or time.monotonic() > item['expires']
                        or params[0].get('from', '').lower() != self.wallet.lower()):
                    raise Rejected('LAB_IDENTITY_CHALLENGE_INVALID')
                proof = '0x'+hmac.new(self.identity_key,
                    (nonce+route+self.wallet.lower()).encode(), hashlib.sha256).hexdigest()
                item['route'] = route; item['proof'] = proof
                self.event({'route': route, 'method': method, 'scope': 'LOCAL_IDENTITY_PROBE',
                            'challenge_id': hashlib.sha256(nonce.encode()).hexdigest(),
                            'result': 'PROOF_GENERATED', 'external_rpc_calls': 0})
                return proof
        if method == 'eth_sendRawTransaction':
            with self.lock:
                before = len(self.rpc.sent)
                if route == 'ingress':
                    if not self.ingress: raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
                    record = self.ingress.capture(params[0])
                    # The capture transaction has COMMITTED before this LOCAL EVM execution.
                    if self.rpc.call('eth_getTransactionReceipt', [record['tx_hash']]):
                        result = record['tx_hash']
                    elif any(e.get('hash') == record['tx_hash'] and e.get('attempted') for e in self.events):
                        raise Rejected('LAB_UNCERTAIN_SEND_REQUIRES_INSPECTION')
                    else:
                        self.event({'route': route, 'hash': record['tx_hash'], 'persisted_before_execution': True, 'attempted': True})
                        result = self.rpc.call(method, params)
                else:
                    # Intentionally unsafe second LOCAL route to detect wallet RPC bypass.
                    result = self.rpc.call(method, params)
                self.event({'route': route, 'method': method, 'hash': result,
                            'new_local_executions': len(self.rpc.sent)-before,
                            'external_broadcasts': 0})
                return result
        result=self.rpc.call(method, params)
        safe_result = result if method in {'eth_chainId','net_version','web3_clientVersion','eth_blockNumber'} else (
            {'number':result.get('number'),'hash':result.get('hash')} if method=='eth_getBlockByNumber' and isinstance(result,dict) else 'READ_COMPLETED')
        self.event({'route':route,'method':method,'scope':'LOCAL_RPC_READ', 'result':safe_result, 'external_rpc_calls':0})
        return result

    def identity_challenge(self):
        with self.lock:
            self.identity_challenges = {n:v for n,v in self.identity_challenges.items() if time.monotonic() <= v['expires']}
            if len(self.identity_challenges) >= 64: raise Rejected('LAB_IDENTITY_CHALLENGE_LIMIT')
            nonce = '0x'+secrets.token_hex(32)
            self.identity_challenges[nonce] = {'expires': time.monotonic()+30}
            return {'nonce': nonce, 'to': self.identity_address, 'wallet': self.wallet}

    def verify_identity(self, nonce, proof):
        with self.lock:
            if not isinstance(nonce,str): raise Rejected('LAB_IDENTITY_CHALLENGE_INVALID')
            item = self.identity_challenges.pop(nonce, None) # Single-use, even on failure.
            if not item or time.monotonic() > item['expires']: raise Rejected('LAB_IDENTITY_CHALLENGE_INVALID')
            if item.get('route') != 'ingress': raise Rejected('LAB_IDENTITY_INGRESS_REQUIRED')
            if (not isinstance(proof,str) or len(proof)!=66
                    or not hmac.compare_digest(item.get('proof',''),proof)):
                raise Rejected('LAB_IDENTITY_PROOF_INVALID')
            self.event({'route':'ingress','scope':'LOCAL_IDENTITY_VERIFIED', 'result':'VERIFIED', 'external_broadcasts':0})
            return {'verified':True,'route':'ingress'}

    def event(self, event):
        with self.lock:
            self.events.append({'observed_at': time.time(), **event})
            write_secret_atomic(self.directory/'rpc-events.json', json.dumps(self.events, indent=2).encode())


PAGE = r'''<!doctype html><meta charset="utf-8"><title>TRUSTA ISOLATED METAMASK LAB</title>
<h1>TRUSTA — 仅本地隔离链测试</h1><p>禁止使用现有 Sepolia 钱包。此服务不连接任何外部 RPC。</p>
<button id="connect">连接隔离测试钱包</button><button id="prepare">生成新的本地测试部署计划</button>
<button id="send" disabled>提交已显示的测试计划给 MetaMask</button><pre id="out"></pre>
<script src="/lab.js"></script>'''

JS = r'''
let account, proposal;
const out=document.querySelector('#out');const send=document.querySelector('#send');
function log(x){out.textContent=JSON.stringify(x,null,2)}
async function guard(){
 const w=await fetch('/wallet').then(x=>x.json());
 if(!window.ethereum?.isMetaMask)throw Error('必须使用实际 MetaMask 扩展');
 const accounts=await ethereum.request({method:'eth_accounts'});
 if(accounts.length!==1||accounts[0].toLowerCase()!==w.wallet.toLowerCase())throw Error('测试钱包地址不匹配，拒绝请求签名');
 if(await ethereum.request({method:'eth_chainId'})!=='0xaa36a7')throw Error('仅允许本地测试链');
 const challenge=await localControl('/identity/challenge/__TOKEN__',{});
 const proof=await ethereum.request({method:'eth_call',params:[{from:accounts[0],to:challenge.to,data:challenge.nonce},'latest']});
 const verified=await localControl('/identity/verify/__TOKEN__',{nonce:challenge.nonce,proof});
 if(verified.verified!==true||verified.route!=='ingress')throw Error('本地身份或 ingress 路由验证失败，拒绝请求签名');
 const current=await ethereum.request({method:'eth_accounts'});
 if(current.length!==1||current[0].toLowerCase()!==accounts[0].toLowerCase()||await ethereum.request({method:'eth_chainId'})!=='0xaa36a7')throw Error('验证期间钱包或网络改变，拒绝请求签名');
 return accounts[0];
}
async function localControl(path,body){
 const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const v=await r.json();if(!r.ok||v.error)throw Error(v.error||'本地身份验证失败');return v;
}
document.querySelector('#connect').onclick=async()=>{try{
 await ethereum.request({method:'eth_requestAccounts'});account=await guard();log({connected:account});
}catch(e){send.disabled=true;proposal=null;log({error:e.message})}};
document.querySelector('#prepare').onclick=async()=>{try{
 account=await guard();proposal=await fetch('/control/__TOKEN__',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(x=>x.json());log(proposal);send.disabled=false;
}catch(e){send.disabled=true;log({error:e.message})}};
send.onclick=async()=>{send.disabled=true;try{
 await guard();const p=proposal;const t={from:account};
 for(const [k,v] of Object.entries(p.plan.transaction))t[k]=typeof v==='number'?'0x'+v.toString(16):v;
 const hash=await ethereum.request({method:'eth_sendTransaction',params:[t]});log({plan_hash:p.plan_hash,transaction_hash:hash,scope:'LOCAL EVM ONLY'});
}catch(e){log({error:e.message,scope:'LOCAL EVM ONLY'})}};
window.ethereum?.on('chainChanged',()=>{send.disabled=true;proposal=null});
window.ethereum?.on('accountsChanged',()=>{send.disabled=true;proposal=null});
'''


def make_server(lab, port=18765):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup(); self.connection.settimeout(5) # Bound slow headers as well as bodies.
        def log_message(self, *args): pass  # No token, calldata or signed bytes in access logs.
        def reply(self, status, body, content='application/json'):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status); self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection','close'); self.close_connection=True
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            origin = self.headers.get('Origin')
            if origin is not None and self.origin_ok():
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)
        def host_ok(self): return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
        def origin_ok(self):
            return self.headers.get('Origin') in (
                None, f'http://127.0.0.1:{self.server.server_port}',
                'chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn')
        def rpc_route(self):
            for route in ('ingress', 'bypass'):
                if secrets.compare_digest(self.path, '/'+route+'/'+lab.token): return route
            return None
        def diagnostic(self, route, status, reason):
            # Never log the capability-bearing path, body, or raw transaction.
            lab.event({'transport': self.command, 'route': route, 'http_status': status,
                       'reason': reason, 'host_ok': self.host_ok(),
                       'origin_allowed': self.origin_ok(),
                       'private_network_preflight': self.headers.get('Access-Control-Request-Private-Network') == 'true',
                       'http_user_agent': self.headers.get('User-Agent', '')[:256],
                       'content_type': self.headers.get_content_type(),
                       'external_broadcasts': 0})
        def do_OPTIONS(self):
            route = self.rpc_route()
            requested = {h.strip().lower() for h in
                         self.headers.get('Access-Control-Request-Headers', '').split(',') if h.strip()}
            if (not self.host_ok() or not route or not self.origin_ok()
                    or self.headers.get('Origin') is None
                    or self.headers.get('Access-Control-Request-Method') != 'POST'
                    or not requested <= {'content-type'}
                    or self.headers.get('Transfer-Encoding')
                    or self.headers.get('Content-Length') not in (None, '0')):
                self.diagnostic(route, 403, 'PREFLIGHT_DENIED')
                return self.reply(403, {'error': 'PREFLIGHT_DENIED'})
            self.diagnostic(route, 204, 'PREFLIGHT_ALLOWED')
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin, Access-Control-Request-Headers')
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            # Older Chromium PNA preflights require this explicit response.
            # It is granted only after the same origin/path/token checks above.
            if self.headers.get('Access-Control-Request-Private-Network') == 'true':
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close'); self.close_connection=True
            self.end_headers()
        def do_GET(self):
            if not self.host_ok(): return self.reply(403, {'error': 'HOST_DENIED'})
            if self.path == '/': return self.reply(200, PAGE, 'text/html; charset=utf-8')
            if self.path == '/lab.js': return self.reply(200, JS.replace('__TOKEN__',lab.token), 'application/javascript')
            if self.path == '/wallet': return self.reply(200, {'wallet': lab.wallet})
            return self.reply(404, {'error': 'NOT_FOUND'})
        def do_POST(self):
            self.connection.settimeout(5)
            route = self.rpc_route()
            if self.path in ('/identity/challenge/'+lab.token, '/identity/verify/'+lab.token):
                if (not self.host_ok() or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}'
                        or self.headers.get_content_type()!='application/json'
                        or self.headers.get('Transfer-Encoding')
                        or len(self.headers.get_all('Content-Length',[]))!=1):
                    return self.reply(403,{'error':'IDENTITY_CONTROL_DENIED'})
                try:
                    from backend.llm.validation import strict_json
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=4096: raise Rejected('RPC_REQUEST_LIMIT')
                    body=strict_json(self.rfile.read(length).decode())
                    if self.path.startswith('/identity/challenge/'):
                        if body!={}: raise Rejected('RPC_REQUEST_INVALID')
                        result=lab.identity_challenge()
                    else:
                        if not isinstance(body,dict) or set(body)!={'nonce','proof'}:raise Rejected('RPC_REQUEST_INVALID')
                        result=lab.verify_identity(body['nonce'],body['proof'])
                    return self.reply(200,result)
                except Exception as exc:
                    reason=exc.code if isinstance(exc,Rejected) else 'RPC_REQUEST_INVALID'
                    lab.event({'scope':'LOCAL_IDENTITY_REJECTED','reason':reason,'external_broadcasts':0})
                    return self.reply(403,{'error':reason})
            if (self.host_ok() and self.path == '/control/'+lab.token
                    and self.headers.get('Origin') == f'http://127.0.0.1:{self.server.server_port}'
                    and self.headers.get('Content-Length') == '2' and not self.headers.get('Transfer-Encoding')):
                if self.rfile.read(2) == b'{}': return self.reply(200, lab.prepare())
                return self.reply(400, {'error':'CONTROL_REQUEST_INVALID'})
            if (not self.host_ok() or route not in {'ingress', 'bypass'}
                    or not self.origin_ok()
                    or self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1
                    or self.headers.get_content_type()!='application/json'):
                self.diagnostic(route, 403, 'LOCAL_RPC_DENIED')
                return self.reply(403, {'error': 'LOCAL_RPC_DENIED'})
            self.diagnostic(route, 200, 'RPC_HTTP_ACCEPTED')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 131072: raise Rejected('RPC_REQUEST_LIMIT')
                from backend.llm.validation import strict_json
                req = strict_json(self.rfile.read(length).decode())
                if (not isinstance(req, dict) or req.get('jsonrpc') != '2.0'
                        or not isinstance(req.get('method'), str)):
                    raise Rejected('RPC_REQUEST_INVALID')
                # JSON-RPC permits omitted params. Only parameterless reads may
                # use this compatibility form; writes never acquire defaults.
                params = req.get('params', [])
                if (not isinstance(params, list) or ('params' not in req and req['method'] not in {
                        'eth_chainId', 'net_version', 'web3_clientVersion', 'eth_blockNumber',
                        'eth_gasPrice', 'eth_maxPriorityFeePerGas'})):
                    raise Rejected('RPC_REQUEST_INVALID')
                lab.event({'route':route,'method':req['method'],'http_user_agent':self.headers.get('User-Agent','')[:256],
                           'origin':self.headers.get('Origin'), 'observation':'HTTP_HEADERS_NOT_CRYPTOGRAPHIC_ATTESTATION'})
                result = lab.request(route, req['method'], params)
                return self.reply(200, {'jsonrpc': '2.0', 'id': req.get('id'), 'result': result})
            except Exception as exc:
                reason = exc.code if isinstance(exc, Rejected) else 'RPC_REQUEST_INVALID'
                lab.event({'route': route, 'rejected': reason, 'external_broadcasts': 0})
                request_id = req.get('id') if isinstance(locals().get('req'), dict) else None
                return self.reply(200, {'jsonrpc': '2.0', 'id': request_id,
                                        'error': {'code': -32000, 'message': reason}})
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def __init__(self,*args,**kwargs):
            self.slots=threading.BoundedSemaphore(16); super().__init__(*args,**kwargs)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request); return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
        def verify_request(self, request, address): return address[0] == '127.0.0.1'
    return Server(('127.0.0.1', port), Handler)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--directory', type=Path, default=Path('data/metamask-validation/lab'))
    p.add_argument('--port', type=int, default=18765)
    p.add_argument('--resume-unused',action='store_true'); args=p.parse_args()
    lab=Lab(args.directory,args.resume_unused); server=make_server(lab,args.port)
    info={'scope':'LOCAL_PYEVM_ONLY', 'page':f'http://127.0.0.1:{server.server_port}/',
          'ingress_rpc':f'http://127.0.0.1:{server.server_port}/ingress/{lab.token}',
          'bypass_rpc':f'http://127.0.0.1:{server.server_port}/bypass/{lab.token}',
          'wallet':lab.wallet,'production_compatible':False}
    write_secret_atomic(args.directory/'operator-session.json',json.dumps(info,indent=2).encode())
    print(json.dumps({k:v for k,v in info.items() if k not in {'ingress_rpc','bypass_rpc'}}),flush=True)
    server.serve_forever()


if __name__=='__main__': main()
