"""Recorded REAL FISCO evidence replay, with mutations; no mock live success."""
import copy
import json
from pathlib import Path
import unittest
import tempfile
import shutil
from backend.domain import Rejected
from backend.collaboration.fisco import FiscoVerifier

RUN=Path('data/anchor-integration/acceptance/ee75a721-606d-40a8-93dc-b1129cb44409')


class RecordedFiscoVerificationTests(unittest.TestCase):
    def setUp(self):
        self.bundle=json.loads((RUN/'round-proof.json').read_text(encoding='utf-8'))
        chain=json.loads((RUN/'fisco-proof.json').read_text(encoding='utf-8'))
        self.original=chain['independent_rpc'][0];self.receipt=copy.deepcopy(self.original['receipt'])
        self.record=self.bundle['record'];self.tx=self.original['transaction_hash']
        self.v=FiscoVerifier.__new__(FiscoVerifier);self.v.url='RECORDED_REAL_FISCO_UNIT_REPLAY'
        self.v.address=self.receipt['to'].lower();self.v.publisher=self.receipt['from'].lower()
        self.v.artifact=json.loads(Path('data/anchor-integration/weidentity-lab/conf/roundanchor-artifact.json').read_text())
        self.code='0x'+self.v.artifact['runtime'];self.readback=list(self.original['readback'])
        def rpc(method,params):
            if method=='getTransactionReceipt':return self.receipt
            if method=='getBlockHashByNumber':return self.original['block_hash']
            if method=='getCode':return self.code
            if method=='getSealerList':return self.original['sealers']
            raise AssertionError('unexpected replay method')
        self.v.rpc=rpc
        self.v.call=lambda signature,*args,**kwargs:((self.v.publisher,) if signature=='publisher()' else None)
        self.v.read=lambda record:self.readback

    def test_real_recorded_receipt_event_readback_verifies(self):
        self.assertTrue(self.v.verify(self.record,self.tx)['verified'])

    def test_reorged_block_rejected(self):
        self.receipt['blockHash']='0x'+'1'*64
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_failed_fisco_status_rejected(self):
        self.receipt['status']='0x1'
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_other_publisher_rejected(self):
        self.receipt['from']='0x'+'2'*40
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_changed_calldata_rejected(self):
        self.receipt['input']=self.receipt['input'][:-2]+'ff'
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_changed_event_rejected(self):
        self.receipt['logs'][0]['data']=self.receipt['logs'][0]['data'][:-2]+'ff'
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_changed_chain_readback_rejected(self):
        self.readback[0]='0'*64
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_changed_runtime_code_rejected(self):
        self.code='0x00'
        with self.assertRaises(Rejected):self.v.verify(self.record,self.tx)

    def test_rpc_scope_cannot_select_public_chain(self):
        with self.assertRaises(Rejected):FiscoVerifier(8545,artifact='',artifact_sha256='',address='',publisher='')

    def test_actual_recorded_deployment_verifies(self):
        latest=Path('data/anchor-integration/acceptance/504fc23a-f8b2-4492-b0da-c65997fb6859/independent-verification.json')
        deployment=json.loads(latest.read_text())['rpc_checks'][0]['deployment']['receipt']
        self.receipt=deployment;self.original={'block_hash':deployment['blockHash']}
        self.assertTrue(self.v.verify_deployment(deployment['transactionHash'])['verified'])

    def test_wrong_creation_bytecode_rejected(self):
        latest=Path('data/anchor-integration/acceptance/504fc23a-f8b2-4492-b0da-c65997fb6859/independent-verification.json')
        deployment=json.loads(latest.read_text())['rpc_checks'][0]['deployment']['receipt']
        self.receipt=deployment;self.original={'block_hash':deployment['blockHash']};deployment['input']='0x00'
        with self.assertRaises(Rejected):self.v.verify_deployment(deployment['transactionHash'])


class IndependentVerifierTrustPinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.original=Path('data/anchor-integration/acceptance/504fc23a-f8b2-4492-b0da-c65997fb6859')
        for name in ('trust-pins.json','round-proof.json','fisco-proof.json'):
            shutil.copyfile(self.original/name,self.root/name)
        self.args={k.replace('-','_'):v for k,v in json.loads((self.original/'verification-arguments-v2.json').read_text()).items()}

    def reject_pin_mutation(self,field,value):
        from scripts.verify_anchor_collaboration import verify
        pins=json.loads((self.root/'trust-pins.json').read_text());pins[field]=value
        (self.root/'trust-pins.json').write_text(json.dumps(pins),encoding='utf-8')
        with self.assertRaisesRegex(Rejected,'ANCHOR_INDEPENDENT_TRUST_PIN_MISMATCH'):
            verify(self.root,**self.args)

    def test_self_supplied_contract_cannot_replace_pinned_contract(self):
        self.reject_pin_mutation('contract','0x'+'1'*40)

    def test_self_supplied_publisher_cannot_replace_pinned_publisher(self):
        self.reject_pin_mutation('publisher','0x'+'2'*40)
