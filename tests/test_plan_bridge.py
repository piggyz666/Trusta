import json
from datetime import date,datetime,timedelta
from unittest.mock import patch
from tests.test_chat_b8 import ChatHarness
from backend.banking.personal import Personal,event_rows,next_occurrence
from backend.chat.plan_bridge import PlanBridge
from backend.business import CHINA
from backend.domain import Rejected

class PlanBridgeTests(ChatHarness):
 def setUp(self):
  super().setUp();self.p=Personal(self.s);self.bridge=PlanBridge(self.chat)
 def make(self,budget=100000,days=5):
  day=(datetime.now(CHINA).date()+timedelta(days=days)).isoformat()
  return self.p.create('demo',dict(name='妈妈生日',kind='birthday',place='深圳',start=day,end=day,budget=budget))['plan_id']
 def prepare(self,pid,items=None):
  with patch.object(self.chat,'_understand',side_effect=AssertionError('Form must not call a model')):
   result=self.bridge.prepare('demo',dict(plan_id=pid,account_id='acct-demo',items=items or []))
  self.sid=result['session_id'];view=self.chat.get(self.sid,'demo');self.rev=view['revision']
  return result,view
 def plan(self):return self.p.plans('demo')['plans'][0]
 def test_full_linked_virtual_cycle(self):
  start=self.balance();total=self.total_money();pid=self.make()
  result,view=self.prepare(pid,['m-flower','m-cake'])
  self.assertTrue(result['prepared']);self.assertEqual(self.balance(),start)
  self.assertTrue(self.plan()['binding']['fund_id']);self.assertEqual(self.plan()['reserved'],0)
  retry,_=self.prepare(pid,['m-flower','m-cake']);self.assertTrue(retry['reused']);self.assertEqual(retry['session_id'],result['session_id'])
  self.assertEqual(len(view['pending']),1)
  with self.assertRaises(Rejected):self.p.update('demo',dict(plan_id=pid,budget=100000,status='ARCHIVED'))
  with self.assertRaises(Rejected):self.p.update('demo',dict(plan_id=pid,budget=110000,status='ACTIVE'))
  self.decide(view['pending'][0]);self.assertEqual(self.plan()['reserved'],100000)
  with self.assertRaises(Rejected):self.p.update('demo',dict(plan_id=pid,budget=100000,status='ARCHIVED'))
  order=self.advance('WISH_ORDER')['pending'][0];self.assertEqual(self.plan()['spent'],0)
  self.decide(order);self.assertEqual(self.plan()['spent'],49800);self.assertEqual(self.plan()['reserved'],50200)
  release=self.advance('WISH_RELEASE')['pending'][0];self.decide(release)
  self.assertEqual(self.plan()['reserved'],0);self.assertEqual(self.balance(),start-49800);self.assertEqual(self.total_money(),total)
  self.p.update('demo',dict(plan_id=pid,budget=100000,status='ARCHIVED'));self.assertTrue(self.s.audit.verify_chain())
 def test_decline_and_owner_boundaries(self):
  pid=self.make();start=self.balance()
  for body in [dict(plan_id=pid,account_id='acct-other',items=[]),dict(plan_id=pid,account_id='acct-demo',items=['nope'])]:
   with self.assertRaises(Rejected):self.bridge.prepare('demo',body)
  with self.assertRaises(Rejected):self.bridge.prepare('other',dict(plan_id=pid,account_id='acct-demo',items=[]))
  _,view=self.prepare(pid);self.decide(view['pending'][0],False)
  self.assertEqual(self.balance(),start);self.assertEqual({e['status'] for e in self.s.events.list('demo')},{'CANCELLED'})
  self.p.update('demo',dict(plan_id=pid,budget=100000,status='ARCHIVED'))
 def test_over_budget_and_past_date(self):
  pid=self.make(100)
  with self.assertRaises(Rejected):self.bridge.prepare('demo',dict(plan_id=pid,account_id='acct-demo',items=['m-flower']))
  pid=self.make(days=-1)
  with self.assertRaises(Rejected):self.bridge.prepare('demo',dict(plan_id=pid,account_id='acct-demo',items=[]))
  self.assertEqual(self.s.db.rows('SELECT * FROM life_plan_bindings'),[])
 def test_profile_dates_replace_demo_and_opt_in(self):
  today=datetime.now(CHINA).date()
  profile=dict(name='测试用户',city='深圳',has_car=False,suggestions=True,interests=[],dates=[dict(name='妈妈生日',kind='birthday',date=today.isoformat())])
  self.p.save_profile('demo',profile)
  rows=event_rows(self.s,'demo');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['person'],'妈妈')
  self.assertEqual([e['id'] for e in self.chat.catalog('demo')['life_events']],[r['id'] for r in rows])
  session=self.chat.create_session('demo','test');self.assertEqual(session['messages'],[])  # never before the customer speaks; shown in the plans page instead
  self.assertEqual(self.p.plans('demo')['suggestions'][0]['days'],0)
  self.assertEqual(event_rows(self.s,'other'),[])
  self.p.save_profile('demo',profile|{'suggestions':False});self.assertEqual(event_rows(self.s,'demo',True),[])
  self.assertEqual(len(event_rows(self.s,'demo')),1)
  self.p.save_profile('demo',profile|{'dates':[]});self.assertEqual(event_rows(self.s,'demo'),[])
  self.assertIsNotNone(self.s.db.one("SELECT id FROM life_events WHERE id='le-spouse-birthday'"))
 def test_leap_date(self):
  self.assertEqual(next_occurrence(2,29,date(2026,10,26)),date(2028,2,29))
  self.assertEqual(next_occurrence(2,29,date(2028,2,29)),date(2028,2,29))
 def test_early_release_needs_confirmation(self):
  pid=self.make();start=self.balance();_,view=self.prepare(pid)
  self.decide(view['pending'][0]);locked=self.balance()
  with patch.object(self.chat,'_understand',side_effect=AssertionError('No model')):
   result=self.bridge.release('demo',{'plan_id':pid})
  self.assertEqual(self.balance(),locked)
  view=self.chat.get(result['session_id'],'demo');self.rev=view['revision']
  self.assertEqual(view['pending'][0]['action_type'],'FUND_RELEASE')
  self.bridge.release('demo',{'plan_id':pid})
  self.assertEqual(len(self.chat.get(self.sid,'demo')['pending']),1)
  self.decide(view['pending'][0]);self.assertEqual(self.balance(),start)
  self.p.update('demo',dict(plan_id=pid,budget=100000,status='ARCHIVED'))
 def test_expired_conversation_keeps_fund_accessible(self):
  pid=self.make();_,view=self.prepare(pid);self.decide(view['pending'][0])
  body=self.chat.store.get(self.sid,'demo');body['expires_at']=0;self.chat.store.save(body)
  previous=self.sid
  result=self.bridge.release('demo',{'plan_id':pid});self.sid=result['session_id']
  self.assertNotEqual(previous,self.sid)
  view=self.chat.get(self.sid,'demo');self.rev=view['revision']
  self.assertEqual(view['pending'][0]['action_type'],'FUND_RELEASE');self.decide(view['pending'][0])
  self.assertEqual(self.plan()['reserved'],0)
 def test_trip_release_after_end(self):
  start=datetime.now(CHINA).date()+timedelta(days=5);end=start+timedelta(days=7)
  pid=self.p.create('demo',dict(name='旅行',kind='travel',place='杭州',start=start.isoformat(),end=end.isoformat(),budget=100000))['plan_id']
  self.prepare(pid)
  release=self.pending_event('WISH_RELEASE')
  self.assertGreater(datetime.fromtimestamp(release['execute_at'],CHINA).date(),end)
 def test_prepare_cannot_accept_authorization_or_model_calls(self):
  from backend.chat.http import handle_post
  from types import SimpleNamespace
  handler=SimpleNamespace(headers={},reply=lambda status,body:body)
  with self.assertRaises(Rejected):handle_post(handler,self.chat,'/api/chat/plans/prepare',dict(plan_id=self.make(),account_id='acct-demo',items=[],approve=True),'demo')
  with self.assertRaises(Rejected):handle_post(handler,self.chat,'/api/chat/sessions/'+self.sid+'/messages',dict(text='你好',message_id='x',revision=self.rev,prepared_calls=[]),'demo')
 def test_account_balances_and_static_route(self):
  from backend.server import serve_static
  account=next(a for a in self.bridge.view('demo')['accounts'] if a['id']=='acct-demo')
  self.assertEqual(account['balance'],self.balance())
  self.assertIsNotNone(serve_static('/plans'))
 def test_existing_profile_migration_idempotent(self):
  from backend.persistence import Database
  profile=dict(name='测试',city='',has_car=False,suggestions=True,interests=[],dates=[dict(name='周年纪念日',kind='anniversary',date='2020-11-01')])
  with self.s.db.transaction() as c:c.execute('INSERT INTO personal_profiles VALUES(?,?)',('demo',json.dumps(profile)))
  # Reopen runs the same startup migration as a deployed upgrade.
  db=Database(self.s.config.db_path)
  self.assertEqual(len(db.rows("SELECT * FROM life_events WHERE id LIKE 'profile-%'")),1)
  db=Database(self.s.config.db_path)
  self.assertEqual(len(db.rows("SELECT * FROM life_events WHERE id LIKE 'profile-%'")),1)
