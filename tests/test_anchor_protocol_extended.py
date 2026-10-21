"""Attack/fault tests; unit fixtures do not claim live consortium verification."""
import copy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest
import numpy as np
from backend.domain import Rejected,digest
from backend.collaboration.otc import AdaptiveOTC,DeadlinePolicy,RoundTelemetry,Observation
from backend.collaboration.otc_model import ResponsivePopulation
from backend.collaboration.verify import verify_round
from backend.security.crypto_registry import AUDIT_SIGNER
import tests.test_anchor_collaboration as unit


class ExtendedRoundTests(unittest.TestCase):
    setUp=unit.RoundTests.setUp
    output=unit.RoundTests.output
    close=unit.RoundTests.close

    def export(self):
        self.now=2000
        return self.rounds.export(self.state['round_id'],'demo')

    def valid(self,bundle):
        return verify_round(bundle,pinned_snapshot=self.state['snapshot'],
            pinned_audit_key=self.s.crypto_registry.resolve(AUDIT_SIGNER)['public_key'],
            expected_record_hash=digest(bundle['record']))

    def completed(self):
        for agent in self.agents:self.rounds.accept(self.state['round_id'],'demo',self.output(agent))
        return self.export()

    def test_public_only_proof_verifies(self):self.assertTrue(self.valid(self.completed()))

    def test_altered_evidence_rejected(self):
        b=self.completed();b['evidence'][0]['payload']['assessment']['result_hash']='a'*64
        self.assertFalse(self.valid(b))

    def test_altered_aggregation_rejected(self):
        b=self.completed();b['record']['verdict']='REVIEW_REQUIRED'
        self.assertFalse(self.valid(b))

    def test_wrong_audit_key_rejected(self):
        b=self.completed()
        self.assertFalse(verify_round(b,pinned_snapshot=self.state['snapshot'],
            pinned_audit_key=self.s.crypto_registry.resolve('wealth')['public_key'],expected_record_hash=digest(b['record'])))

    def test_snapshot_self_substitution_rejected(self):
        b=self.completed();b['snapshot']['members']['finance']['epoch']=2
        self.assertFalse(self.valid(b))

    def test_missing_merkle_path_rejected(self):
        b=self.completed();b['inclusion_proofs'].pop()
        self.assertFalse(self.valid(b))

    def test_duplicate_member_evidence_rejected(self):
        b=self.completed();b['evidence'][1]=copy.deepcopy(b['evidence'][0])
        self.assertFalse(self.valid(b))

    def test_void_with_quorum_and_normal_deadline_rejected(self):
        b=self.completed();b['record']['status']='VOID';b['record']['verdict']=None
        self.assertFalse(self.valid(b))

    def test_equivocation_disk_growth_bounded(self):
        self.rounds.accept(self.state['round_id'],'demo',self.output())
        for i in range(5):self.rounds.accept(self.state['round_id'],'demo',self.output(facts=digest(i)))
        self.assertEqual(len(self.s.db.rows('SELECT * FROM collaboration_conflicts')),1)

    def test_concurrent_acceptance_has_unique_receipt_sequence(self):
        outputs=[self.output(a) for a in self.agents]
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(lambda b:self.rounds.accept(self.state['round_id'],'demo',b),outputs))
        self.assertEqual({v['receipt_seq'] for v in values},{1,2})
        self.assertTrue(self.valid(self.export()))

    def test_database_failure_rolls_back_nonce_and_output(self):
        b=self.output()
        with self.s.db.transaction() as conn:
            conn.execute("CREATE TRIGGER force_output_failure BEFORE INSERT ON collaboration_outputs BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(Exception):self.rounds.accept(self.state['round_id'],'demo',b)
        self.assertEqual(self.s.db.rows('SELECT * FROM collaboration_outputs'),[])
        self.assertIsNone(self.s.db.one('SELECT nonce FROM replay_nonces WHERE nonce=?',(b['message']['nonce'],)))
        with self.s.db.transaction() as conn:conn.execute('DROP TRIGGER force_output_failure')
        self.assertEqual(self.rounds.accept(self.state['round_id'],'demo',b)['status'],'ACCEPTED')

    def test_live_key_rotation_invalidates_frozen_membership(self):
        b=self.output();self.s.crypto_registry.rotate('finance',0)
        with self.assertRaises(Rejected):self.rounds.accept(self.state['round_id'],'demo',b)

    def test_forged_message_rejected_before_external_identity_call(self):
        b=self.output();b['payload']['epoch']=9
        def forbidden(*args):raise AssertionError('forged input reached external identity service')
        self.fixture.check_current=forbidden
        with self.assertRaises(Rejected):self.rounds.accept(self.state['round_id'],'demo',b)


class ParametricOTCTests(unittest.TestCase):
    def setUp(self):
        self.policy=DeadlinePolicy(1000000,1000000000,500000000,1,1,
                                  (1000000,100000000,300000000,1000000000),history_window=100)
        self.spec=ResponsivePopulation('h',('finance','wealth'),1,bootstrap_samples=19)
        rng=np.random.default_rng(6721)
        self.history=[]
        for seq in range(80):
            values=rng.lognormal(-2.,.4,2)*1e9
            self.history.append(RoundTelemetry(seq,1,1000000000,tuple(Observation(agent,min(1000000000,int(v)),
                'VALID' if v<=1000000000 else 'CENSORED') for agent,v in zip(self.spec.responsive_agents,values)),'h'))

    def select(self,history=None,spec=None):
        return AdaptiveOTC(self.policy,responsive_population=spec or self.spec).select(
            self.history if history is None else history,next_sequence=100,configuration_hash='h')

    def test_valid_specialization_fits_actual_mle_and_bootstrap(self):
        result=self.select();self.assertEqual(result['model'],'CENSORED_LOGNORMAL_WITHHOLDING')
        self.assertTrue(self.policy.minimum_ns<=result['deadline_ns']<=self.policy.maximum_ns)
        self.assertTrue(result['bootstrap_pvalue']>=.05)

    def test_insufficient_rounds_uses_km(self):
        r=self.select(self.history[:5]);self.assertEqual(r['model'],'EMPIRICAL_KM')
        self.assertEqual(r['parametric_gate']['reason'],'INSUFFICIENT_ROUNDS')

    def test_terminal_failure_uses_km(self):
        history=list(self.history);history[0]=replace(history[0],observations=(Observation('finance',1,'ERROR'),history[0].observations[1]))
        r=self.select(history);self.assertEqual(r['parametric_gate']['reason'],'NON_LATENCY_FAILURE')

    def test_population_switch_uses_km(self):
        r=self.select(spec=replace(self.spec,configuration_hash='different'))
        self.assertEqual(r['parametric_gate']['reason'],'SPECIALIZATION_INAPPLICABLE')

    def test_material_dependence_uses_km(self):
        history=[replace(r,observations=(r.observations[0],replace(r.observations[0],agent='wealth'))) for r in self.history]
        r=self.select(history);self.assertEqual(r['parametric_gate']['reason'],'DEPENDENCE_DETECTED')

    def test_drift_uses_km(self):
        history=[r if i<40 else replace(r,observations=tuple(replace(o,elapsed_ns=min(o.elapsed_ns*3,900000000)) for o in r.observations))
                 for i,r in enumerate(self.history)]
        r=self.select(history);self.assertEqual(r['parametric_gate']['reason'],'DRIFT_DETECTED')

    def test_censor_minimum_not_fabricated(self):
        r=self.select(spec=replace(self.spec,minimum_censored=1000))
        self.assertEqual(r['parametric_gate']['reason'],'INSUFFICIENT_OBSERVATIONS')

    def test_invalid_population_parameters_rejected(self):
        with self.assertRaises(Rejected):AdaptiveOTC(self.policy,responsive_population=replace(self.spec,bootstrap_samples=1))
