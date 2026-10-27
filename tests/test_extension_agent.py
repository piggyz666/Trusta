import unittest
from tests import test_agent_upgrade
from backend.chat.validator import ToolCallValidator,Clarify,InjectionDetected
from backend.chat.rules import rule_calls
from backend.chat.tool_selection import relevant_tools

class ExtendedAgentTests(unittest.TestCase):
 setUp=test_agent_upgrade.UpgradeTests.setUp
 say=test_agent_upgrade.UpgradeTests.say
 def validator(self,text):return ToolCallValidator([text],self.chat.catalog('demo'),lambda value:[])
 def test_rule_deposit_proposal_confirm_and_reject(self):
  text='存入100元定期存款，90天';calls=rule_calls(text,self.chat.catalog('demo'));self.assertEqual(calls[0]['name'],'propose_extension')
  before=self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  r=self.say(text,calls);c=next(c for c in r['reply']['cards'] if c['type']=='confirm')
  self.assertEqual(c['action_type'],'DEPOSIT_OPEN');self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
  self.chat.decide(self.session['session_id'],'demo',c['action_id'],c['action_hash'],False)
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
 def test_fx_grounding_no_practice_or_injected_approval(self):
  text='买入100美元';calls=rule_calls(text,self.chat.catalog('demo'));v=self.validator(text);args=calls[0]['arguments'];result=v.validate('propose_extension',args)
  self.assertEqual(result[0]['normalized']['finance_draft']['units'],10000)
  self.assertEqual(result[0]['normalized']['finance_draft']['quote_mode'],'reference')
  with self.assertRaises(Clarify):v.validate('propose_extension',args|{'quantity':{'span':'100','value':'999'}})
  with self.assertRaises(InjectionDetected):v.validate('propose_extension',args|{'approve':True})
  with self.assertRaises(Clarify):self.validator('查汇率').validate('propose_extension',args)
 def test_product_reference_and_missing_repayment_mode(self):
  text='申购100元稳健债券练习基金';calls=rule_calls(text,self.chat.catalog('demo'));steps=self.validator(text).validate(calls[0]['name'],calls[0]['arguments']);self.assertEqual(steps[0]['normalized']['finance_draft']['product_id'],'practice-bond')
  args=calls[0]['arguments']|{'resource':{'id':'practice-index','span':'稳健债券练习基金'}}
  with self.assertRaises(Clarify):self.validator(text).validate('propose_extension',args)
  with self.assertRaises(Clarify):self.validator('信用账户还款').validate('propose_extension',{'operation':'CARD_REPAY'})
  self.assertIn('propose_extension',relevant_tools('定期存款和基金'))
 def test_agent_fund_pending_and_todos_read(self):
  text='申购100元稳健债券练习基金';r=self.say(text,rule_calls(text,self.chat.catalog('demo')))
  c=next(c for c in r['reply']['cards'] if c['type']=='confirm');self.assertEqual(c['action_type'],'FUND_SUBSCRIBE')
  before=self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  self.assertEqual(self.s.db.rows('SELECT * FROM advanced_positions'),[])
  r=self.say('查看统一待办',[{'name':'get_extended','arguments':{'page':'todos'}}]);self.assertIn('待办',r['reply']['text']);self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before)
 def test_four_decimal_units_and_successful_confirmed_agent_deposit(self):
  text='赎回97.3658份稳健债券练习基金';call=rule_calls(text,self.chat.catalog('demo'))[0]
  self.assertEqual(self.validator(text).validate(call['name'],call['arguments'])[0]['normalized']['finance_draft']['units'],973658)
  args=call['arguments']|{'quantity':{'span':'97.3658','value':'97.3659'}}
  with self.assertRaises(Clarify):self.validator(text).validate('propose_extension',args)
  text='存入100元定期存款，90天';before=self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance']
  r=self.say(text,rule_calls(text,self.chat.catalog('demo')));c=next(c for c in r['reply']['cards'] if c['type']=='confirm')
  self.chat.decide(self.session['session_id'],'demo',c['action_id'],c['action_hash'],True)
  self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],before-10000)
  from backend.banking.extensions import Extensions
  self.assertEqual(Extensions(self.chat).view('demo')['deposits'][0]['principal'],10000)
