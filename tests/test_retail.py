import tempfile,unittest
from pathlib import Path
from backend.config import Config
from backend.service import TrustaService
from backend.chat.service import ChatService
from backend.banking.retail import Retail
from backend.domain import Rejected
class RetailTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=TrustaService(Config(db_path=str(Path(self.tmp.name)/'test.db'),signing_key=b'x'*32,communication_version=3,banking_version=5));self.r=Retail(ChatService(self.s))
 def tearDown(self):self.tmp.cleanup()
 def prepare(self):
  oid=self.r.create('demo',{'product_id':'wash','card_id':'card-demo'})['order_id'];return oid,self.r.propose('demo',oid)
 def decide(self,p):
  card=p['cards'][0]
  return self.r.decide('demo',{k:card[k] for k in ('task_id','action_id','action_hash')}|{'approve':True})
 def balance(self):return self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
 def test_confirm_pay_refund_and_no_replay(self):
  start=self.balance();oid,p=self.prepare();self.assertEqual(self.balance(),start);self.assertEqual(p['status'],'WAITING_USER',p)
  result=self.decide(p);self.assertEqual(result['status'],'COMPLETED',result);self.assertEqual(self.balance(),start-3500)
  self.assertEqual(self.r.view('demo')['orders'][0]['status'],'PAID')
  refund=self.r.propose('demo',oid,True);self.assertEqual(refund['status'],'WAITING_USER',refund);self.decide(refund);self.assertEqual(self.balance(),start)
  again=self.r.propose('demo',oid,True);self.assertEqual(again['status'],'BLOCKED',again)
  rows=self.s.db.rows("SELECT * FROM consumption_records WHERE merchant=?",(self.r.view('demo')['orders'][0]['title'],));self.assertEqual(len(rows),2);self.assertEqual({r['kind'] for r in rows},{'PURCHASE','REFUND'})
 def test_forged_price_and_owner(self):
  with self.assertRaises(Rejected):self.r.create('demo',{'product_id':'wash','card_id':'card-demo','amount':1})
  with self.assertRaises(Rejected):self.r.create('other',{'product_id':'wash','card_id':'card-demo'})
  oid,p=self.prepare()
  with self.assertRaises(Rejected):self.r.propose('other',oid)
 def test_insufficient_balance_and_stale_card(self):
  with self.s.db.transaction() as c:c.execute("UPDATE accounts SET balance=10 WHERE id='acct-demo'")
  oid,p=self.prepare();self.assertEqual(p['status'],'BLOCKED',p);self.assertEqual(self.balance(),10)
 def test_recheck_before_execution(self):
  oid,p=self.prepare();start=self.balance()
  with self.s.db.transaction() as c:c.execute("UPDATE cards SET status='FROZEN',version=version+1 WHERE id='card-demo'")
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.balance(),start)
 def test_design_is_owned_persistent_and_not_financial(self):
  start=self.balance()
  self.r.design('demo',{'card_id':'card-demo','theme':'champagne','nickname':'生活卡'})
  self.assertEqual(self.r.view('demo')['cards'][0]['design'],{'theme':'champagne','nickname':'生活卡'})
  self.assertEqual(self.balance(),start)
  for owner,theme in [('other','ruby'),('demo','unknown')]:
   with self.assertRaises(Rejected):self.r.design(owner,{'card_id':'card-demo','theme':theme,'nickname':'x'})
 def test_second_card_debits_only_selected_account(self):
  import time
  with self.s.db.transaction() as c:c.execute("INSERT INTO cards VALUES('card-saving','demo','acct-savings','DEBIT','ACTIVE',500000,1,0,1,?,0)",(time.time(),))
  start=self.balance();saving=self.s.db.one("SELECT balance FROM accounts WHERE id='acct-savings'")['balance']
  oid=self.r.create('demo',{'product_id':'wash','card_id':'card-saving'})['order_id'];p=self.r.propose('demo',oid)
  self.decide(p)
  self.assertEqual(self.balance(),start)
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-savings'")['balance'],saving-3500)
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-savings'")['balance'],saving-3500)
  self.decide(self.r.propose('demo',oid,True))
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-savings'")['balance'],saving)
if __name__=='__main__':unittest.main()
