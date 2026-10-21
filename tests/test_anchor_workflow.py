import copy
import unittest
from backend.domain import Rejected,digest
from backend.collaboration.rounds import CollaborationRounds
from backend.collaboration.telemetry import CollaborationTelemetry
from backend.collaboration.workflow import ReadonlyCrosscheck,verify_business
from backend.security.crypto_registry import AUDIT_SIGNER
import tests.test_anchor_collaboration as unit


class WorkflowAndTelemetryTests(unittest.TestCase):
    setUp=unit.RoundTests.setUp

    def new_round(self):
        self.state=self.rounds.start(self.task['task_id'],'demo',self.agents,k=2,
            deadline={'deadline_ns':1000,'telemetry_horizon_ns':2000},policy_id='1'*64,model_commitments=self.models)
        self.telemetry=CollaborationTelemetry(self.rounds)
        return self.state['round_id']

    def output(self,agent):return unit.RoundTests.output(self,agent)

    def test_late_telemetry_does_not_modify_closed_root(self):
        rid=self.new_round();self.rounds.accept(rid,'demo',self.output('finance'))
        late=self.output('wealth');self.now=2100;closed=self.rounds.close(rid,'demo')
        self.assertEqual(closed['record']['status'],'VOID')
        observed=self.telemetry.observe_late(rid,'demo',late)
        self.assertFalse(observed['formal_evidence_included'])
        self.assertEqual(self.rounds.close(rid,'demo'),closed)
        self.now=3000;history=self.telemetry.capture(rid,'demo')
        self.assertEqual(history.quorum(),(1100,True))

    def test_telemetry_not_available_before_horizon(self):
        rid=self.new_round();self.now=2100;self.rounds.close(rid,'demo')
        with self.assertRaises(Rejected):self.telemetry.capture(rid,'demo')

    def test_after_horizon_not_latency_completion(self):
        rid=self.new_round();late=self.output('wealth');self.now=3001
        with self.assertRaises(Rejected):self.telemetry.observe_late(rid,'demo',late)

    def test_unsigned_late_response_not_telemetry(self):
        rid=self.new_round();late=self.output('wealth');late['payload']['epoch']=99;self.now=2100
        with self.assertRaises(Rejected):self.telemetry.observe_late(rid,'demo',late)

    def test_telemetry_capture_idempotent_and_restart(self):
        rid=self.new_round();self.now=3000;self.rounds.close(rid,'demo')
        first=self.telemetry.capture(rid,'demo');second=CollaborationTelemetry(self.rounds).capture(rid,'demo')
        self.assertEqual(first,second);self.assertEqual(first.quorum(),(2000,False))

    def test_telemetry_reboot_is_not_latency_evidence(self):
        rid=self.new_round();self.now=3000;self.rounds.close(rid,'demo')
        fresh=CollaborationRounds(self.s,self.fixture,clock=lambda:self.now,boot_id='changed-boot')
        with self.assertRaises(Rejected):CollaborationTelemetry(fresh).capture(rid,'demo')

    def test_original_multi_agent_business_and_public_causal_proofs(self):
        workflow=ReadonlyCrosscheck(self.s,self.rounds);models={a:digest(a) for a in ('finance','wealth','transaction')}
        context=workflow.run('demo',account_id='acct-demo',month='2026-09',deadline={'deadline_ns':1000},
                              policy_id='1'*64,model_commitments=models)
        self.now=2000;bundle=self.rounds.export(context['state']['round_id'],'demo')
        proof=workflow.export_business(context,'demo');key=self.s.crypto_registry.resolve(AUDIT_SIGNER)['public_key']
        self.assertTrue(verify_business(proof,bundle,key,pinned_registry_hash=digest(proof['runtime_registry'])))
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        broken=copy.deepcopy(proof);broken['business']['result']['Balance:result']={'available':999999999}
        self.assertFalse(verify_business(broken,bundle,key,pinned_registry_hash=digest(proof['runtime_registry'])))

    def test_cross_user_read_cannot_run_under_collaboration(self):
        workflow=ReadonlyCrosscheck(self.s,self.rounds);models={a:digest(a) for a in ('finance','wealth','transaction')}
        with self.assertRaises(Rejected):
            workflow.run('other-user',account_id='acct-demo',month='2026-09',deadline={'deadline_ns':1000},
                         policy_id='1'*64,model_commitments=models)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
