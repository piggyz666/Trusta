"""EVM testnet outbox adapter. Independent read-back is mandatory for verification."""
from dataclasses import dataclass, asdict
import hashlib
import json
import os
import time
from backend.domain import Rejected, canonical, digest
from backend.security.crypto import HEX
from backend.bank_sdk.transport import BankTransport
from backend.audit_trust.transaction_safety import require_sepolia, prepare_transaction, sign_approved_plan, broadcast_approved

@dataclass(frozen=True)
class ExternalAnchorProof:
    tx_hash: str
    chain_id: int
    contract: str
    checkpoint_hash: str
    merkle_root: str
    identifier_commitment: str
    sequence: int
    schema_version: int
    block_number: int | None
    block_hash: str | None
    confirmations: int
    required_confirmations: int
    checks: dict
    external_blockchain_verified: bool
    reason: str

class EthereumRPC:
    def __init__(self, url, timeout=10):
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        self.path=parsed.path or '/'
        self.transport=BankTransport(f'{parsed.scheme}://{parsed.netloc}',timeout)
        if parsed.query or parsed.fragment or parsed.username: raise Rejected('INVALID_RPC_URL')
        self.sequence=0
        self.url=url
        self.timeout=timeout

    def call(self, method, params):
        from urllib.request import Request, build_opener
        from backend.bank_sdk.transport import NoRedirect
        if method == 'eth_sendRawTransaction':
            from eth_utils import keccak
            expected=getattr(self,'_approved_raw_transaction_hash',None)
            if (os.getenv('ANCHOR_ALLOW_BROADCAST')!='true' or os.getenv('ANCHOR_ALLOW_SIGNING')!='true'
                    or not expected or len(params)!=1 or keccak(hexstr=params[0]).hex()!=expected):
                raise Rejected('ANCHOR_BROADCAST_APPROVAL_REQUIRED')
            require_sepolia(self)
        elif method not in {'eth_chainId','eth_getCode','eth_getBalance','eth_getTransactionCount',
                'eth_gasPrice','eth_maxPriorityFeePerGas','eth_feeHistory','eth_estimateGas','eth_call',
                'eth_getTransactionByHash','eth_getTransactionReceipt','eth_getBlockByNumber','eth_blockNumber'}:
            raise Rejected('ANCHOR_RPC_METHOD_FORBIDDEN')
        self.sequence+=1
        request_id=self.sequence
        deadline=time.monotonic()+self.timeout
        try:
            # Anchor-only system proxy support. BankTransport stays untouched.
            request=Request(self.url,data=canonical({'jsonrpc':'2.0','id':request_id,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with build_opener(NoRedirect()).open(request,timeout=self.timeout) as response:
                from backend.network import read_bounded
                data=read_bounded(response,1048576,deadline)
                if response.headers.get_content_type()!='application/json':
                    raise Rejected('RPC_RESPONSE_INVALID')
                from backend.llm.validation import strict_json
                raw=strict_json(data.decode())
        except Rejected:
            raise
        except Exception:
            raise Rejected('ANCHOR_RPC_UNAVAILABLE') from None
        if not isinstance(raw,dict) or raw.get('id')!=request_id or raw.get('jsonrpc')!='2.0' or 'error' in raw or 'result' not in raw:
            raise Rejected('RPC_RESPONSE_INVALID')
        return raw['result']

class ExternalBlockchainAnchor:
    name='EXTERNAL_EVM_TESTNET'
    def __init__(self, db, rpc, contract, chain_id=11155111, confirmations=3, wallet_env='ANCHOR_WALLET_PRIVATE_KEY', finalized=False):
        from eth_utils import is_address, to_checksum_address
        if chain_id!=11155111 or type(confirmations) is not int or not 1<=confirmations<=64 or not is_address(contract):
            raise Rejected('ANCHOR_TESTNET_CONFIG_REQUIRED')
        self.db,self.rpc,self.contract=db,rpc,to_checksum_address(contract)
        self.chain_id,self.confirmations,self.wallet_env,self.finalized=chain_id,confirmations,wallet_env,finalized
        from types import SimpleNamespace
        from backend.audit import AuditEngine
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(SimpleNamespace(db=db,audit=AuditEngine(db)))
        with db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_anchor_submissions(hash TEXT PRIMARY KEY,root TEXT NOT NULL,tx_hash TEXT NOT NULL,body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS external_anchor_nonces(chain_id INTEGER NOT NULL,sender TEXT NOT NULL,nonce INTEGER NOT NULL,checkpoint_hash TEXT NOT NULL,tx_hash TEXT NOT NULL,PRIMARY KEY(chain_id,sender,nonce))')

    @staticmethod
    def selector(signature):
        from eth_utils import keccak
        return keccak(text=signature)[:4].hex()

    def prepare_commit(self, commitment, checkpoint_hash):
        require_sepolia(self.rpc)
        if not HEX.fullmatch(checkpoint_hash) or not HEX.fullmatch(commitment.digest): raise Rejected('INVALID_ANCHOR_COMMITMENT')
        if self.rpc.call('eth_getCode',[self.contract,'latest']) in {'0x','0x0'}: raise Rejected('ANCHOR_CONTRACT_NOT_DEPLOYED')
        checkpoint=next((json.loads(r['body']) for r in self.db.rows('SELECT body FROM audit_checkpoints') if digest(json.loads(r['body']))==checkpoint_hash),None)
        if not checkpoint: raise Rejected('ANCHOR_CHECKPOINT_NOT_FOUND')
        if checkpoint['merkle_root']!=commitment.digest: raise Rejected('ANCHOR_ROOT_MISMATCH')
        identifier=hashlib.sha256(checkpoint['checkpoint_id'].encode()).hexdigest();sequence=checkpoint['last_event_sequence']
        data='0x'+self.selector('commit(bytes32,bytes32,bytes32,uint64,uint32)')+checkpoint_hash+commitment.digest+identifier+f'{sequence:064x}'+f'{1:064x}'
        sender=os.getenv('ANCHOR_WALLET_ADDRESS','')
        if not sender: raise Rejected('ANCHOR_PUBLIC_ADDRESS_REQUIRED')
        return prepare_transaction(self.rpc,sender,data,'commit',self.contract)

    def submit(self, commitment, checkpoint_hash, approved_plan=None):
        require_sepolia(self.rpc)
        if not HEX.fullmatch(checkpoint_hash) or not HEX.fullmatch(commitment.digest):raise Rejected('INVALID_ANCHOR_COMMITMENT')
        prior=self.db.one('SELECT * FROM external_anchor_submissions WHERE hash=?',(checkpoint_hash,))
        if prior:
            if prior['root']!=commitment.digest:raise Rejected('ANCHOR_ROOT_MISMATCH')
            return prior['tx_hash']
        sender=os.getenv('ANCHOR_WALLET_ADDRESS','').lower()
        with self.claims.hold('anchor-wallet:'+str(self.chain_id)+':'+sender):
            return self._submit(commitment,checkpoint_hash,approved_plan)

    def _submit(self, commitment, checkpoint_hash, approved_plan=None):
        require_sepolia(self.rpc)
        if not HEX.fullmatch(checkpoint_hash) or not HEX.fullmatch(commitment.digest): raise Rejected('INVALID_ANCHOR_COMMITMENT')
        prior=self.db.one('SELECT * FROM external_anchor_submissions WHERE hash=?',(checkpoint_hash,))
        if prior:
            if prior['root']!=commitment.digest: raise Rejected('ANCHOR_ROOT_MISMATCH')
            return prior['tx_hash']  # Never allocate a new nonce after uncertain broadcast.
        if not os.getenv(self.wallet_env,'') and not os.getenv('ANCHOR_KEYSTORE_PATH',''):
            raise Rejected('BLOCKED_BY_EXTERNAL_CREDENTIAL')
        fresh=self.prepare_commit(commitment,checkpoint_hash)
        plan=approved_plan or fresh
        if (plan.get('kind')!='commit' or plan.get('sender')!=fresh['sender']
                or plan.get('transaction',{}).get('data')!=fresh['transaction']['data']
                or plan.get('transaction',{}).get('to')!=fresh['transaction']['to']):
            raise Rejected('ANCHOR_TRANSACTION_BINDING_INVALID')
        nonce=fresh['transaction']['nonce'];sender=fresh['sender'].lower()
        reserved=self.db.one('SELECT checkpoint_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',(self.chain_id,sender,nonce))
        if reserved and reserved['checkpoint_hash']!=checkpoint_hash:
            raise Rejected('ANCHOR_NONCE_RESERVED')
        data=plan['transaction']['data'];identifier=data[138:202];sequence=int(data[202:266],16)
        signed=sign_approved_plan(self.rpc,plan,self.wallet_env);tx_hash='0x'+signed.hash.hex().removeprefix('0x')
        body={'checkpoint_hash':checkpoint_hash,'root':commitment.digest,'identifier':identifier,'sequence':sequence,'version':1,
              'chain_id':self.chain_id,'contract':self.contract,'publisher':plan['sender'],'input':data,
              'unsigned_plan':plan,'raw_tx':'0x'+signed.raw_transaction.hex().removeprefix('0x')}
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO external_anchor_submissions VALUES(?,?,?,?)',(checkpoint_hash,commitment.digest,tx_hash,canonical(body)))
            conn.execute('INSERT INTO external_anchor_nonces VALUES(?,?,?,?,?)',(self.chain_id,sender,nonce,checkpoint_hash,tx_hash))
        returned=broadcast_approved(self.rpc,plan,signed)
        if returned.lower()!=tx_hash.lower(): raise Rejected('ANCHOR_TX_HASH_MISMATCH')
        return tx_hash

    def inspect(self, anchor_ref):
        from eth_utils import keccak
        row=self.db.one('SELECT * FROM external_anchor_submissions WHERE tx_hash=?',(anchor_ref,))
        if not row: raise Rejected('ANCHOR_NOT_FOUND')
        expected=json.loads(row['body']);checks={};block_number=block_hash=None;confirmations=0
        try:
            checks['chain_id']=int(self.rpc.call('eth_chainId',[]),16)==self.chain_id==expected['chain_id']
            if not checks['chain_id']: raise Rejected('ANCHOR_CHAIN_MISMATCH')
            checks['contract_configuration']=self.contract.lower()==expected['contract'].lower()
            tx=self.rpc.call('eth_getTransactionByHash',[anchor_ref]);receipt=self.rpc.call('eth_getTransactionReceipt',[anchor_ref])
            if not tx or not receipt: raise Rejected('ANCHOR_NOT_MINED')
            checks['transaction_hash']=tx['hash'].lower()==anchor_ref.lower()==receipt['transactionHash'].lower()
            checks['transaction_chain']=int(tx.get('chainId','0x0'),16)==self.chain_id
            checks['destination']=tx['to'].lower()==receipt['to'].lower()==self.contract.lower()
            checks['publisher']=tx['from'].lower()==expected['publisher'].lower()
            checks['input']=tx['input'].lower()==expected['input'].lower()
            checks['success']=int(receipt['status'],16)==1
            block_number=int(receipt['blockNumber'],16);block_hash=receipt['blockHash']
            block=self.rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
            checks['canonical_block']=bool(block and block['hash'].lower()==block_hash.lower()==tx['blockHash'].lower() and tx['blockNumber']==receipt['blockNumber'])
            head=int(self.rpc.call('eth_blockNumber',[]),16);confirmations=max(0,head-block_number+1)
            checks['confirmations']=confirmations>=self.confirmations
            if self.finalized:
                final=self.rpc.call('eth_getBlockByNumber',['finalized',False]);checks['finality']=int(final['number'],16)>=block_number
            topic='0x'+keccak(text='Committed(bytes32,bytes32,bytes32,uint64,uint32)').hex()
            event_data='0x'+expected['root']+expected['identifier']+f"{expected['sequence']:064x}"+f"{1:064x}"
            events=[log for log in receipt['logs'] if log.get('address','').lower()==self.contract.lower() and not log.get('removed',False)
                    and [t.lower() for t in log.get('topics',[])]==[topic.lower(),'0x'+expected['checkpoint_hash']] and log.get('data','').lower()==event_data.lower()]
            checks['event']=len(events)==1
            root=self.rpc.call('eth_call',[{'to':self.contract,'data':'0x'+self.selector('read(bytes32)')+expected['checkpoint_hash']},receipt['blockNumber']])
            checks['read_back']=root.lower()==event_data.lower()
            current=self.rpc.call('eth_call',[{'to':self.contract,'data':'0x'+self.selector('read(bytes32)')+expected['checkpoint_hash']},'latest'])
            checks['current_read_back']=current.lower()==event_data.lower()
            reread=self.rpc.call('eth_getTransactionReceipt',[anchor_ref]);checks['stable_receipt']=bool(reread and reread['blockHash']==block_hash)
            verified=all(checks.values());reason='VERIFIED' if verified else 'ANCHOR_EVIDENCE_MISMATCH'
        except Exception as exc:
            verified=False;reason=exc.code if isinstance(exc,Rejected) else 'ANCHOR_RPC_UNAVAILABLE'
        proof=ExternalAnchorProof(anchor_ref,self.chain_id,self.contract,expected['checkpoint_hash'],expected['root'],expected['identifier'],expected['sequence'],1,
                                  block_number,block_hash,confirmations,self.confirmations,checks,verified,reason)
        return {'status':'CONFIRMED' if verified else 'SUBMITTED','root':expected['root'],'checkpoint_hash':expected['checkpoint_hash'],
                'is_blockchain':True,'externally_verified':verified,'proof':asdict(proof)}
