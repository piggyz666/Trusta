import copy,json,tempfile,unittest
import os
from unittest.mock import patch
from pathlib import Path
from eth_utils import keccak
from eth_abi import encode,decode
from eth_tester import EthereumTester
from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
from backend.domain import canonical,Rejected
from backend.domain import digest
from backend.security.extensions import AnchorCommitment
from tests.phase6_helpers import service

class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evm=EthereumTester();cls.owner=cls.evm.get_accounts()[0]
        artifact=json.loads(Path('contracts/AuditAnchorContract.json').read_text())
        tx=cls.evm.send_transaction({'from':cls.owner,'gas':2000000,'data':'0x'+artifact['bin']})
        cls.address=cls.evm.get_transaction_receipt(tx)['contract_address']
    def invoke(self,fn,types,values,who=None):
        data='0x'+(keccak(text=fn)[:4]+encode(types,values)).hex()
        tx=self.evm.send_transaction({'from':who or self.owner,'to':self.address,'gas':300000,'data':data});return self.evm.get_transaction_receipt(tx)
    def read(self,key):return decode(['bytes32','bytes32','uint64','uint32'],bytes.fromhex(self.evm.call({'from':self.owner,'to':self.address,'data':'0x'+(keccak(text='read(bytes32)')[:4]+key).hex()})[2:]))
    def test_actual_evm_commit_read_and_event(self):
        k=keccak(text='first');root=keccak(text='root');ident=keccak(text='id')
        receipt=self.invoke('commit(bytes32,bytes32,bytes32,uint64,uint32)',['bytes32','bytes32','bytes32','uint64','uint32'],[k,root,ident,12,1])
        self.assertEqual(receipt['status'],1);self.assertEqual(len(receipt['logs']),1);self.assertEqual(self.read(k),(root,ident,12,1))
    def test_duplicate_idempotent_and_conflict_reverted(self):
        k=keccak(text='duplicate');r=keccak(text='r');i=keccak(text='i');args=[k,r,i,2,1]
        self.assertEqual(self.invoke('commit(bytes32,bytes32,bytes32,uint64,uint32)',['bytes32','bytes32','bytes32','uint64','uint32'],args)['status'],1)
        duplicate=self.invoke('commit(bytes32,bytes32,bytes32,uint64,uint32)',['bytes32','bytes32','bytes32','uint64','uint32'],args)
        self.assertEqual(duplicate['status'],1);self.assertEqual(duplicate['logs'],[])
        args[1]=keccak(text='bad');self.assertEqual(self.invoke('commit(bytes32,bytes32,bytes32,uint64,uint32)',['bytes32','bytes32','bytes32','uint64','uint32'],args)['status'],0)
    def test_nonpublisher_cannot_commit(self):
        receipt=self.invoke('commit(bytes32,bytes32,bytes32,uint64,uint32)',['bytes32','bytes32','bytes32','uint64','uint32'],[keccak(text='attack'),keccak(text='root'),keccak(text='i'),1,1],self.evm.get_accounts()[1])
        self.assertEqual(receipt['status'],0)

class RPCFixture:
    def __init__(self,expected,txhash):
        self.chain=11155111;self.head=12;self.block='0x'+'a'*64;self.missing=False;self.read_bad=False;self.reorg=False;self.receipts=0
        self.tx={'hash':txhash,'chainId':hex(self.chain),'to':expected['contract'],'from':expected['publisher'],'input':expected['input'],'blockNumber':'0xa','blockHash':self.block}
        self.data='0x'+expected['root']+expected['identifier']+f"{expected['sequence']:064x}"+f'{1:064x}'
        self.receipt={'transactionHash':txhash,'to':expected['contract'],'status':'0x1','blockNumber':'0xa','blockHash':self.block,'logs':[{'address':expected['contract'],'topics':['0x'+keccak(text='Committed(bytes32,bytes32,bytes32,uint64,uint32)').hex(),'0x'+expected['checkpoint_hash']],'data':self.data}]}
    def call(self,method,params):
        if method=='eth_chainId':return hex(self.chain)
        if method=='eth_getTransactionByHash':return None if self.missing else self.tx
        if method=='eth_getTransactionReceipt':
            self.receipts+=1
            return self.receipt|({'blockHash':'0x'+'b'*64} if self.reorg and self.receipts>1 else {})
        if method=='eth_getBlockByNumber':return {'hash':self.block,'number':'0xc'}
        if method=='eth_blockNumber':return hex(self.head)
        if method=='eth_call':return '0x'+'0'*256 if self.read_bad else self.data
        raise AssertionError(method)

class AnchorProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)
        self.tx='0x'+'f'*64;self.expected={'checkpoint_hash':'1'*64,'root':'2'*64,'identifier':'3'*64,'sequence':7,'chain_id':11155111,'version':1,'contract':'0x'+'4'*40,'publisher':'0x'+'5'*40,'input':'0x1234'}
        self.rpc=RPCFixture(self.expected,self.tx);self.a=ExternalBlockchainAnchor(self.s.db,self.rpc,self.expected['contract'])
        with self.s.db.transaction() as db:db.execute('INSERT INTO external_anchor_submissions VALUES(?,?,?,?)',(self.expected['checkpoint_hash'],self.expected['root'],self.tx,canonical(self.expected)))
    def test_complete_proof_local_fixture(self):
        r=self.a.inspect(self.tx);self.assertEqual(r['status'],'CONFIRMED');self.assertTrue(all(r['proof']['checks'].values()))
    def test_hash_alone_cannot_verify(self):
        self.rpc.missing=True;self.assertEqual(self.a.inspect(self.tx)['status'],'SUBMITTED')
    def test_wrong_chain(self):
        self.rpc.chain=1;self.assertFalse(self.a.inspect(self.tx)['externally_verified'])
    def test_wrong_destination_and_failed_receipt(self):
        for field,value in [('to','0x'+'6'*40),('status','0x0')]:
            original=self.rpc.receipt[field];self.rpc.receipt[field]=value;self.assertFalse(self.a.inspect(self.tx)['externally_verified']);self.rpc.receipt[field]=original
    def test_event_mismatch_and_readback_mismatch(self):
        self.rpc.receipt['logs'][0]['data']='0x00';self.assertFalse(self.a.inspect(self.tx)['externally_verified'])
        self.rpc.receipt['logs'][0]['data']=self.rpc.data;self.rpc.read_bad=True;self.assertFalse(self.a.inspect(self.tx)['externally_verified'])
    def test_confirmation_depth_and_reorg(self):
        self.rpc.head=10;self.assertFalse(self.a.inspect(self.tx)['externally_verified'])
        self.rpc.head=12;self.rpc.reorg=True;self.rpc.receipts=0;self.assertFalse(self.a.inspect(self.tx)['externally_verified'])
    def test_production_chain_configuration_rejected(self):
        with self.assertRaises(Rejected):ExternalBlockchainAnchor(self.s.db,self.rpc,self.expected['contract'],chain_id=1)

class AnchorSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name);self.cp=self.s.checkpoints.generate_checkpoint()
        self.sent=[]
        outer=self
        class RPC:
            def call(self,method,params):
                if method=='eth_chainId':return hex(11155111)
                if method=='eth_getCode':return '0x6000'
                if method=='eth_getTransactionCount':return '0x0'
                if method=='eth_gasPrice':return '0x3b9aca00'
                if method=='eth_getBlockByNumber':return {'number':'0x1','hash':'0x'+'a'*64,'baseFeePerGas':'0xd'}
                if method=='eth_maxPriorityFeePerGas':return hex(1000000)
                if method=='eth_estimateGas':return hex(356357)
                if method=='eth_getBalance':return hex(10**18)
                if method=='eth_call':
                    from eth_account import Account
                    return '0x'+'0'*24+Account.from_key('0'*63+'1').address[2:].lower()
                if method=='eth_sendRawTransaction':outer.sent.append(params[0]);return '0x'+keccak(hexstr=params[0]).hex()
                raise AssertionError(method)
        self.a=ExternalBlockchainAnchor(self.s.db,RPC(),'0x'+'4'*40,wallet_env='PHASE6_SYNTHETIC_WALLET')
    def test_signed_transaction_durable_binding_and_idempotency(self):
        root=AnchorCommitment(self.cp['merkle_root']);cp_hash=digest(self.cp)
        from eth_account import Account
        from backend.audit_trust.transaction_safety import plan_hash
        import time
        with patch.dict(os.environ,{'PHASE6_SYNTHETIC_WALLET':'0x'+'0'*63+'1',
                'ANCHOR_WALLET_ADDRESS':Account.from_key('0'*63+'1').address,
                'ANCHOR_CONTRACT_CODE_HASH':'0x'+keccak(hexstr='0x6000').hex(),
                'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true','ANCHOR_KEYSTORE_PATH':''}):
            prepared=self.a.prepare_commit(root,cp_hash)
            os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(prepared)
            with patch('backend.audit_trust.transaction_safety.time.time',return_value=time.time()+2):
                tx=self.a.submit(root,cp_hash,approved_plan=prepared)
            self.assertEqual(self.a.submit(root,cp_hash),tx)
        self.assertEqual(len(self.sent),1)
        body=json.loads(self.s.db.one('SELECT body FROM external_anchor_submissions WHERE hash=?',(cp_hash,))['body'])
        self.assertEqual(body['sequence'],self.cp['last_event_sequence']);self.assertEqual(body['root'],root.digest)
        with self.assertRaises(Rejected):self.a.submit(AnchorCommitment('a'*64),cp_hash)
    def test_missing_credential_never_sends(self):
        with patch.dict(os.environ,{'PHASE6_SYNTHETIC_WALLET':''}):
            with self.assertRaises(Rejected):self.a.submit(AnchorCommitment(self.cp['merkle_root']),digest(self.cp))
        self.assertEqual(self.sent,[])
