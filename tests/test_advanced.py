import time,unittest
from unittest.mock import patch
from datetime import datetime
from backend.business import CHINA
from backend.banking.advanced import Advanced,dates,statements
from backend.domain import Rejected,canonical
from tests import test_finance

class AdvancedTests(unittest.TestCase):
 setUp=test_finance.FinanceTests.setUp
 tearDown=test_finance.FinanceTests.tearDown
 balance=test_finance.FinanceTests.balance
 decide=test_finance.FinanceTests.decide
 propose=test_finance.FinanceTests.propose
 complete=test_finance.FinanceTests.complete
 def create(self,typ,**kw):return self.f.create('demo',dict(type=typ,card_id='card-demo',**kw))['contract']
 def view(self):return Advanced(self.chat).view('demo')['states'][0]
 def test_credit_repayment_refund_surplus_full_cycle(self):
  start=self.balance();t=self.create('card_charge',amount=10000,merchant='测试消费');p=self.propose(t,'CARD_CHARGE')
  self.assertEqual(self.balance(),start);self.assertEqual(self.view()['debt'],0);self.decide(p,False);self.assertEqual(self.view()['debt'],0)
  self.complete(t,'CARD_CHARGE');self.assertEqual(self.balance(),start);self.assertEqual(self.view()['debt'],10000)
  with self.assertRaises(Rejected):self.create('card_repay',repay_mode='minimum') # not billed yet
  pay=self.create('card_repay',repay_mode='full');self.complete(pay,'CARD_REPAY');self.assertEqual(self.balance(),start-10000)
  ref=self.create('card_refund',entry_id=t['id'],amount=10000);self.complete(ref,'CARD_REFUND');self.assertEqual(self.view()['surplus'],10000);self.assertEqual(self.balance(),start-10000)
  surplus=self.create('card_surplus');self.complete(surplus,'CARD_SURPLUS');self.assertEqual(self.balance(),start);self.assertEqual(self.view()['surplus'],0)
  with self.assertRaises(Rejected):self.create('card_refund',entry_id=t['id'],amount=1)
 def test_refund_offsets_other_purchase_without_creating_cash(self):
  t=self.create('card_charge',amount=10000,merchant='测试A');self.complete(t,'CARD_CHARGE');self.complete(self.create('card_repay',repay_mode='full'),'CARD_REPAY')
  self.complete(self.create('card_charge',amount=6000,merchant='测试B'),'CARD_CHARGE');start=self.balance()
  self.complete(self.create('card_refund',entry_id=t['id'],amount=10000),'CARD_REFUND')
  self.assertEqual(self.view()['debt'],0);self.assertEqual(self.view()['surplus'],4000);self.assertEqual(self.balance(),start)
  self.assertTrue(all(s['outstanding']==0 for s in self.view()['statements']))
 def test_minimum_statement_rules_and_month_boundary(self):
  at=datetime(2026,12,31,tzinfo=CHINA).timestamp();self.assertEqual(dates(at),('2027-01-01','2027-01-05','2027-01-25'))
  s={'entries':[dict(amount=10001,refunded=0,paid=0,statement='2026-10-05',due='2026-10-25')]}
  self.assertEqual(statements(s,datetime(2026,10,26,tzinfo=CHINA).timestamp())[0]['minimum'],1001)
  t=self.create('card_charge',amount=10001,merchant='往期测试消费');self.complete(t,'CARD_CHARGE')
  v=self.view();v['entries'][0].update(statement='2020-01-05',due='2020-01-25',posted='2020-01-02')
  with self.s.db.transaction() as c:c.execute('UPDATE advanced_positions SET body=?',(canonical({k:v[k] for k in ('customer_id','card_id','version','entries','orders','holdings')}),))
  start=self.balance();payment=self.create('card_repay',repay_mode='minimum');self.assertEqual(payment['amount'],1001);self.complete(payment,'CARD_REPAY');self.assertEqual(self.balance(),start-1001);self.assertEqual(self.view()['debt'],9000)
  with self.assertRaises(Rejected):self.create('card_repay',repay_mode='minimum')
 def test_funds_pending_cancel_units_and_redemption(self):
  start=self.balance();t=self.create('fund_subscribe',product_id='practice-bond',amount=10000);self.complete(t,'FUND_SUBSCRIBE');self.assertEqual(self.balance(),start-10000)
  cancel=self.create('fund_cancel',order_id=t['id']);self.complete(cancel,'FUND_CANCEL');self.assertEqual(self.balance(),start)
  with self.assertRaises(Rejected):self.create('fund_confirm',order_id=t['id'])
  t=self.create('fund_subscribe',product_id='practice-bond',amount=10000);self.complete(t,'FUND_SUBSCRIBE');c=self.create('fund_confirm',order_id=t['id']);self.complete(c,'FUND_CONFIRM');self.assertEqual(self.balance(),start-10000)
  h=self.view()['holdings']['practice-bond'];self.assertEqual(h['cost'],9980);self.assertEqual(h['units'],9980*1000000//10250)
  r=self.create('fund_redeem',product_id='practice-bond',units=h['units']);self.complete(r,'FUND_REDEEM');self.assertEqual(self.balance(),start-10000+r['amount']);self.assertEqual(self.view()['holdings']['practice-bond'],{'cost':0,'units':0})
  self.assertEqual(self.propose(r,'FUND_REDEEM')['status'],'BLOCKED')
 def test_owner_risk_stale_and_confirmation_guards(self):
  empty_redeem=self.create('fund_redeem',product_id='practice-bond',units=10000)
  self.assertEqual(self.propose(empty_redeem,'FUND_REDEEM')['status'],'BLOCKED')
  with self.assertRaises(Rejected):Advanced(self.chat).create('other',{'type':'fund_subscribe','card_id':'card-demo','amount':10000,'product_id':'practice-bond'})
  t=self.create('fund_subscribe',product_id='practice-index',amount=10000);p=self.propose(t,'FUND_SUBSCRIBE');card=next(c for c in p['cards'] if c['type']=='confirm');self.assertEqual(card['risk_level'],'RED')
  with self.assertRaises(Rejected):self.f.decide('demo',{k:card[k] for k in ('task_id','action_id','action_hash')}|{'approve':True})
  self.complete(self.create('card_charge',amount=1000,merchant='改变状态'),'CARD_CHARGE');before=self.balance()
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.balance(),before);self.assertEqual(self.view()['orders'],[])
  with self.s.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=1 WHERE customer_id='demo'")
  self.assertEqual(self.propose(self.create('fund_subscribe',product_id='practice-index',amount=10000),'FUND_SUBSCRIBE')['status'],'BLOCKED')
 def test_todos_owner_assets_and_protection(self):
  from backend.banking.todos import view
  from backend.chat.activity import ActivityFeed
  before=ActivityFeed(self.chat).overview('demo')['finance']['net_at_cost_minor']
  self.complete(self.create('fund_subscribe',product_id='practice-bond',amount=10000),'FUND_SUBSCRIBE')
  self.assertEqual(ActivityFeed(self.chat).overview('demo')['finance']['net_at_cost_minor'],before)
  a=view(self.chat,'demo');self.assertTrue(any(i['title']=='基金份额待确认' for i in a['items']));self.assertEqual(view(self.chat,'other')['items'],[])
  self.complete(self.create('card_charge',amount=1000,merchant='测试'),'CARD_CHARGE')
  self.assertEqual(ActivityFeed(self.chat).overview('demo')['finance']['net_at_cost_minor'],before-1000)
  self.s.safety.enable_protection('demo',24,'USER_REQUEST','test')
  t=self.create('fund_subscribe',product_id='practice-bond',amount=10000);self.assertEqual(self.propose(t,'FUND_SUBSCRIBE')['status'],'BLOCKED')

class AdvancedHTTPTests(unittest.TestCase):
 from tests.test_chat import ChatHTTPTests as _HTTP
 stop=_HTTP.stop
 request=_HTTP.request
 def setUp(self):
  import tempfile,threading
  from pathlib import Path
  from backend.config import Config
  from backend.service import TrustaService
  from backend.server import create_server
  from backend.chat.service import ChatService
  from backend.chat.llm import ScriptedToolClient
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.service=TrustaService(Config(db_path=str(Path(self.tmp.name)/'http.db'),signing_key=b'test',communication_version=3,banking_version=5,safety_profile='competition'))
  self.server=create_server(self.service,'token',0,chat_service=ChatService(self.service,ScriptedToolClient({})))
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
  self.base=f'http://127.0.0.1:{self.server.server_port}'
 def test_owner_routes_and_unconfirmed_fund_cannot_move_money(self):
  for path in ('advanced','todos'):
   self.assertEqual(self.request('/api/chat/'+path)[0],200)
   self.assertIn(self.request('/api/chat/'+path,token='wrong')[0],(401,403))
  with self.service.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=3 WHERE customer_id='demo'")
  before=self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  b={'type':'fund_subscribe','card_id':'card-demo','amount':10000,'product_id':'practice-bond'}
  self.assertEqual(self.request('/api/chat/finance/create',b|{'approve':True})[0],400)
  code,result=self.request('/api/chat/finance/create',b);self.assertEqual(code,200)
  code,p=self.request('/api/chat/finance/proposal',{'contract_id':result['contract']['id'],'operation':'FUND_SUBSCRIBE'});self.assertEqual(code,200);self.assertEqual(p['status'],'WAITING_USER')
  card=next(c for c in p['cards'] if c['type']=='confirm');self.assertEqual(card['risk_level'],'RED')
  args={k:card[k] for k in ('task_id','action_id','action_hash')}
  self.assertEqual(self.request('/api/chat/finance/decide',args|{'approve':True})[0],400)
  self.assertEqual(self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
  self.assertEqual(self.request('/api/chat/finance/decide',args|{'approve':False})[0],200)
  self.assertEqual(self.service.db.rows('SELECT * FROM advanced_positions'),[])
