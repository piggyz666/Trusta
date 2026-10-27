import unittest,json,time
from datetime import datetime,timedelta
from unittest.mock import patch
from tests import test_finance
from backend.banking.extensions import Extensions,GoldSavings,interest,occurrence
from backend.banking.member_support import Support
from backend.business import CHINA
from backend.domain import Rejected,canonical

class ExtensionTests(unittest.TestCase):
 setUp=test_finance.FinanceTests.setUp
 tearDown=test_finance.FinanceTests.tearDown
 balance=test_finance.FinanceTests.balance
 decide=test_finance.FinanceTests.decide
 propose=test_finance.FinanceTests.propose
 complete=test_finance.FinanceTests.complete
 def create(self,**kw):return Extensions(self.chat).create('demo',{'type':'deposit_open','card_id':'card-demo','amount':10000,'term':90}|kw)['contract']
 def test_deposit_confirmation_rejection_and_withdrawal(self):
  start=self.balance();t=self.create();p=self.propose(t,'DEPOSIT_OPEN');self.assertEqual(p['status'],'WAITING_USER',p);self.assertEqual(self.balance(),start)
  self.decide(p,False);self.assertEqual(self.balance(),start)
  self.complete(t,'DEPOSIT_OPEN');self.assertEqual(self.balance(),start-10000)
  d=Extensions(self.chat).view('demo')['deposits'][0];self.assertEqual(d['principal'],10000)
  withdraw=Extensions(self.chat).create('demo',{'type':'deposit_withdraw','deposit_id':d['id']})['contract'];self.complete(withdraw,'DEPOSIT_WITHDRAW');self.assertEqual(self.balance(),start)
  with self.assertRaises(Rejected):Extensions(self.chat).create('demo',{'type':'deposit_withdraw','deposit_id':d['id']})
 def test_maturity_renewal_then_early_withdrawal(self):
  t=self.create();self.complete(t,'DEPOSIT_OPEN');x=Extensions(self.chat);d=x.view('demo')['deposits'][0]
  with self.assertRaises(Rejected):x.create('demo',{'type':'deposit_renew','deposit_id':d['id']})
  today=datetime.now(CHINA).date();d.update(opened=(today-timedelta(days=90)).isoformat(),matures=today.isoformat())
  with self.s.db.transaction() as c:c.execute('UPDATE term_deposits SET body=? WHERE id=?',(canonical(d),d['id']))
  start=self.balance();r=x.create('demo',{'type':'deposit_renew','deposit_id':d['id']})['contract'];self.assertEqual(r['interest'],19);self.complete(r,'DEPOSIT_RENEW');self.assertEqual(self.balance(),start+19)
  d2=x.view('demo')['deposits'][0];self.assertEqual(d2['cycles'],2);self.assertEqual(d2['principal'],10000)
  r=x.create('demo',{'type':'deposit_withdraw','deposit_id':d['id']})['contract'];self.assertEqual(r['interest'],0);self.complete(r,'DEPOSIT_WITHDRAW')
 def test_deposit_ownership_balance_and_stale_state(self):
  with self.assertRaises(Rejected):Extensions(self.chat).create('other',{'type':'deposit_open','card_id':'card-demo','amount':10000,'term':90})
  t=self.create();p=self.propose(t,'DEPOSIT_OPEN');start=self.balance()
  with self.s.db.transaction() as c:c.execute("UPDATE accounts SET balance=0,version=version+1 WHERE id='acct-demo'")
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(Extensions(self.chat).view('demo')['deposits'],[])
  self.assertEqual(self.propose(self.create(),'DEPOSIT_OPEN')['status'],'BLOCKED')
 def test_fx_roundtrip_cost_ledger_and_strong_auth(self):
  x=Extensions(self.chat);start=self.balance();b={'type':'fx_buy','card_id':'card-demo','currency':'USD','units':10000,'quote_mode':'practice'}
  t=x.create('demo',b)['contract'];p=self.propose(t,'FX_BUY');self.assertEqual(p['status'],'WAITING_USER',p);c=next(c for c in p['cards'] if c['type']=='confirm');self.assertEqual(c['risk_level'],'RED')
  with self.assertRaises(Rejected):self.f.decide('demo',{k:c[k] for k in ('task_id','action_id','action_hash')}|{'approve':True})
  self.assertEqual(self.balance(),start);self.assertEqual(self.decide(p)['status'],'COMPLETED');self.assertEqual(self.balance(),start-70000)
  t=x.create('demo',b|{'type':'fx_sell','units':4000})['contract'];self.complete(t,'FX_SELL');w=x.view('demo')['wallets'][0];self.assertEqual((w['units'],w['cost']),(6000,42000))
  t=x.create('demo',b|{'type':'fx_sell','units':6000})['contract'];self.complete(t,'FX_SELL');self.assertEqual(self.balance(),start)
  t=x.create('demo',b|{'type':'fx_sell'})['contract'];self.assertEqual(self.propose(t,'FX_SELL')['status'],'BLOCKED')
  sums=self.s.db.rows('SELECT side,SUM(amount) total FROM ledger_entries GROUP BY side');self.assertEqual(sums[0]['total'],sums[1]['total'])
 def test_reference_no_fallback_and_jpy_unit(self):
  from backend.market import MarketQuotes
  x=Extensions(self.chat);b={'type':'fx_buy','card_id':'card-demo','currency':'JPY','units':10000,'quote_mode':'reference'}
  self.chat.market=MarketQuotes(gold_sources=(),fx_sources=())
  with self.assertRaises(Rejected):x.create('demo',b)
  rates={'USD':7.1,'EUR':8.2,'JPY':0.05,'HKD':0.91}
  self.chat.market=MarketQuotes(gold_sources=(),fx_sources=(lambda:{code:{'value':value,'as_of':'2026-10-26T00:00:00Z','source':'TEST FIXTURE'} for code,value in rates.items()},))
  start=self.balance()
  for code,expected in [('USD',71000),('EUR',82000),('JPY',500),('HKD',9100)]:
   draft=x.create('demo',b|{'currency':code})['contract'];self.assertEqual(draft['amount'],expected);self.assertEqual(draft['quote']['symbol'],code+'CNY')
  self.assertEqual(self.balance(),start)
  t=x.create('demo',b)['contract'];self.assertEqual(t['amount'],500);self.assertEqual(t['rate'],'0.05')
  self.complete(t,'FX_BUY');self.assertEqual(self.balance(),start-500)
  t=x.create('demo',b|{'type':'fx_sell'})['contract'];self.complete(t,'FX_SELL');self.assertEqual(self.balance(),start)
  self.chat.market.cache['JPY']['fetched_at']=time.time()-1000
  with self.assertRaises(Rejected):x.create('demo',b)
 def plan(self):return GoldSavings(self.chat).create('demo',{'name':'测试','card_id':'card-demo','amount':10000,'cycle':'monthly','start':datetime.now(CHINA).date().isoformat(),'count':3})['plan']
 def test_savings_due_pause_restore_confirm_no_duplicate(self):
  g=GoldSavings(self.chat);p=self.plan();start=self.balance();b={'plan_id':p['id'],'due':p['start']};t=g.prepare('demo',b)['contract'];self.assertEqual(t['id'],g.prepare('demo',b)['contract']['id']);self.assertEqual(self.balance(),start)
  proposal=self.propose(t,'GOLD_BUY');g.update('demo',{'plan_id':p['id'],'status':'PAUSED'})
  try:self.decide(proposal)
  except Rejected:pass
  self.assertEqual(self.balance(),start);g.update('demo',{'plan_id':p['id'],'status':'ACTIVE'});t=g.prepare('demo',b)['contract'];self.assertLessEqual(t['amount'],p['amount']);self.complete(t,'GOLD_BUY')
  with self.assertRaises(Rejected):g.prepare('demo',b)
  g.update('demo',{'plan_id':p['id'],'status':'CANCELLED'})
  with self.assertRaises(Rejected):g.update('demo',{'plan_id':p['id'],'status':'ACTIVE'})
 def test_savings_invalid_future_and_month_anchor(self):
  self.assertEqual(str(occurrence('2027-01-31','monthly',1)),'2027-02-28');self.assertEqual(str(occurrence('2027-01-31','monthly',2)),'2027-03-31')
  g=GoldSavings(self.chat);p=self.plan()
  with self.assertRaises(Rejected):g.prepare('other',{'plan_id':p['id'],'due':p['start']})
  with self.assertRaises(Rejected):g.prepare('demo',{'plan_id':p['id'],'due':str(occurrence(p['start'],'monthly',1))})
 def test_overview_assets_do_not_disappear(self):
  from backend.chat.activity import ActivityFeed
  a=ActivityFeed(self.chat);before=a.overview('demo')['finance']['assets_at_cost_minor'];t=self.create();self.complete(t,'DEPOSIT_OPEN');self.assertEqual(a.overview('demo')['finance']['assets_at_cost_minor'],before)
  t=Extensions(self.chat).create('demo',{'type':'fx_buy','card_id':'card-demo','currency':'USD','units':100,'quote_mode':'practice'})['contract'];self.complete(t,'FX_BUY');self.assertEqual(a.overview('demo')['finance']['assets_at_cost_minor'],before)
 def test_operator_reply_visible(self):
  t=self.create();self.complete(t,'DEPOSIT_OPEN');s=Support(self.chat);item=s.view('demo')['transactions'][0]
  case=s.create('demo',{'action_id':item['id'],'text':'请核对'});self.s.config.security_operator_token='test-operator-only'
  with self.assertRaises(Rejected):self.chat.operator_reply('wrong',case['id'],'越权')
  self.chat.operator_reply('test-operator-only',case['id'],'核对完成',True)
  row=next(c for c in s.view('demo')['cases'] if c['id']==case['id']);self.assertEqual(row['status'],'RESOLVED');self.assertEqual(row['replies'][0]['text'],'核对完成')
 def test_support_links_owner_transaction_and_existing_operator(self):
  t=self.create();self.complete(t,'DEPOSIT_OPEN');s=Support(self.chat);item=s.view('demo')['transactions'][0]
  with self.assertRaises(Rejected):s.create('other',{'action_id':item['id'],'text':'无法越权'})
  case=s.create('demo',{'action_id':item['id'],'text':'核对存款'});s.update('demo',{'id':case['id'],'operation':'message','text':'补充说明'})
  with self.assertRaises(Rejected):s.update('other',{'id':case['id'],'operation':'close'})
  self.assertEqual(s.view('demo')['cases'][0]['transaction']['id'],item['id']);self.assertEqual(s.view('demo')['cases'][0]['queue'][0]['text'],'补充说明')
  s.update('demo',{'id':case['id'],'operation':'close'});self.assertEqual(s.view('demo')['cases'][0]['status'],'CANCELLED_BY_USER')
  with self.assertRaises(Rejected):s.update('demo',{'id':case['id'],'operation':'message','text':'不能追加'})

class ExtensionHTTPTests(unittest.TestCase):
 from tests.test_chat import ChatHTTPTests as _HTTP
 setUp=_HTTP.setUp
 stop=_HTTP.stop
 request=_HTTP.request
 def test_routes_authenticated_forms_decline_and_no_injected_approval(self):
  for path in ('extensions','gold-savings','support'):
   self.assertEqual(self.request('/api/chat/'+path)[0],200)
   self.assertIn(self.request('/api/chat/'+path,token='wrong')[0],(401,403))
  b={'type':'deposit_open','card_id':'card-demo','amount':10000,'term':90}
  self.assertEqual(self.request('/api/chat/extensions/create',b|{'approve':True})[0],400)
  status,r=self.request('/api/chat/extensions/create',b);self.assertEqual(status,200)
  status,p=self.request('/api/chat/finance/proposal',{'contract_id':r['contract']['id'],'operation':'DEPOSIT_OPEN'});self.assertEqual(p['status'],'WAITING_USER')
  c=next(c for c in p['cards'] if c['type']=='confirm');before=self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  status,result=self.request('/api/chat/finance/decide',{k:c[k] for k in ('task_id','action_id','action_hash')}|{'approve':False});self.assertEqual(status,200)
  status,support=self.request('/api/chat/support');self.assertEqual(status,200)
  declined=next(i for i in support['transactions'] if i['id']==c['action_id']);self.assertEqual(declined['status'],'DECLINED')
  status,case=self.request('/api/chat/support/create',{'action_id':c['action_id'],'text':'检查已拒绝业务'});self.assertEqual(status,200)
  self.assertEqual(self.request('/api/chat/support/update',{'id':case['id'],'operation':'close'})[0],200)
  self.assertEqual(self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
  for route in ('deposits','gold-savings','exchange','support'):
   from backend.server import SPA_ROUTES
   self.assertIn('/'+route,SPA_ROUTES)
 def test_agent_navigation_and_scheduled_worker_are_read_only(self):
  from backend.chat.rules import rule_calls
  for text,page in [('查看黄金定投','gold-savings'),('定期存款','deposits'),('换汇','exchange'),('服务工单','support')]:
   calls=rule_calls(text,{})
   self.assertEqual(calls[0]['arguments']['page'],page)
  chat=self.server.chat if hasattr(self.server,'chat') else None
  # The scheduled worker never moves balances on its own.
  from backend.chat.service import ChatService
  from scripts.scheduled_worker import run_once
  before=self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  run_once(self.service);run_once(self.service)
  self.assertEqual(self.service.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
