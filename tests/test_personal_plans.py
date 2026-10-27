import unittest
from datetime import date,timedelta
from tests.test_retail import RetailTests
from backend.banking.personal import Personal
from backend.domain import Rejected
class PersonalTests(RetailTests):
 def setUp(self):super().setUp();self.p=Personal(self.s)
 def make_plan(self,budget=5000):
  today=date.today().isoformat();return self.p.create('demo',{'name':'周末计划','kind':'wash','place':'深圳','start':today,'end':today,'budget':budget})['plan_id']
 def linked(self,pid):
  oid=self.r.create('demo',{'product_id':'wash','card_id':'card-demo','plan_id':pid})['order_id'];return oid,self.r.propose('demo',oid)
 def test_plan_confirm_budget_and_refund(self):
  pid=self.make_plan();oid,p=self.linked(pid);self.assertEqual(self.p.plans('demo')['plans'][0]['spent'],0);self.decide(p)
  self.assertEqual(self.p.plans('demo')['plans'][0]['spent'],3500)
  _,second=self.linked(pid);self.assertEqual(second['status'],'BLOCKED')
  self.decide(self.r.propose('demo',oid,True));self.assertEqual(self.p.plans('demo')['plans'][0]['spent'],0)
 def test_budget_change_invalidates_existing_confirmation(self):
  pid=self.make_plan();_,p=self.linked(pid);start=self.balance()
  self.p.update('demo',{'plan_id':pid,'budget':2000,'status':'ACTIVE'})
  try:self.decide(p)
  except Rejected:pass
  self.assertEqual(self.balance(),start)
 def test_plan_owner_and_archived_and_budget_floor(self):
  pid=self.make_plan()
  with self.assertRaises(Rejected):self.p.update('other',{'plan_id':pid,'budget':10000,'status':'ACTIVE'})
  oid,p=self.linked(pid);self.decide(p)
  with self.assertRaises(Rejected):self.p.update('demo',{'plan_id':pid,'budget':1000,'status':'ACTIVE'})
  self.p.update('demo',{'plan_id':pid,'budget':5000,'status':'ARCHIVED'})
  with self.assertRaises(Rejected):self.linked(pid)
  self.decide(self.r.propose('demo',oid,True));self.assertEqual(self.p.plans('demo')['plans'][0]['spent'],0)
 def test_profile_is_private_and_suggestions_opt_in(self):
  tomorrow=(date.today()+timedelta(days=1)).isoformat()
  body={'name':'测试用户','city':'深圳','has_car':True,'suggestions':False,'interests':['旅行'],'dates':[{'name':'生日','kind':'birthday','date':tomorrow}]}
  self.p.save_profile('demo',body);self.assertEqual(self.p.profile('other')['name'],'');self.assertEqual(self.p.plans('demo')['suggestions'],[])
  self.p.save_profile('demo',body|{'suggestions':True});self.assertEqual(self.p.plans('demo')['suggestions'][0]['days'],1)
 def test_two_pending_orders_cannot_overrun_budget(self):
  import time
  with self.s.db.transaction() as c:c.execute("INSERT INTO cards VALUES('card-saving','demo','acct-savings','DEBIT','ACTIVE',500000,1,0,1,?,0)",(time.time(),))
  pid=self.make_plan();_,a=self.linked(pid)
  oid=self.r.create('demo',{'product_id':'wash','card_id':'card-saving','plan_id':pid})['order_id'];b=self.r.propose('demo',oid)
  start=self.balance();self.decide(a)
  try:self.decide(b)
  except Rejected:pass
  self.assertEqual(self.balance(),start-3500);self.assertEqual(self.p.plans('demo')['plans'][0]['spent'],3500)
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-savings'")['balance'],500000)
if __name__=='__main__':unittest.main()
