"""Execute actual compiled consortium contract bytecode in local EVM negatives."""
import json
import hashlib
import unittest
from pathlib import Path
from eth_tester import EthereumTester
from eth_abi import encode,decode
from eth_utils import keccak


class CollaborationContractExecutionTests(unittest.TestCase):
    def setUp(self):
        self.evm=EthereumTester();self.sender=self.evm.get_accounts()[0]
        artifact=json.loads(Path('contracts/artifacts/CollaborationAnchor.json').read_text())
        source=Path('contracts/CollaborationAnchor.sol').read_text(encoding='utf-8')
        self.assertEqual(artifact['source_sha256'], hashlib.sha256(source.encode('utf-8')).hexdigest(), 'Recompile the local EVM fixture after changing the contract')
        tx=self.evm.send_transaction({'from':self.sender,'gas':3000000,'data':'0x'+artifact['bytecode']})
        self.address=self.evm.get_transaction_receipt(tx)['contract_address']
        self.args=[bytes([n])*32 for n in range(1,6)]+[3,2]

    def send(self,args=None,sender=None):
        data=keccak(text='commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint256)')[:4]+encode(['bytes32']*5+['uint256']*2,args or self.args)
        tx=self.evm.send_transaction({'from':sender or self.sender,'to':self.address,'gas':500000,'data':'0x'+data.hex()})
        r=self.evm.get_transaction_receipt(tx)
        if r['status']!=1:raise ValueError('contract rejected')
        return r

    def call(self,name,types=(),values=(),returns=()):
        data=keccak(text=name)[:4]+encode(types,values)
        result=self.evm.call({'from':self.sender,'to':self.address,'gas':500000,'data':'0x'+data.hex()})
        return decode(returns,bytes.fromhex(result[2:]))

    def test_real_commit_and_readback(self):
        r=self.send();self.assertEqual(r['status'],1)
        self.assertEqual(list(self.call('get(bytes32)',['bytes32'],[self.args[0]],['bytes32']*4+['uint256']*2)),self.args[1:])

    def test_publisher_bound_to_deployer(self):self.assertEqual(self.call('publisher()',returns=['address'])[0].lower(),self.sender.lower())

    def test_other_signer_cannot_commit(self):
        with self.assertRaises(Exception):self.send(sender=self.evm.get_accounts()[1])

    def test_exact_commit_retry_idempotent_no_new_event(self):
        self.send();r=self.send();self.assertEqual(len(r['logs']),0)

    def test_conflicting_record_rejected(self):
        self.send();args=list(self.args);args[1]=b'x'*32
        with self.assertRaises(Exception):self.send(args)

    def test_invalid_quorum_rejected(self):
        args=list(self.args);args[-1]=0
        with self.assertRaises(Exception):self.send(args)

    def test_empty_round_id_rejected(self):
        args=list(self.args);args[0]=b'\0'*32
        with self.assertRaises(Exception):self.send(args)

    def test_oversized_member_count_rejected(self):
        args=list(self.args);args[-2]=7
        with self.assertRaises(Exception):self.send(args)

    def test_changed_root_rejected_on_retry(self):
        self.send();args=list(self.args);args[2]=b'y'*32
        with self.assertRaises(Exception):self.send(args)

    def test_changed_verdict_rejected_on_retry(self):
        self.send();args=list(self.args);args[4]=b'z'*32
        with self.assertRaises(Exception):self.send(args)
