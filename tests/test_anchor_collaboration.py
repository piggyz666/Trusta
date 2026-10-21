"""Deterministic protocol negatives. Fixtures never stand in for live WeID evidence."""
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from backend.config import Config
from backend.domain import Rejected, digest
from backend.service import TrustaService
from backend.collaboration.otc import (Observation, RoundTelemetry, DeadlinePolicy,
                                       AdaptiveOTC, km_survival)
from backend.collaboration.rounds import CollaborationRounds
from backend.security.did_key import from_public_key


class OTCTests(unittest.TestCase):
    def setUp(self):
        self.policy = DeadlinePolicy(1_000_000, 10_000_000, 5_000_000, 1, 1,
                                     (1_000_000, 2_000_000, 5_000_000, 10_000_000))
        self.controller = AdaptiveOTC(self.policy)

    def telemetry(self, seq=0, latency=2_000_000, valid=True, horizon=10_000_000):
        return RoundTelemetry(seq, 1, horizon, (Observation('finance', latency if valid else horizon,
                                                          'VALID' if valid else 'CENSORED'),), 'c')

    def test_empirical_objective(self):
        d = self.controller.select([self.telemetry()], next_sequence=1, configuration_hash='c')
        self.assertEqual(d['deadline_ns'], 2_000_000)
        self.assertEqual(d['model'], 'EMPIRICAL_KM')

    def test_future_data_rejected(self):
        with self.assertRaises(Rejected):
            self.controller.select([self.telemetry(1)], next_sequence=1, configuration_hash='c')

    def test_duplicate_history_rejected(self):
        with self.assertRaises(Rejected):
            self.controller.select([self.telemetry(), self.telemetry()], next_sequence=1, configuration_hash='c')

    def test_censoring_retains_void_mass(self):
        self.assertEqual(km_survival([(2, True), (10, False)], 5), .5)

    def test_tied_events_before_censors(self):
        self.assertEqual(km_survival([(2, True), (2, False)], 2), .5)

    def test_short_horizon_not_extrapolated(self):
        d = self.controller.select([self.telemetry(horizon=5_000_000)], next_sequence=1, configuration_hash='c')
        self.assertEqual(d['model'], 'SAFE_DEFAULT')

    def test_prior_configuration_not_reused(self):
        d = self.controller.select([self.telemetry()], next_sequence=1, configuration_hash='another')
        self.assertEqual(d['model'], 'SAFE_DEFAULT')

    def test_invalid_outputs_not_valid_latency(self):
        r = replace(self.telemetry(), observations=(Observation('finance', 1, 'INVALID'),))
        self.assertEqual(r.quorum(), (10_000_000, False))

    def test_malformed_censor_rejected(self):
        with self.assertRaises(Rejected):
            replace(self.telemetry(), observations=(Observation('finance', 1, 'CENSORED'),)).validate()

    def test_duplicate_agents_rejected(self):
        r = self.telemetry()
        with self.assertRaises(Rejected):
            replace(r, observations=r.observations * 2).validate()

    def test_missing_candidate_boundaries_rejected(self):
        with self.assertRaises(Rejected):
            AdaptiveOTC(replace(self.policy, candidates_ns=(2_000_000,)))

    def test_bool_time_rejected(self):
        with self.assertRaises(Rejected):
            AdaptiveOTC(replace(self.policy, minimum_ns=True))

    def test_rolling_window_forgets_old_drift(self):
        c = AdaptiveOTC(replace(self.policy, history_window=1))
        d = c.select([self.telemetry(), self.telemetry(1, 5_000_000)], next_sequence=2, configuration_hash='c')
        self.assertEqual(d['deadline_ns'], 5_000_000)
        self.assertEqual(d['history_sequences'], [1])

    def test_safe_default_clamp(self):
        d = self.controller.select([], next_sequence=1, configuration_hash='c', previous_deadline=99_000_000)
        self.assertEqual(d['deadline_ns'], self.policy.maximum_ns)

    def test_p95_all_censored_clamps(self):
        self.assertEqual(self.controller.p95([self.telemetry(valid=False)]), self.policy.maximum_ns)

    def test_offline_oracle_labeled(self):
        self.assertEqual(self.controller.oracle_offline([self.telemetry()])['scope'], 'OFFLINE_ORACLE_ONLY')


class DevelopmentAdmissionFixture:
    """Unit fixture, NEVER used by an integration command or formal mode."""
    def __init__(self, service):
        self.s = service

    def check_current(self, agent, member, policy_id):
        self.s.crypto_registry.require_active(agent)

    def check_current_in(self, conn, agent, member, policy_id):
        self.s.crypto_registry.resolve_in(agent,member['key_id'],conn)

    def snapshot(self, agents, policy_id, models):
        members = {}
        for a in agents:
            i = self.s.crypto_registry.resolve(a)
            members[a] = {name: i[name] for name in ('key_id', 'capability_hash', 'public_key')}
            members[a].update(did=from_public_key(i['public_key']), epoch=1, model_commitment=models[a])
        return {'scope': 'UNIT_FIXTURE_NOT_WEID_OR_M1', 'members': members, 'policy_id': policy_id}


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.directory.name) / 'bank.db'),
                                    signing_key=b'anchor-unit-isolated-test-root-key',
                                    communication_version=3, banking_version=5))
        self.task = self.s.create_task('分析2026-09消费')
        self.now = 1000
        self.fixture = DevelopmentAdmissionFixture(self.s)
        self.rounds = CollaborationRounds(self.s, self.fixture, clock=lambda: self.now, boot_id='unit-boot')
        self.agents = ['finance', 'wealth']
        self.models = {a: digest({'source': a}) for a in self.agents}
        self.state = self.rounds.start(self.task['task_id'], 'demo', self.agents, k=2,
                                       deadline={'deadline_ns': 1000}, policy_id='1'*64,
                                       model_commitments=self.models)

    def output(self, agent='finance', *, facts='2'*64, verdict='CONSISTENT'):
        return self.rounds.sign(self.state['round_id'], 'demo', agent,
                               {'verdict': verdict, 'facts_hash': facts, 'result_hash': '3'*64,
                                'capability': 'finance.summary' if agent == 'finance' else 'wealth.recommend'})

    def close(self):
        self.now = 2000
        return self.rounds.close(self.state['round_id'], 'demo')

    def test_quorum_does_not_close_early(self):
        for a in self.agents:
            self.rounds.accept(self.state['round_id'], 'demo', self.output(a))
        with self.assertRaises(Rejected):
            self.rounds.close(self.state['round_id'], 'demo')
        self.assertEqual(self.close()['record']['status'], 'COMPLETED')

    def test_insufficient_evidence_void(self):
        self.rounds.accept(self.state['round_id'], 'demo', self.output())
        result = self.close()
        self.assertEqual(result['record']['status'], 'VOID')
        self.assertIsNone(result['record']['verdict'])

    def test_late_output_rejected(self):
        b = self.output()
        self.now = 2001
        with self.assertRaises(Rejected):
            self.rounds.accept(self.state['round_id'], 'demo', b)

    def test_exact_boundary_accepted(self):
        b = self.output()
        self.now = 2000
        self.assertEqual(self.rounds.accept(self.state['round_id'], 'demo', b)['status'], 'ACCEPTED')

    def test_exact_retransmission_idempotent(self):
        b = self.output()
        self.rounds.accept(self.state['round_id'], 'demo', b)
        self.assertEqual(self.rounds.accept(self.state['round_id'], 'demo', b)['status'], 'DUPLICATE')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM collaboration_outputs')), 1)

    def test_equivocation_cannot_replace_first(self):
        self.rounds.accept(self.state['round_id'], 'demo', self.output())
        r = self.rounds.accept(self.state['round_id'], 'demo', self.output(facts='4'*64))
        self.assertEqual(r['status'], 'EQUIVOCATION_EXCLUDED')
        self.assertEqual(self.close()['evidence'][0]['payload']['assessment']['facts_hash'], '2'*64)

    def test_tampered_payload_rejected_without_nonce(self):
        b = self.output()
        b['payload']['epoch'] = 9
        with self.assertRaises(Rejected):
            self.rounds.accept(self.state['round_id'], 'demo', b)
        self.assertFalse(self.s.db.one('SELECT nonce FROM replay_nonces WHERE nonce=?', (b['message']['nonce'],)))

    def test_cross_owner_denied(self):
        with self.assertRaises(Rejected):
            self.rounds.accept(self.state['round_id'], 'other-user', self.output())

    def test_signed_wrong_epoch_denied(self):
        b = self.output()
        b['payload']['epoch'] = 2
        b['message'] = self.s.messages.sign('finance', 'supervisor', self.task['task_id'],
                                          'anchor-round:' + self.state['round_id'], 'READ_ONLY_REPORT',
                                          'anchor:' + self.state['round_id'] + ':finance', b['payload'],
                                          payload_version=self.state['task_revision'])
        with self.assertRaises(Rejected):
            self.rounds.accept(self.state['round_id'], 'demo', b)

    def test_conflicting_facts_require_review(self):
        self.rounds.accept(self.state['round_id'], 'demo', self.output())
        self.rounds.accept(self.state['round_id'], 'demo', self.output('wealth', facts='4'*64))
        self.assertEqual(self.close()['record']['verdict'], 'REVIEW_REQUIRED')

    def test_restart_keeps_accepted_state(self):
        b = self.output()
        self.rounds.accept(self.state['round_id'], 'demo', b)
        self.rounds = CollaborationRounds(self.s, self.fixture, clock=lambda: self.now, boot_id='unit-boot')
        self.assertEqual(self.rounds.accept(self.state['round_id'], 'demo', b)['status'], 'DUPLICATE')

    def test_reboot_does_not_extend_window(self):
        self.rounds = CollaborationRounds(self.s, self.fixture, clock=lambda: 0, boot_id='other-boot')
        self.assertEqual(self.rounds.close(self.state['round_id'], 'demo')['record']['status'], 'VOID')

    def test_closed_record_immutable(self):
        first = self.close()
        self.now += 9999
        self.assertEqual(self.rounds.close(self.state['round_id'], 'demo'), first)
        with self.assertRaises(Exception), self.s.db.transaction() as conn:
            conn.execute('UPDATE collaboration_closed SET body=?', ('{}',))

    def test_financial_action_schema_rejected(self):
        with self.assertRaises(Rejected):
            self.rounds.sign(self.state['round_id'], 'demo', 'finance', {'action': 'transfer.execute'})
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'), [])

    def test_unadmitted_agent_rejected(self):
        with self.assertRaises(Rejected):
            self.output('transaction')

    def test_state_tampering_denied(self):
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE collaboration_rounds SET body=?', ('{}',))
        with self.assertRaises(Rejected):
            self.close()
