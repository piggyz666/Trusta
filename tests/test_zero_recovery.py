"""Actual OS processes, durable claims and restart fault injection; virtual funds only."""
import json
import multiprocessing as mp
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from backend.domain import Rejected,digest
from backend.runtime.graph_claims import process_alive
from backend.runtime.taskgraph import node
from tests.phase6_helpers import service,seed_participants
from backend.config import Config
from backend.service import TrustaService


def prepare_worker(root,bill,start,out):
    s=service(root);out.put(('ready',os.getpid()));start.wait(10)
    try:out.put(('result',s.splits.prepare_payment(bill,'alice','acct-alice')['task_id']))
    except Rejected as exc:out.put(('rejected',exc.code))


def claim_worker(root,claim,out):
    from pathlib import Path
    s=TrustaService(Config(db_path=str(Path(root)/'bank.sqlite3'),banking_version=5,communication_version=3,
        signing_key=b'phase6-test-root-key-only-32-bytes',strong_auth_secret='test-secret'),recover_workflows=False)
    with s.graphs.claims.hold(claim):
        out.put(os.getpid());time.sleep(60)


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=service(self.tmp.name);self.ctx=mp.get_context('spawn')

    def dead_claim(self,claim):
        out=self.ctx.Queue();worker=self.ctx.Process(target=claim_worker,args=(self.tmp.name,claim,out))
        worker.start()
        try:
            pid=out.get(timeout=15);self.assertTrue(process_alive(pid))
        finally:
            worker.terminate();worker.join(5);out.close();out.join_thread()
        self.assertFalse(process_alive(pid));return pid

    def test_two_processes_prepare_only_one_participant_action(self):
        seed_participants(self.s)
        bill=self.s.splits.create('demo','acct-demo',2000,['alice','bob']);self.s.splits.publish(bill['id'],'demo')
        start=self.ctx.Event();out=self.ctx.Queue()
        workers=[self.ctx.Process(target=prepare_worker,args=(self.tmp.name,bill['id'],start,out)) for _ in range(2)]
        try:
            for w in workers:w.start()
            self.assertTrue(all(out.get(timeout=15)[0]=='ready' for _ in workers));start.set()
            results=[out.get(timeout=15) for _ in workers]
            for w in workers:w.join(10);self.assertEqual(w.exitcode,0)
            self.assertTrue(any(kind=='result' for kind,_ in results))
            self.assertTrue(all(kind=='result' or value=='GRAPH_WORKER_BUSY' for kind,value in results))
            task=self.s.splits.prepare_payment(bill['id'],'alice','acct-alice')
            self.assertTrue(all(kind!='result' or value==task['task_id'] for kind,value in results))
            self.assertEqual(len(self.s.db.rows('SELECT * FROM actions')),1)
            self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        finally:
            start.set()
            for w in workers:
                if w.is_alive():w.terminate();w.join(5)
            out.close();out.join_thread()

    def test_dead_metadata_claim_recovers_but_dead_financial_claim_does_not_execute(self):
        seed_participants(self.s);bill=self.s.splits.create('demo','acct-demo',2000,['alice','bob'])
        self.dead_claim('split:'+bill['id'])
        self.assertEqual(self.s.splits.get(bill['id'],'demo')['status'],'DRAFT')
        task=self.s.create_task('给张三转1元');self.dead_claim(task['task_id'])
        with self.assertRaises(Rejected):self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_restart_recovers_all_parallel_step_rows_atomically(self):
        graph={'schema_version':1,'nodes':[node('A','READ','finance','accounts.read',parameters={'account_id':'input:a'}),
            node('B','READ','service','subscriptions.read'),node('R','REPORT','supervisor','graph.report',['A','B'])]}
        with patch.object(self.s.graphs,'advance',side_effect=lambda task,user:self.s.blackboard.get(task)):
            task=self.s.graphs.create('demo','crash frontier',graph,{'input:a':'acct-demo'})
        for code in ('A','B'):self.s.scheduler.start(task['task_id'],code)
        self.dead_claim(task['task_id'])
        other=service(self.tmp.name);task=other.blackboard.get(task['task_id'])
        self.assertEqual(task['status'],'FAILED')
        self.assertFalse(any(s['status']=='RUNNING' for s in task['steps']))
        rows=other.db.rows('SELECT step_id,status FROM task_steps WHERE task_id=?',(task['task_id'],))
        self.assertEqual({r['step_id']:r['status'] for r in rows},{s['step_id']:s['status'] for s in task['steps']})
        self.assertTrue(other.audit.verify_chain())

    def test_dead_scheduled_checking_claim_rebuilds_for_new_authorization(self):
        due=time.time()+60
        normalized={'task_type':'SCHEDULED_TRANSFER','recipient_name':'张三','amount':100,'currency':'CNY',
            'needs_clarification':False,'execute_at':due}
        task=self.s.banking.create('scheduled test','demo','acct-demo',normalized)
        task=self.s.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        job=self.s.banking.jobs(task['task_id'])[0];job['status']='CHECKING';self.s.banking.save_job(job)
        self.dead_claim(task['task_id'])
        other=service(self.tmp.name);fresh=other.get_task(task['task_id'])
        self.assertEqual(fresh['status'],'WAITING_USER')
        self.assertNotEqual(digest(fresh['proposed_actions'][-1]),digest(task['proposed_actions'][0]))
        self.assertEqual(other.banking.jobs(task['task_id'])[0]['status'],'REAUTH_REQUIRED')
        self.assertEqual(other.db.rows('SELECT * FROM transactions'),[])

    def test_live_event_claim_is_not_recovered_on_second_start(self):
        event=self.s.events.create('demo','birthday-test','acct-demo','BIRTHDAY_TRANSFER',time.time()+60,
            {'recipient_name':'张三','amount':100})
        body=dict(event,status='RUNNING')
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',('RUNNING',self.s.events._encode(body),event['id']))
        with self.s.events.claims.hold('event:'+event['id']):
            self.assertEqual(service(self.tmp.name).events.get(event['id'],'demo')['status'],'RUNNING')
        self.assertEqual(service(self.tmp.name).events.get(event['id'],'demo')['status'],'MANUAL_REVIEW')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
