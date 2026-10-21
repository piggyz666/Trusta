from pathlib import Path
import tempfile
import threading
import unittest
from backend.config import Config
from backend.server import create_server
from backend.service import TrustaService
import tests.test_api as old


class BankingApiTests(unittest.TestCase):
    request=old.ApiTests.request
    close_server=old.ApiTests.close_server

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service=TrustaService(Config(db_path=str(Path(self.temp.name)/'api.db'),signing_key=b'phase5-api-key',
                                         communication_version=3,banking_version=5,strong_auth_secret='test-secret'))
        self.server=create_server(self.service,'test-token',0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def test_individual_action_endpoint_binds_id_and_hash(self):
        status,task=self.request('/api/tasks',{'text':'分析2026-09消费，找出可能的订阅，我逐项确认后取消'})
        self.assertEqual(status,201)
        first,second=task['action_records']
        path=f"/api/tasks/{task['task_id']}/actions/{second['action']['action_id']}/authorize"
        self.assertEqual(self.request(path,{'action_hash':first['authorization']['action_hash'],'approve':True})[0],400)
        path=f"/api/tasks/{task['task_id']}/actions/{first['action']['action_id']}/authorize"
        status,result=self.request(path,{'action_hash':first['authorization']['action_hash'],'approve':True})
        self.assertEqual(status,200)
        self.assertEqual(result['status'],'WAITING_USER')
        self.assertEqual(self.request(path,{'approve_all':True})[0],400)
        self.assertEqual(self.request(path,{'action_hash':first['authorization']['action_hash'],'approve':True})[0],400)

    def test_new_receipt_proof_auth_and_no_direct_write_api(self):
        _,task=self.request('/api/tasks',{'text':'冻结card-demo'})
        entry=task['action_records'][0]
        path=f"/api/tasks/{task['task_id']}/actions/{entry['action']['action_id']}/authorize"
        status,done=self.request(path,{'action_hash':entry['authorization']['action_hash'],'approve':True})
        self.assertEqual(done['status'],'COMPLETED')
        self.service.anchors.run_once()
        receipt=done['receipts'][0]
        self.assertTrue(self.request('/api/audit/proof?receipt_id='+receipt['receipt_id'])[1]['record_verified'])
        for wrong in ('/api/bank/card/freeze','/api/schedules/tick','/api/investment/purchase'):
            self.assertEqual(self.request(wrong,{})[0],404)
        self.assertEqual(self.request(path,{'action_hash':entry['authorization']['action_hash'],'approve':True},token='wrong')[0],403)

    def test_resume_returns_new_action_requiring_new_authorization(self):
        _,task=self.request('/api/tasks',{'text':'冻结card-demo'})
        entry=task['action_records'][0]
        path=f"/api/tasks/{task['task_id']}/actions/{entry['action']['action_id']}/resume"
        status,resumed=self.request(path,{})
        self.assertEqual(status,200)
        self.assertNotEqual(resumed['action_records'][-1]['authorization']['action_hash'],entry['authorization']['action_hash'])
        self.assertEqual(resumed['status'],'WAITING_USER')
        self.assertEqual(self.service.db.rows('SELECT * FROM bank_operations'),[])

    def test_phase5_conversation_purchase_multiturn_no_approval(self):
        _,conversation=self.request('/api/conversations',{})
        path=f"/api/conversations/{conversation['conversation_id']}/turns"
        _,one=self.request(path,{'turn_id':'first','revision':0,'text':'购买sandbox-r1'})
        _,two=self.request(path,{'turn_id':'second','revision':one['revision'],'text':'100元'})
        self.assertEqual(two['task']['status'],'WAITING_USER')
        self.assertEqual(self.service.db.rows('SELECT * FROM bank_operations'),[])
