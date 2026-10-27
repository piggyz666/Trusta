import os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.config import Config
from backend.service import TrustaService
from backend.chat.service import ChatService
from backend.banking.finance import Finance,schedule,LIMIT
from backend.domain import Rejected

class FinanceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=TrustaService(Config(db_path=str(Path(self.tmp.name)/'test.db'),signing_key=b'x'*32,communication_version=3,banking_version=5,safety_profile='competition'))
  self.chat=ChatService(self.s);self.f=Finance(self.chat)
  with self.s.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=3 WHERE customer_id='demo'")
  self.chat.market=type('Market',(),{'view':lambda self:{'quotes':[{'symbol':'XAU','price':800.01,'snapshot':False,'stale':False,'source':'UNIT TEST FIXTURE','as_of':'test'}]}})()
 def tearDown(self):self.tmp.cleanup()
 def balance(self):return self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
 def create(self,typ='loan',**kw):return self.f.create('demo',{'type':typ,'card_id':'card-demo',**({'amount':10001,'months':3} if typ=='loan' else {'units':3} if typ.startswith('gold') else {}),**kw})['contract']
 def propose(self,t,op):return self.f.propose('demo',{'contract_id':t['id'],'operation':op})
 def decide(self,p,approve=True):
  card=next(c for c in p['cards'] if c['type']=='confirm');b={k:card[k] for k in ('task_id','action_id','action_hash')}|{'approve':approve}
  if card['risk_level']=='RED' and approve:
   with patch.dict(os.environ,{'TRUSTA_DEMO_MODE':'true'}):sms=self.f.sms('demo',{k:card[k] for k in ('task_id','action_id')})
   b['verification']={'method':'SMS','code':sms['dev_code']}
  return self.f.decide('demo',b)
 def complete(self,t,op):
  p=self.propose(t,op);self.assertEqual(p['status'],'WAITING_USER',p);r=self.decide(p);self.assertEqual(r['status'],'COMPLETED',r);return p
 def test_loan_partial_and_early_settlement_ledger(self):
  start=self.balance();t=self.create();p=self.propose(t,'LOAN_DRAW');self.assertEqual(self.balance(),start)
  self.assertEqual(self.decide(p)['status'],'COMPLETED');self.assertEqual(self.balance(),start+10001)
  self.complete(t,'CREDIT_REPAY');self.complete(t,'CREDIT_SETTLE')
  self.assertEqual(self.balance(),start-50) # 30 cents first + 20 second; last fee waived
  record=next(c for c in self.f.view('demo')['contracts'] if c['id']==t['id']);self.assertEqual(record['remaining'],0);self.assertEqual(record['status'],'SETTLED');self.assertTrue(all(r['paid'] for r in record['schedule']))
  self.assertEqual(self.propose(t,'LOAN_DRAW')['status'],'BLOCKED')
  ledger=self.s.db.rows("SELECT side,SUM(amount) AS n FROM ledger_entries GROUP BY side");self.assertEqual(ledger[0]['n'],ledger[1]['n'])
  fees=self.s.db.one("SELECT SUM(amount) n FROM consumption_records WHERE category='融资费用'")['n'];self.assertEqual(fees,50)
 def test_gold_confirm_strong_auth_and_roundtrip(self):
  start=self.balance();t=self.create('gold_buy');p=self.propose(t,'GOLD_BUY');c=p['cards'][0];self.assertEqual(c['risk_level'],'RED')
  with self.assertRaises(Rejected):self.f.decide('demo',{k:c[k] for k in ('task_id','action_id','action_hash')}|{'approve':True})
  self.assertEqual(self.balance(),start);self.assertEqual(self.decide(p)['status'],'COMPLETED');self.assertEqual(self.balance(),start-2400)
  t2=self.create('gold_sell');self.complete(t2,'GOLD_SELL');self.assertEqual(self.balance(),start)
  pos=self.f.view('demo')['positions'][0];self.assertEqual((pos['units'],pos['cost']),(0,0))
 def test_reject_does_not_move_funds(self):
  t=self.create();p=self.propose(t,'LOAN_DRAW');start=self.balance();self.decide(p,False);self.assertEqual(self.balance(),start);self.assertEqual(self.f.view('demo')['outstanding'],0)
 def test_installment_source_and_no_double_financing(self):
  with self.s.db.transaction() as c:c.execute("INSERT INTO consumption_records VALUES('eligible','acct-demo','已支付消费','交通',10000,'CNY',?,'PURCHASE','SETTLED')",(time.time(),))
  start=self.balance();t=self.create('installment',source_id='eligible',months=3);self.complete(t,'INSTALLMENT_START');self.assertEqual(self.balance(),start+10000)
  other=self.create('installment',source_id='eligible',months=6);self.assertEqual(self.propose(other,'INSTALLMENT_START')['status'],'BLOCKED')
  self.complete(t,'CREDIT_SETTLE');self.assertEqual(self.balance(),start-50)
  self.assertNotIn('eligible',[r['id'] for r in self.f.view('demo')['eligible']])
 def test_stale_card_and_balance_prevent_execution(self):
  t=self.create();p=self.propose(t,'LOAN_DRAW');start=self.balance()
  with self.s.db.transaction() as c:c.execute("UPDATE cards SET status='FROZEN',version=version+1 WHERE id='card-demo'")
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.balance(),start)
 def test_credit_limit_and_cross_card_recheck(self):
  with self.s.db.transaction() as c:
   c.execute("UPDATE cards SET spending_limit=3000000 WHERE id='card-demo'")
   c.execute("INSERT INTO cards VALUES('card-other','demo','acct-savings','DEBIT','ACTIVE',3000000,1,0,1,?,0)",(time.time(),))
  t=self.create(amount=1500000);t2=self.create(amount=1500000,card_id='card-other');p=self.propose(t,'LOAN_DRAW');p2=self.propose(t2,'LOAN_DRAW');self.assertEqual(p2['status'],'WAITING_USER');self.assertEqual(self.decide(p)['status'],'COMPLETED')
  try:self.decide(p2)
  except Rejected:pass
  self.assertEqual(self.f.view('demo')['outstanding'],1500000)
 def test_unknown_quote_and_expiry(self):
  self.chat.market.view=lambda:{'quotes':[{'symbol':'XAU','snapshot':True,'stale':True,'price':999}]}
  with self.assertRaises(Rejected):self.create('gold_buy')
  t=self.create();p=self.propose(t,'LOAN_DRAW');start=self.balance()
  with patch('backend.banking.actions.time.time',return_value=t['expires_at']+1):
   try:self.decide(p)
   except Rejected:pass
  self.assertEqual(self.balance(),start)
 def test_owner_amount_inputs_and_missing_holding(self):
  for data in ({'type':'loan','card_id':'card-demo','amount':True,'months':3},{'type':'loan','card_id':'card-demo','amount':10000,'months':2},{'type':'gold_buy','card_id':'card-demo','units':0}):
   with self.assertRaises(Rejected):self.f.create('demo',data)
  with self.assertRaises(Rejected):self.f.create('other',{'type':'loan','card_id':'card-demo','amount':10000,'months':3})
  t=self.create('gold_sell');self.assertEqual(self.propose(t,'GOLD_SELL')['status'],'BLOCKED')
  with self.assertRaises(Rejected):self.f.propose('other',{'contract_id':t['id'],'operation':'GOLD_SELL'})
 def test_schedule_rounding_and_dates(self):
  for n in (3,6,12):
   rows=schedule(10001,n,now=1769817600);self.assertEqual(sum(r['principal'] for r in rows),10001);self.assertEqual(len(rows),n);self.assertTrue(all(r['total']==r['principal']+r['fee'] for r in rows))
 def test_gold_requires_suitable_profile_and_rechecks(self):
  with self.s.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=2 WHERE customer_id='demo'")
  t=self.create('gold_buy');self.assertEqual(self.propose(t,'GOLD_BUY')['status'],'BLOCKED')
  with self.s.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=3 WHERE customer_id='demo'")
  p=self.propose(t,'GOLD_BUY');start=self.balance()
  with self.s.db.transaction() as c:c.execute("UPDATE risk_profiles SET risk_level=1 WHERE customer_id='demo'")
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.balance(),start)

if __name__=='__main__':unittest.main()
