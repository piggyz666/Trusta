import copy
import base64
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import threading
import json
import http.client
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from backend.config import Config
from backend.domain import Rejected,digest
from backend.service import TrustaService
from backend.security.banking_controls import hotp


class BackendScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Config(db_path=str(Path(self.tmp.name)/'bank.db'),signing_key=b'scenario-backend-tests',
                           banking_version=5,communication_version=3,safety_profile='competition')
        self.s=TrustaService(self.config)
        self.counter=int(time.time()//30)+1
        with patch('backend.security.banking_controls.time.time',return_value=self.counter*30):
            seed=self.s.safety.begin_enrollment('demo')['secret']
            self.seed=base64.b32decode(seed)
            self.s.safety.confirm_enrollment('demo',hotp(self.seed,self.counter))

    def approve(self,task):
        entry=task['action_records'][0]
        if entry['authorization']['auth_method']=='totp':
            self.counter+=1
            with patch('backend.security.banking_controls.time.time',return_value=self.counter*30):
                return self.s.banking.authorize(task['task_id'],'demo',digest(entry['action']),True,hotp(self.seed,self.counter))
        return self.s.banking.authorize(task['task_id'],'demo',digest(entry['action']),True)

    def test_annual_report_grounded_and_readonly(self):
        before=self.s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo')
        task=self.s.create_task('分析2026年度账单报告')
        self.assertEqual(task['status'],'COMPLETED')
        result=task['result']
        self.assertEqual(result['period'],'ANNUAL')
        self.assertEqual(len(result['months']),12)
        self.assertEqual(sum(m['net_spend'] for m in result['months']),result['summary']['net_spend'])
        monthly=[self.s.create_task(f'分析2026-{m:02}消费')['result']['summary']['net_spend'] for m in range(1,13)]
        self.assertEqual(sum(monthly),result['summary']['net_spend'])
        self.assertEqual(before,self.s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo'))
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM authorizations'),[])

    def test_annual_evidence_rejects_duplicates_wrong_month_and_currency(self):
        evidence=self.s.finance.annual_records('demo','acct-demo',2026)
        bad=copy.deepcopy(evidence)
        row=next(row for rows in bad.values() for row in rows)
        bad['2026-01'].append(row)
        with self.assertRaises(Rejected): self.s.finance.annual(2026,bad,'CNY')
        bad=copy.deepcopy(evidence)
        source=next(month for month,rows in bad.items() if rows)
        bad['2026-01'].append(bad[source].pop())
        with self.assertRaises(Rejected): self.s.finance.annual(2026,bad,'CNY')
        bad=copy.deepcopy(evidence)
        next(row for rows in bad.values() for row in rows)['currency']='EUR'
        with self.assertRaises(Rejected): self.s.finance.annual(2026,bad,'CNY')
        with self.assertRaises(Rejected): self.s.finance.annual_records('other','acct-demo',2026)
        with self.assertRaises(Rejected): self.s.finance.annual_records('demo','acct-demo',True)

    def test_annual_conversation_fields(self):
        from backend.conversation.schemas import extract,normalize_fields
        fields=extract('请帮我分析2026年年度账单报告')
        self.assertEqual(normalize_fields(fields),{'task_type':'BILL_ANALYSIS','year':2026,'needs_clarification':False})
        for request in ('分析0000年账单','分析9999年账单'):
            with self.assertRaises(Rejected): self.s.intent.normalize(request)

    def test_application_has_no_bank_change_until_authorized(self):
        task=self.s.create_task('申请虚拟借记卡 acct-demo')
        self.assertEqual(task['status'],'WAITING_USER')
        self.assertEqual(self.s.db.rows('SELECT * FROM card_applications'),[])
        done=self.approve(task)
        self.assertEqual(done['status'],'COMPLETED')
        self.assertEqual(done['action_records'][0]['verification']['status'],'VERIFIED')
        self.assertEqual(self.s.db.one('SELECT status FROM card_applications')['status'],'SUBMITTED')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM cards')),1) # Application is not fabricated card issuance.
        self.assertEqual(self.s.create_task('申请借记卡acct-demo')['status'],'BLOCKED')
        self.assertEqual(TrustaService(self.config).gateway.read('service','demo','card_application.get',resource_id='acct-demo')['status'],'SUBMITTED')

    def test_loss_terminal_state_and_strong_auth(self):
        task=self.s.create_task('挂失card-demo')
        entry=task['action_records'][0]
        self.assertEqual(entry['policy']['decision'],'STRONG_AUTH')
        with self.assertRaises(Rejected): self.s.authorize(task['task_id'],'demo',digest(entry['action']),True,'old-secret')
        self.assertEqual(self.s.gateway.read('service','demo','card.get',resource_id='card-demo')['status'],'ACTIVE')
        done=self.approve(task)
        self.assertEqual(done['status'],'COMPLETED')
        card=self.s.gateway.read('service','demo','card.get',resource_id='card-demo')
        self.assertEqual(card['status'],'LOST')
        self.assertEqual((card['online_enabled'],card['international_enabled'],card['contactless_enabled']),(0,0,0))
        for text in ('解冻card-demo','挂失card-demo','card-demo限额设为1000元'):
            self.assertEqual(self.s.create_task(text)['status'],'BLOCKED')
        self.assertTrue(self.s.audit.verify_chain())

    def test_frozen_card_can_report_lost_and_owner_isolation(self):
        self.approve(self.s.create_task('冻结card-demo'))
        self.assertEqual(self.approve(self.s.create_task('挂失card-demo'))['status'],'COMPLETED')
        self.assertEqual(self.s.create_task('申请借记卡acct-demo','other')['status'],'BLOCKED')
        with self.assertRaises(Rejected): self.s.gateway.read('service','other','card_application.get',resource_id='acct-demo')

    def test_reminders_idempotent_restart_cancel_consistent(self):
        first=self.s.events.reminders('demo','acct-demo')
        self.assertEqual(len(first['reminders']),3)
        self.assertEqual({r['authority'] for r in first['reminders']},{'NOTIFICATION_ONLY'})
        ids={r['id'] for r in first['reminders']}
        self.assertEqual(ids,{r['id'] for r in self.s.events.reminders('demo','acct-demo')['reminders']})
        self.assertEqual(len(self.s.db.rows('SELECT * FROM renewal_reminders')),3)
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])
        self.approve(self.s.create_task('取消订阅sub-stream'))
        current=TrustaService(self.config).events.reminders('demo','acct-demo')['reminders']
        self.assertNotIn('sub-stream',{r['resource_id'] for r in current})
        with self.assertRaises(Rejected): self.s.events.reminders('other','acct-demo')

    def event(self,**changes):
        values=dict(request_id='birthday-2026',account_id='acct-demo',event_type='BIRTHDAY_TRANSFER',
                    execute_at=time.time()+2,parameters={'recipient_name':'张三','amount':10000})
        values.update(changes)
        return self.s.events.create('demo',**values)

    def test_events_create_proposal_once_never_auto_pay(self):
        event=self.event()
        self.assertEqual(self.s.events.tick('demo')['processed'],0)
        with patch('backend.runtime.event_plans.time.time',return_value=event['execute_at']+1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:self.s.events.tick('demo'),range(2)))
        self.assertEqual(sum(r['processed'] for r in results),1)
        current=self.s.events.get(event['id'],'demo')
        self.assertEqual(current['status'],'PROPOSED')
        self.assertEqual(self.s.get_task(current['task_id'])['status'],'WAITING_USER')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        restarted=TrustaService(self.config)
        self.assertEqual(restarted.events.tick('demo')['processed'],0)

    def test_events_ownership_cancel_idempotency_and_bad_parameters(self):
        event=self.event()
        self.assertEqual(self.s.events.create('demo',event['request_id'],**event['intent'])['id'],event['id'])
        with self.assertRaises(Rejected): self.event(parameters={'recipient_name':'张三','amount':20000})
        with self.assertRaises(Rejected): self.s.events.cancel(event['id'],'other')
        with self.assertRaises(Rejected): self.event(request_id='bad',parameters={'recipient_name':'张三','amount':True})
        with self.assertRaises(Rejected): self.event(request_id='bad',event_type='AUTO_PAY')
        self.s.events.cancel(event['id'],'demo')
        with patch('backend.runtime.event_plans.time.time',return_value=event['execute_at']+1):
            self.assertEqual(self.s.events.tick('demo')['processed'],0)

    def test_cross_event_and_interruption_do_not_retry(self):
        event=self.event(event_type='SPENDING_REVIEW',parameters={})
        with patch('backend.runtime.event_plans.time.time',return_value=event['execute_at']+1):
            current=self.s.events.tick('demo')['events'][0]
        task=self.s.get_task(current['task_id'])
        self.assertEqual(task['task_type'],'CROSS_SCENARIO')
        self.assertEqual(task['status'],'WAITING_USER')
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])
        interrupted=self.event(request_id='interrupted')
        interrupted['status']='RUNNING'
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE event_plans SET status='RUNNING',body=? WHERE id=?",(self.s.events._encode(interrupted),interrupted['id']))
        restarted=TrustaService(self.config)
        self.assertEqual(restarted.events.get(interrupted['id'],'demo')['status'],'MANUAL_REVIEW')
        self.assertEqual(restarted.events.tick('demo')['processed'],0)

    def test_event_state_tamper_fails_closed(self):
        event=self.event()
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE event_plans SET execute_at=? WHERE id=?',(event['execute_at']-10,event['id']))
        with self.assertRaises(Rejected): self.s.events.tick('demo')

    def test_new_http_endpoints_scoped_and_no_financial_execution(self):
        from backend.server import create_server
        server=create_server(self.s,'test-token',port=0,identities={'test-token':'demo','other-token':'other'})
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.addCleanup(cleanup)
        def call(method,path,body=None,token='test-token'):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
            connection.request(method,path,json.dumps(body) if body is not None else None,headers)
            response=connection.getresponse()
            data=json.loads(response.read());status=response.status;connection.close()
            return status,data
        status,report=call('POST','/api/tasks',{'text':'分析2026年账单'})
        self.assertEqual(status,201)
        self.assertEqual(report['result']['period'],'ANNUAL')
        status,reminders=call('GET','/api/reminders?account_id=acct-demo')
        self.assertEqual(status,200)
        self.assertEqual(len(reminders['reminders']),3)
        self.assertNotEqual(call('GET','/api/reminders?account_id=acct-demo',token='other-token')[0],200)
        body={'request_id':'http-event','account_id':'acct-demo','event_type':'SPENDING_REVIEW','execute_at':time.time()+30,'parameters':{}}
        status,event=call('POST','/api/events',body)
        self.assertEqual(status,201)
        self.assertEqual(call('GET','/api/events',token='other-token')[1]['events'],[])
        self.assertNotEqual(call('POST','/api/events/'+event['id']+'/cancel',{},'other-token')[0],200)
        self.assertEqual(call('POST','/api/events/'+event['id']+'/cancel',{})[0],200)
        self.assertEqual(call('POST','/api/events/tick',{})[1]['processed'],0)
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])


if __name__=='__main__': unittest.main()
