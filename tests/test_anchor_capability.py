import copy
import hashlib
from pathlib import Path
import unittest
import tempfile
import shutil
from backend.collaboration.capability_proof import (STATEMENT, CapabilityProofEngine,
                                                   build_witness, context_signals, statement_hash)
from backend.domain import Rejected


def statement():
    return {'version': STATEMENT, 'protocol_domain': 'trusta-anchor-test-instance',
            'chain_id': 101, 'did': 'did:weid:101:0x'+'1'*40, 'message_key_hash': '2'*64,
            'model_commitment': '3'*64, 'benchmark_root': '4'*64, 'evaluator_version': '5'*64,
            'threshold': 800000, 'policy_id': '6'*64, 'epoch': 1, 'score_encoding': 'anchor-f1-micro-v1'}


class CapabilityContractTests(unittest.TestCase):
    def witness(self, **values):
        return build_witness(statement(), **({'tp':19,'fp':0,'fn':1,'prediction_hash':'7'*64} | values))

    def test_fixed_point_floor(self):
        witness, public = self.witness()
        self.assertEqual(witness['score'], 974358)
        self.assertEqual(len(public), 33)

    def test_legacy_banking_proof_not_m1(self):
        s = statement();s['version'] = 'BANKING_ELIGIBILITY_V1'
        with self.assertRaises(Rejected):statement_hash(s)

    def test_counts_cannot_be_boolean(self):
        with self.assertRaises(Rejected):self.witness(tp=True)

    def test_negative_count(self):
        with self.assertRaises(Rejected):self.witness(fn=-1)

    def test_count_overflow(self):
        with self.assertRaises(Rejected):self.witness(tp=2**32)

    def test_zero_denominator(self):
        with self.assertRaises(Rejected):self.witness(tp=0,fp=0,fn=0)

    def test_below_threshold(self):
        with self.assertRaises(Rejected):self.witness(tp=1,fp=9,fn=1)

    def test_random_blind_differs(self):
        a, _ = self.witness();b, _ = self.witness()
        self.assertNotEqual(a['scoreCommitment'], b['scoreCommitment'])

    def test_each_context_field_bound(self):
        base = statement()
        for name in ('did', 'message_key_hash', 'model_commitment', 'benchmark_root',
                     'evaluator_version', 'policy_id', 'epoch', 'chain_id', 'protocol_domain'):
            changed = dict(base)
            changed[name] = changed[name]+1 if type(changed[name]) is int else ('8'*64 if name.endswith(('hash','commitment','root','version','id')) and name not in {'did'} else changed[name]+'x')
            with self.subTest(field=name):
                self.assertNotEqual(context_signals(changed), context_signals(base))

    def test_prediction_transcript_bound(self):
        a, _ = self.witness();b, _ = self.witness(prediction_hash='9'*64)
        self.assertNotEqual(a['evaluationTranscript'], b['evaluationTranscript'])

    def test_extra_statement_field_rejected(self):
        with self.assertRaises(Rejected):statement_hash(statement()|{'self_granted':True})


class ActualGroth16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]/'data/anchor-integration/m1-artifacts'
        pin = hashlib.sha256((cls.root/'manifest.json').read_bytes()).hexdigest()
        cls.engine = CapabilityProofEngine(cls.root,pin)
        cls.bundle = cls.engine.prove(statement(), {'tp':19,'fp':0,'fn':1,'prediction_hash':'7'*64})

    def test_actual_proof_verifies(self):
        result = self.engine.verify(self.bundle, statement(), self.bundle['public_signals'])
        self.assertTrue(result['verified']);self.assertFalse(result['financial_authority'])

    def test_wrong_epoch_rejected(self):
        with self.assertRaises(Rejected):
            self.engine.verify(self.bundle, statement()|{'epoch':2}, self.bundle['public_signals'])

    def test_wrong_verification_key_pin_rejected(self):
        with self.assertRaises(Rejected):CapabilityProofEngine(self.root,'0'*64)

    def test_wrong_public_score_commitment_rejected_by_groth16(self):
        bundle = copy.deepcopy(self.bundle)
        bundle['public_signals'][24] = str(int(bundle['public_signals'][24])+1)
        with self.assertRaises(Rejected):
            self.engine.verify(bundle,statement(),bundle['public_signals'])

    def test_forged_proof_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        bundle['proof']['pi_a'][0] = '1'
        with self.assertRaises(Rejected):
            self.engine.verify(bundle,statement(),bundle['public_signals'])

    def test_production_claim_rejected(self):
        with self.assertRaises(Rejected):
            self.engine.verify(self.bundle|{'production_ready':True},statement(),self.bundle['public_signals'])

    def test_independent_verifier_needs_only_manifest_and_public_vk(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('manifest.json','verification_key.json'):shutil.copyfile(self.root/name,Path(directory)/name)
            verifier=CapabilityProofEngine(directory,self.engine.pin,verification_only=True)
            self.assertTrue(verifier.verify(self.bundle,statement(),self.bundle['public_signals'])['verified'])

    def test_verification_only_engine_refuses_proving(self):
        verifier=CapabilityProofEngine(self.root,self.engine.pin,verification_only=True)
        with self.assertRaises(Rejected):verifier.prove(statement(),{'tp':20,'fp':0,'fn':0,'prediction_hash':'7'*64})
