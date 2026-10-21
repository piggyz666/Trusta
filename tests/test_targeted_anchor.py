import copy
import hashlib
import tempfile
import unittest
from eth_utils import keccak
from backend.audit_trust.independent_anchor import verify_public_anchor,verify_two_rpcs
from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
from backend.domain import Rejected,digest
from tests.phase6_helpers import service


class PublicRPC:
    """Synthetic RPC, explicitly NOT external-chain proof."""
    def __init__(self,cp,contract,publisher,txhash):
        self.chain=11155111;self.head=12;self.txhash=txhash;self.code='0x60006000';self.publisher=publisher
        self.calls=[];self.receipts=0;self.reorg=False;self.read_bad=False;self.missing=False
        self.block={'number':'0xa','hash':'0x'+'a'*64,'transactions':[txhash]}
        cph=digest(cp);ident=hashlib.sha256(cp['checkpoint_id'].encode()).hexdigest()
        self.data='0x'+cp['merkle_root']+ident+f"{cp['last_event_sequence']:064x}"+f'{1:064x}'
        self.tx={'hash':txhash,'chainId':hex(self.chain),'to':contract,'from':publisher,'value':'0x0',
            'input':'0x'+ExternalBlockchainAnchor.selector('commit(bytes32,bytes32,bytes32,uint64,uint32)')+cph+self.data[2:],
            'blockNumber':'0xa','blockHash':self.block['hash']}
        self.receipt={'transactionHash':txhash,'to':contract,'status':'0x1','blockNumber':'0xa','blockHash':self.block['hash'],
            'logs':[{'address':contract,'topics':['0x'+keccak(text='Committed(bytes32,bytes32,bytes32,uint64,uint32)').hex(),'0x'+cph],
                'data':self.data,'transactionHash':txhash,'blockHash':self.block['hash'],'blockNumber':'0xa','removed':False}]}
    def call(self,method,params):
        self.calls.append(method)
        if method=='eth_chainId':return hex(self.chain)
        if method=='eth_getCode':return self.code
        if method=='eth_blockNumber':return hex(self.head)
        if method=='eth_getBlockByNumber':return copy.deepcopy(self.block)
        if method=='eth_getTransactionByHash':return None if self.missing else copy.deepcopy(self.tx)
        if method=='eth_getTransactionReceipt':
            self.receipts+=1
            return self.receipt|({'blockHash':'0x'+'b'*64} if self.reorg and self.receipts>1 else {})
        if method=='eth_call':
            if params[0]['data']=='0x'+ExternalBlockchainAnchor.selector('publisher()'):return '0x'+'0'*24+self.publisher[2:]
            return '0x'+'0'*256 if self.read_bad else self.data
        raise AssertionError('Write or unsupported RPC: '+method)


class IndependentAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);s=service(self.tmp.name)
        self.cp=s.checkpoints.generate_checkpoint();self.key,_=s.checkpoints.public_key(self.cp)
        self.contract='0x'+'4'*40;self.publisher='0x'+'5'*40;self.tx='0x'+'f'*64
        self.rpc=PublicRPC(self.cp,self.contract,self.publisher,self.tx)
        self.kw=dict(checkpoint=self.cp,trusted_public_key=self.key,contract=self.contract,publisher=self.publisher,tx_hash=self.tx)
    def verify(self):return verify_public_anchor(self.rpc,**self.kw)
    def test_valid_public_evidence_needs_no_original_database_or_wallet(self):
        r=self.verify();self.assertTrue(r['external_blockchain_verified']);self.assertEqual(r['confirmations'],3)
        self.assertFalse(any('send' in m.lower() for m in self.rpc.calls))
    def test_pinned_checkpoint_signature_required(self):
        self.cp['merkle_root']='0'*64;self.assertFalse(self.verify()['external_blockchain_verified']);self.assertEqual(self.rpc.calls,[])
    def test_wrong_pinned_public_key_rejected(self):
        self.kw['trusted_public_key']='not a public key';self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_mainnet_and_no_code_rejected(self):
        self.rpc.chain=1;self.assertFalse(self.verify()['external_blockchain_verified'])
        self.rpc.chain=11155111;self.rpc.code='0x';self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_wrong_contract_publisher_or_nonzero_value_rejected(self):
        for key,value in [('to','0x'+'6'*40),('from','0x'+'6'*40),('value','0x1')]:
            before=self.rpc.tx[key];self.rpc.tx[key]=value;self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.tx[key]=before
        self.rpc.publisher='0x'+'6'*40;self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_missing_transaction_failed_receipt_and_changed_calldata(self):
        self.rpc.missing=True;self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.missing=False
        self.rpc.receipt['status']='0x0';self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.receipt['status']='0x1'
        self.rpc.tx['input']='0x1234';self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_event_root_identifier_version_and_duplicate_rejected(self):
        log=self.rpc.receipt['logs'][0];before=log['data']
        for index in (2,66,194):
            replacement='0' if before[index]!='0' else '1'
            log['data']=before[:index]+replacement+before[index+1:];self.assertFalse(self.verify()['external_blockchain_verified'])
        log['data']=before;self.rpc.receipt['logs'].append(copy.deepcopy(log));self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_readback_confirmations_and_reorg_rejected(self):
        self.rpc.read_bad=True;self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.read_bad=False
        self.rpc.head=10;self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.head=12
        self.rpc.reorg=True;self.rpc.receipts=0;self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_canonical_membership_and_event_block_binding_required(self):
        self.rpc.block['transactions']=[];self.assertFalse(self.verify()['external_blockchain_verified']);self.rpc.block['transactions']=[self.tx]
        self.rpc.receipt['logs'][0]['blockNumber']='0xb';self.assertFalse(self.verify()['external_blockchain_verified'])
    def test_code_hash_and_dual_rpc_consistency(self):
        good='0x'+keccak(hexstr=self.rpc.code).hex();self.kw['code_hash']=good;self.assertTrue(self.verify()['external_blockchain_verified'])
        self.kw['code_hash']='0x'+'0'*64;self.assertFalse(self.verify()['external_blockchain_verified']);self.kw.pop('code_hash')
        other=PublicRPC(self.cp,self.contract,self.publisher,self.tx)
        self.assertTrue(verify_two_rpcs(self.rpc,other,**self.kw)['external_blockchain_verified'])
        other.block['hash']='0x'+'b'*64;self.assertFalse(verify_two_rpcs(self.rpc,other,**self.kw)['external_blockchain_verified'])
    def test_malformed_config_rejected(self):
        for key,value in [('contract','bad'),('tx_hash','fake'),('confirmations',True),('confirmations',0)]:
            with self.assertRaises(Rejected):verify_public_anchor(self.rpc,**(self.kw|{key:value}))

if __name__=='__main__':unittest.main()
