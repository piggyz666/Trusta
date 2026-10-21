"""Public RPC verification and a separate FISCO provenance outbox worker.

This module cannot reach Sepolia, bank adapters or existing user wallets.
FISCO status 0 is success (unlike Ethereum receipt status 1).
"""
import hashlib
import json
from pathlib import Path
import subprocess
import re
import urllib.request
from eth_abi import encode, decode
from eth_utils import keccak
from backend.domain import Rejected, canonical, digest
from backend.collaboration.process import wsl_executable


def fields(record):
    return [digest(record['round_id']),digest(record),record['evidence_root'],record['snapshot_hash'],
            digest({'status':record['status'],'verdict':record['verdict'],
                    'aggregation_version':record['aggregation_version']}),record['evidence_count'],record['k']]


def calldata(record):
    expected=fields(record)
    selector=keccak(text='commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint256)')[:4]
    return '0x'+(selector+encode(['bytes32']*5+['uint256']*2,
                 [bytes.fromhex(v) for v in expected[:5]]+expected[5:])).hex()


class FiscoVerifier:
    def __init__(self, port, *, artifact, artifact_sha256, address, publisher):
        if type(port) is not int or not 9645 <= port <= 9652:
            raise Rejected('ANCHOR_FISCO_RPC_NOT_ISOLATED')
        if any(not isinstance(v,str) or not re.fullmatch('0x[0-9a-fA-F]{40}',v) for v in (address,publisher)):
            raise Rejected('ANCHOR_FISCO_ADDRESS_INVALID')
        self.url='http://127.0.0.1:'+str(port)
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        raw=Path(artifact).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=artifact_sha256:
            raise Rejected('ANCHOR_FISCO_ARTIFACT_MISMATCH')
        self.artifact=json.loads(raw);self.address=address.lower();self.publisher=publisher.lower()
        if self.rpc('getClientVersion',[])['Chain Id']!='101':
            raise Rejected('ANCHOR_FISCO_WRONG_CHAIN')

    def rpc(self,method,params):
        if method not in {'getClientVersion','getBlockNumber','getBlockHashByNumber','getCode',
                           'getTransactionReceipt','call','getBlockByNumber','getSealerList'}:
            raise Rejected('ANCHOR_READONLY_RPC_ONLY')
        try:
            request=urllib.request.Request(self.url,data=canonical({'jsonrpc':'2.0','method':method,
                'params':params,'id':1}).encode(),headers={'Content-Type':'application/json'})
            with self.http.open(request,timeout=10) as response:
                raw=response.read(1048577)
                if len(raw)>1048576: raise ValueError()
                result=json.loads(raw)
            if 'error' in result or result.get('id')!=1 or 'result' not in result:raise ValueError()
            return result['result']
        except (OSError,ValueError,KeyError,TypeError):
            raise Rejected('ANCHOR_FISCO_READ_FAILED') from None

    def call(self,signature,types=(),values=(),returns=()):
        data='0x'+(keccak(text=signature)[:4]+encode(types,values)).hex()
        result=self.rpc('call',[1,{'from':self.publisher,'to':self.address,'value':'0x0','data':data}])
        if int(result['status'],16)!=0:raise Rejected('ANCHOR_FISCO_CALL_REVERTED')
        return decode(returns,bytes.fromhex(result['output'][2:]))

    def read(self,record):
        result=self.call('get(bytes32)',['bytes32'],[bytes.fromhex(fields(record)[0])],
                         ['bytes32']*4+['uint256']*2)
        return [v.hex() if isinstance(v,bytes) else v for v in result]

    def verify_contract(self):
        code=self.rpc('getCode',[1,self.address])
        if code.lower()!='0x'+self.artifact['runtime'].lower():
            raise Rejected('ANCHOR_FISCO_CODE_MISMATCH')
        if self.call('publisher()',returns=['address'])[0].lower()!=self.publisher:
            raise Rejected('ANCHOR_FISCO_PUBLISHER_MISMATCH')
        return keccak(bytes.fromhex(code[2:])).hex()

    def receipt(self,txhash):
        r=self.rpc('getTransactionReceipt',[1,txhash])
        if (not r or r['transactionHash'].lower()!=txhash.lower() or int(r['status'],16)!=0
                or r['from'].lower()!=self.publisher):
            raise Rejected('ANCHOR_FISCO_RECEIPT_INVALID')
        block=r['blockNumber'];canonical_hash=self.rpc('getBlockHashByNumber',[1,block])
        if canonical_hash.lower()!=r['blockHash'].lower():raise Rejected('ANCHOR_FISCO_NONCANONICAL')
        return r

    def verify(self,record,txhash):
        code_hash=self.verify_contract();r=self.receipt(txhash);expected=fields(record)
        if r['to'].lower()!=self.address or self.read(record)!=expected[1:]:
            raise Rejected('ANCHOR_FISCO_RECORD_MISMATCH')
        if r['input'].lower()!=calldata(record):raise Rejected('ANCHOR_FISCO_INPUT_MISMATCH')
        topic='0x'+keccak(text='RoundCommitted(bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint256)').hex()
        matches=[log for log in r['logs'] if log['address'].lower()==self.address
                 and log['topics']==[topic,'0x'+expected[0]]]
        if len(matches)!=1:raise Rejected('ANCHOR_FISCO_EVENT_MISMATCH')
        values=decode(['bytes32']*4+['uint256']*2,bytes.fromhex(matches[0]['data'][2:]))
        if [v.hex() if isinstance(v,bytes) else v for v in values]!=expected[1:]:
            raise Rejected('ANCHOR_FISCO_EVENT_MISMATCH')
        return {'verified':True,'chain_id':101,'group_id':1,'rpc':self.url,'transaction_hash':txhash,
                'block_hash':r['blockHash'],'block_number':r['blockNumber'],'receipt':r,
                'runtime_code_keccak':code_hash,'publisher':self.publisher,'record_hash':digest(record),
                'sealers':self.rpc('getSealerList',[1]),'readback':expected[1:]}

    def verify_deployment(self,txhash):
        code_hash=self.verify_contract();receipt=self.receipt(txhash)
        if (receipt['contractAddress'].lower()!=self.address
                or receipt['input'].lower()!='0x'+self.artifact['bytecode'].lower()):
            raise Rejected('ANCHOR_FISCO_DEPLOYMENT_MISMATCH')
        return {'verified':True,'receipt':receipt,'runtime_code_keccak':code_hash}


class FiscoAnchorWorker:
    def __init__(self,rounds,verifiers):
        if len(verifiers)<2 or len({v.url for v in verifiers})!=len(verifiers):
            raise Rejected('ANCHOR_INDEPENDENT_RPC_REQUIRED')
        self.rounds,self.s,self.verifiers=rounds,rounds.s,tuple(verifiers)
        self.workspace=Path(__file__).resolve().parents[2]

    def process(self,round_id,owner):
        with self.s.lock,self.s.db.transaction() as conn:
            self.rounds._round(conn,round_id,owner)
            row=conn.execute('SELECT * FROM collaboration_outbox WHERE round_id=?',(round_id,)).fetchone()
            if not row:raise Rejected('ANCHOR_NOT_CLOSED')
            saved=self.rounds._decode(row);record=saved['record']
            if row['record_hash']!=digest(record):raise Rejected('ANCHOR_OUTBOX_TAMPERED')
            resume=row['status']!='PENDING'
            if not resume:conn.execute("UPDATE collaboration_outbox SET status='SUBMITTING' WHERE round_id=?",(round_id,))
        if resume:return self.reconcile(round_id,owner)
        try:
            if any(v.read(record)[0]!='0'*64 for v in self.verifiers):
                raise Rejected('ANCHOR_RECONCILIATION_REQUIRED_NO_BLIND_RETRY')
            for v in self.verifiers:v.verify_contract()
            f=fields(record)
            request=dict(zip(['round_id_hash','record_hash','evidence_root','snapshot_hash','verdict_hash','count','quorum'],f))
            result=subprocess.run([wsl_executable(),'-d','Ubuntu-22.04','--','bash','scripts/anchor_weidentity_bridge.sh'],
                cwd=self.workspace,input=canonical({'operation':'anchor-commit',**request}).encode(),
                capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            lines=[v[12:] for v in result.stdout.decode('utf-8').splitlines() if v.startswith('TRUSTA_JSON=')]
            if result.returncode or len(lines)!=1:
                # Bridge stdout contains public diagnostics only, never secret input.
                with self.s.lock,self.s.db.transaction() as conn:
                    saved['bridge_failure']={'exit':result.returncode,'public_output':result.stdout.decode('utf-8',errors='replace')[:2048]}
                    conn.execute('UPDATE collaboration_outbox SET body=? WHERE round_id=?',
                                 (self.rounds._encode(saved),round_id))
                raise Rejected('ANCHOR_SUBMISSION_UNCERTAIN')
            response=json.loads(lines[0]);tx=response['receipt']['transactionHash']
            with self.s.lock,self.s.db.transaction() as conn:
                conn.execute("UPDATE collaboration_outbox SET status='VERIFYING',body=? WHERE round_id=?",
                             (self.rounds._encode(saved|{'submission':response}),round_id))
            checks=[v.verify(record,tx) for v in self.verifiers]
            saved|={'submission':response,'independent_rpc':checks}
            with self.s.lock,self.s.db.transaction() as conn:
                conn.execute("UPDATE collaboration_outbox SET status='ANCHORED',body=? WHERE round_id=?",
                             (self.rounds._encode(saved),round_id))
                self.s.audit.append_in(conn,record['task_id'],None,'ANCHOR_FISCO_CONFIRMED','supervisor',
                                       {'round_id':round_id,'record_hash':digest(record),'transaction_hash':tx})
            return saved
        except Exception:
            # The durable state deliberately blocks retries after uncertain sends.
            # recover() verifies a supplied public hash rather than re-submitting.
            raise

    def recover(self,round_id,owner,txhash):
        with self.s.lock,self.s.db.transaction() as conn:
            self.rounds._round(conn,round_id,owner)
            row=conn.execute('SELECT * FROM collaboration_outbox WHERE round_id=?',(round_id,)).fetchone()
            if not row:raise Rejected('ANCHOR_NOT_CLOSED')
            saved=self.rounds._decode(row);record=saved['record']
            if row['record_hash']!=digest(record):raise Rejected('ANCHOR_OUTBOX_TAMPERED')
        checks=[v.verify(record,txhash) for v in self.verifiers]
        saved|={'recovered_transaction_hash':txhash,'independent_rpc':checks}
        with self.s.lock,self.s.db.transaction() as conn:
            conn.execute("UPDATE collaboration_outbox SET status='ANCHORED',body=? WHERE round_id=?",
                         (self.rounds._encode(saved),round_id))
        return saved

    def find_transaction(self,record):
        """Bounded read-only recovery when no local response survived."""
        rpc=self.verifiers[0];height=int(rpc.rpc('getBlockNumber',[1]),16);wanted=calldata(record)
        inspected=0
        for number in range(height,max(-1,height-32),-1):
            block=rpc.rpc('getBlockByNumber',[1,hex(number),True])
            for tx in block['transactions']:
                inspected+=1
                if inspected>256:raise Rejected('ANCHOR_TX_HASH_REQUIRED_NO_REBROADCAST')
                if (tx.get('to','').lower()==rpc.address and tx.get('input','').lower()==wanted
                        and tx.get('from','').lower()==rpc.publisher):return tx['hash']
        raise Rejected('ANCHOR_TX_HASH_REQUIRED_NO_REBROADCAST')

    def reconcile(self,round_id,owner):
        with self.s.lock,self.s.db.transaction() as conn:
            self.rounds._round(conn,round_id,owner)
            row=conn.execute('SELECT * FROM collaboration_outbox WHERE round_id=?',(round_id,)).fetchone()
            if not row:raise Rejected('ANCHOR_NOT_CLOSED')
            saved=self.rounds._decode(row);record=saved['record']
            if row['record_hash']!=digest(record):raise Rejected('ANCHOR_OUTBOX_TAMPERED')
        tx=saved.get('recovered_transaction_hash') or saved.get('submission',{}).get('receipt',{}).get('transactionHash')
        if not tx:
            path=self.workspace/'data/anchor-integration/weidentity-lab/anchor-receipts'/(fields(record)[0]+'.json')
            if path.is_file() and path.stat().st_size<=65536:
                response=json.loads(path.read_text(encoding='utf-8'));tx=response['receipt']['transactionHash']
        if not tx:tx=self.find_transaction(record)
        return self.recover(round_id,owner,tx)
