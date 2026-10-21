import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from backend.config import Config
from backend.domain import Rejected,digest
from backend.service import TrustaService
from backend.runtime.finance_transport import FinanceTransport
from tests.phase6_helpers import service
from tests.test_phase6_graph import read_graph,cancel_graph


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=service(self.tmp.name)

    def test_invalid_process_profile_does_not_silently_disable_isolation(self):
        for values in ({'banking_version':4,'communication_version':3,'finance_process_enabled':True},
                       {'agent_timeout_seconds':0},{'agent_timeout_seconds':True},{'finance_process_enabled':1}):
            with self.subTest(values=values),self.assertRaises(Rejected):TrustaService(Config(**values))

    def test_concurrent_confirmation_authorizes_exactly_one_transition(self):
        task=self.s.graphs.create('demo','cancel',cancel_graph(),{'input:account':'acct-demo',
            'input:kind':'SUBSCRIPTION_CANCEL','input:target':'sub-stream','input:values':{}})
        record=task['graph_state']['actions']['Cancel'];action=record['action'];auth=record['authorization']
        def decide(_):
            try:return self.s.auth.decide(auth['authorization_id'],'demo',action,True)['status']
            except Rejected as exc:return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(decide,range(8)))
        self.assertEqual(results.count('AUTHORIZED'),1)
        self.assertEqual(results.count('INVALID_AUTHORIZATION_STATE'),7)
        done=self.s.graphs.advance(task['task_id'],'demo')
        self.assertEqual(done['status'],'COMPLETED');self.assertEqual(len(done['receipts']),1)
        self.assertEqual(self.s.graphs.advance(task['task_id'],'demo')['receipts'],done['receipts'])


@unittest.skipUnless(os.name=='nt','requires actual Windows AppContainer')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        ReliabilityTests.setUp(self);self.s.finance_transport=FinanceTransport(self.s)
        self.addCleanup(self.s.finance_transport.close)

    def staged(self):
        with patch.object(self.s.graphs,'advance',side_effect=lambda task,user:self.s.blackboard.get(task)):
            task=self.s.graphs.create('demo','read',read_graph(),{'input:account':'acct-demo','input:month':'2026-09'})
        self.s.scheduler.start(task['task_id'],'Read')
        task=self.s.blackboard.get(task['task_id']);node=read_graph()['nodes'][0]
        return task,node,{'account_id':'acct-demo','month':'2026-09'}

    def restart(self,recover=False):
        self.s.finance_transport.close()
        other=TrustaService(self.s.config,recover_workflows=recover)
        other.finance_transport=FinanceTransport(other);self.addCleanup(other.finance_transport.close)
        return other

    def test_committed_read_survives_restart_and_lost_ack_without_second_bank_read(self):
        task,node,values=self.staged();first=self.s.finance_transport.run(node,task,values)
        other=self.restart()
        with patch.object(other.gateway,'read',side_effect=AssertionError('must reuse persisted result')):
            result=other.finance_transport.run(node,other.blackboard.get(task['task_id']),values)
        self.assertEqual(first,result)
        with patch.object(other.gateway,'read',side_effect=AssertionError('lost delivery ack must reuse result')):
            self.assertEqual(other.finance_transport.run(node,other.blackboard.get(task['task_id']),values),result)
        other.scheduler.transition(task['task_id'],'Read','COMPLETED')
        with patch.object(other.handoffs,'record_in',side_effect=RuntimeError('crash before atomic ack')):
            with self.assertRaises(RuntimeError):other._agent_message('finance','supervisor',task['task_id'],'Read','READ_ONLY_REPORT',result)
        row=other.db.rows('SELECT * FROM agent_delivery_jobs')[0]
        self.assertEqual(other.finance_transport.decode(row)['state'],'RESULT_COMMITTED')
        other._agent_message('finance','supervisor',task['task_id'],'Read','READ_ONLY_REPORT',result)
        self.assertEqual(other.finance_transport.decode(other.db.rows('SELECT * FROM agent_delivery_jobs')[0])['state'],'APPLIED')
        with self.assertRaises(Rejected):
            other.finance_transport.run(node,other.blackboard.get(task['task_id']),values)
        self.assertEqual(len(other.db.rows('SELECT * FROM agent_handoff_evidence')),1)
        self.assertEqual(other.db.rows('SELECT * FROM transactions'),[])

    def test_dispatched_crash_retries_only_bounded_read_and_preserves_write_boundary(self):
        task,node,values=self.staged()
        with patch('backend.runtime.finance_transport.ContainerProcess',side_effect=Rejected('AGENT_CONTAINER_LAUNCH_FAILED')):
            with self.assertRaises(Rejected):self.s.finance_transport.run(node,task,values)
        other=self.restart();result=other.finance_transport.run(node,other.blackboard.get(task['task_id']),values)
        job=other.finance_transport.decode(other.db.rows('SELECT * FROM agent_delivery_jobs')[0])
        self.assertEqual(job['attempts'],2);self.assertTrue(result)
        bad=node|{'capability':'transfer.propose','owner':'transaction'}
        with self.assertRaises(Rejected):other.finance_transport.run(bad,task,values)
        self.assertEqual(other.db.rows('SELECT * FROM transactions'),[])

    def test_default_restart_conservatively_fails_running_task(self):
        task,node,values=self.staged();self.s.finance_transport.run(node,task,values)
        other=self.restart(recover=True)
        self.assertEqual(other.blackboard.get(task['task_id'])['status'],'FAILED')
        with self.assertRaises(Rejected):other.finance_transport.run(node,other.blackboard.get(task['task_id']),values)

    def test_actual_child_cannot_read_parent_memory_connect_network_or_write_runtime(self):
        task=self.s.graphs.create('demo','read',read_graph(),{'input:account':'acct-demo','input:month':'2026-09'})
        self.assertEqual(task['status'],'COMPLETED')
        process=self.s.finance_transport.process
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            # Confirm this is a reachable listener in the trusted host.
            with socket.create_connection(listener.getsockname(),timeout=2):pass
            result=process.exchange({'operation':'probe','paths':{},'parent_pid':os.getpid(),
                'network_port':listener.getsockname()[1],'write_path':str(process.root/'forbidden-probe.txt')})['probe']
        self.assertEqual(result['parent_memory'],'ACCESS_DENIED')
        self.assertEqual(result['network_connect'],'ACCESS_DENIED')
        self.assertEqual(result['runtime_write'],'ACCESS_DENIED')
        self.assertFalse((process.root/'forbidden-probe.txt').exists())

    def test_read_retry_exhaustion_remains_sealed_and_no_fallback(self):
        task,node,values=self.staged()
        with patch('backend.runtime.finance_transport.ContainerProcess',side_effect=Rejected('AGENT_CONTAINER_LAUNCH_FAILED')):
            for _ in range(2):
                with self.assertRaises(Rejected):self.s.finance_transport.run(node,task,values)
            with self.assertRaises(Rejected) as exc:self.s.finance_transport.run(node,task,values)
        self.assertEqual(exc.exception.code,'AGENT_DELIVERY_RETRY_EXHAUSTED')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_blocked_pipe_write_is_inside_exchange_timeout(self):
        task,node,values=self.staged();self.s.finance_transport.run(node,task,values)
        process=self.s.finance_transport.process;process.timeout=.1;release=threading.Event()
        def stalled_write(*args):release.wait(3);return 0
        try:
            with patch.object(process.k,'WriteFile',side_effect=stalled_write):
                with self.assertRaises(Rejected) as exc:process.exchange({'oversized_for_pipe':'x'*100000})
            self.assertEqual(exc.exception.code,'AGENT_PROCESS_TIMEOUT_OR_PROTOCOL')
            self.assertIsNone(process.process)
        finally:release.set()
