"""Boundary regressions; no external credentials or financial network writes."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.phase6_helpers import service, seed_participants
from backend.domain import Rejected, digest
from backend.llm.grounded_request import normalize_request
from backend.runtime.taskgraph import node


class ConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=service(self.temp.name)

    def test_aa_can_compose_with_analysis_without_participant_debit(self):
        seed_participants(self.s)
        plan=normalize_request(self.s,'AA 19元参与人alice,bob，然后分析2026-09消费','acct-demo')
        self.assertEqual(plan['graph']['nodes'][0]['capability'],'split.request')
        task=self.s.graphs.create('demo','collection then analysis',plan['graph'],plan['inputs'])
        self.assertEqual(task['status'],'COMPLETED')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        bill=task['graph_state']['outputs']['S0:result']
        self.assertEqual(bill['total_amount'],1900)

    def test_split_prepare_two_service_instances_is_single_proposal(self):
        seed_participants(self.s);other=service(self.temp.name)
        bill=self.s.splits.create('demo','acct-demo',2100,['alice','bob'])
        self.s.splits.publish(bill['id'],'demo')
        entered=threading.Event();release=threading.Event()
        original=self.s.create_normalized_task
        def blocked(*a,**kw):
            entered.set();self.assertTrue(release.wait(5));return original(*a,**kw)
        with patch.object(self.s,'create_normalized_task',blocked),ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.s.splits.prepare_payment,bill['id'],'alice','acct-alice')
            self.assertTrue(entered.wait(5))
            try:
                try: second=other.splits.prepare_payment(bill['id'],'alice','acct-alice')
                except Rejected as exc:
                    self.assertEqual(exc.code,'GRAPH_WORKER_BUSY');second=None
            finally: release.set()
            task=first.result(5)
        second=second or other.splits.prepare_payment(bill['id'],'alice','acct-alice')
        self.assertEqual(task['task_id'],second['task_id'])
        rows=self.s.db.rows('SELECT body FROM tasks')
        self.assertEqual(sum(json.loads(r['body'])['user_goal'].startswith('AA ') for r in rows),1)

    def test_starting_second_service_does_not_recover_a_live_graph(self):
        entered=threading.Event();release=threading.Event()
        original=self.s.graphs._semantic
        def blocked(n,t,state):
            if n['node_type']=='READ':
                entered.set();self.assertTrue(release.wait(10))
            return original(n,t,state)
        graph={'schema_version':1,'nodes':[
            node('A','READ','finance','accounts.read',parameters={'account_id':'input:a'}),node('B','READ','service','subscriptions.read'),
            node('R','REPORT','supervisor','graph.report',['A','B'])]}
        with patch.object(self.s.graphs,'_semantic',blocked),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.s.graphs.create,'demo','parallel reads',graph,{'input:a':'acct-demo'})
            self.assertTrue(entered.wait(5))
            try:
                other=service(self.temp.name)
                current=other.blackboard.list('demo')[0]
                self.assertEqual(current['status'],'RUNNING')
            finally: release.set()
            self.assertEqual(future.result(10)['status'],'COMPLETED')

    def test_graph_selection_cannot_mutate_a_claimed_task(self):
        graph={'schema_version':1,'nodes':[node('W','WAIT_USER','supervisor','wait.user',parameters={'deadline':'input:d'})]}
        task=self.s.graphs.create('demo','selection',graph,{'input:d':time.time()+300})
        other=service(self.temp.name)
        with self.s.graphs.claims.hold(task['task_id']):
            with self.assertRaises(Rejected) as caught:
                other.graphs.signal(task['task_id'],'demo','W',True)
            self.assertEqual(caught.exception.code,'GRAPH_WORKER_BUSY')
            self.assertEqual(self.s.blackboard.get(task['task_id'])['revision'],task['revision'])

    def test_verifier_transport_failure_preserves_executed_payment_for_review(self):
        task=self.s.create_task('冻结card-demo')
        with patch.object(self.s.verifier,'verify',side_effect=OSError('offline')):
            result=self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        self.assertEqual(result['status'],'MANUAL_REVIEW')
        self.assertEqual(result['action_records'][0]['status'],'MANUAL_REVIEW')
        self.assertIsNotNone(result['action_records'][0]['execution']['transaction'])
        self.assertEqual(result['receipts'],[])
        with self.assertRaises(Rejected):self.s.banking.refresh(task['task_id'],'demo',task['proposed_actions'][0]['action_id'])

    def test_split_paid_state_requires_untampered_ledger_evidence(self):
        seed_participants(self.s)
        bill=self.s.splits.create('demo','acct-demo',1000,['alice','bob'])
        bill=self.s.splits.publish(bill['id'],'demo')
        row=self.s.db.one('SELECT body FROM split_bills WHERE id=?',(bill['id'],))
        body=json.loads(row['body'])
        for p in body['participants'].values():p['status']='PAID'
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE split_bills SET body=? WHERE id=?',(json.dumps(body),bill['id']))
        with self.assertRaises(Rejected):self.s.splits.get(bill['id'],'demo')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_transfer_verifier_exception_keeps_transaction_and_stops_for_review(self):
        task=self.s.create_task('给张三转1元')
        self.assertEqual(task['status'],'WAITING_USER')
        with patch.object(self.s.verifier,'verify',side_effect=OSError('read unavailable')):
            result=self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        self.assertEqual(result['status'],'MANUAL_REVIEW')
        self.assertIsNotNone(result['execution']['transaction'])
        self.assertIsNone(result['receipt'])
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_second_service_start_preserves_live_legacy_read(self):
        entered=threading.Event();release=threading.Event()
        original=self.s.finance.records
        def blocked(*a,**kw):
            entered.set();self.assertTrue(release.wait(8));return original(*a,**kw)
        with patch.object(self.s.finance,'records',blocked),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.s.create_task,'分析2026-09消费')
            self.assertTrue(entered.wait(5))
            try:self.assertEqual(service(self.temp.name).blackboard.list('demo')[0]['status'],'RUNNING')
            finally:release.set()
            self.assertEqual(future.result(10)['status'],'COMPLETED')

    def test_receipt_insert_failure_does_not_commit_orphan_generated_evidence(self):
        task=self.s.create_task('给张三转1元')
        with self.s.db.transaction() as conn:
            conn.execute("CREATE TRIGGER receipt_fault BEFORE INSERT ON receipts BEGIN SELECT RAISE(ABORT,'fault injection'); END")
        result=self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        self.assertEqual(result['status'],'MANUAL_REVIEW')
        self.assertEqual(self.s.db.rows('SELECT * FROM receipts'),[])
        events=[json.loads(row['body']) for row in self.s.db.rows('SELECT body FROM audit_events')]
        self.assertFalse(any(e['event_type']=='RECEIPT_GENERATED' for e in events))
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_repeated_verified_receipt_is_idempotent_and_bound(self):
        task=self.s.create_task('给张三转1元')
        result=self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        receipt=self.s.receipts.create(result['proposed_actions'][0],result['execution']['contract'],
            result['execution']['transaction'],result['verification'])
        self.assertEqual(receipt,result['receipt'])
        self.assertEqual(len(self.s.db.rows('SELECT * FROM receipts')),1)
