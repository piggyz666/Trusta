import tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
from backend.config import Config
from backend.service import TrustaService
from backend.chat.service import ChatService
from backend.chat.llm import ScriptedToolClient
from backend.banking.finance import Finance
from backend.chat.activity import ActivityFeed
from backend.chat.trial_budget import reserve
from backend.domain import Rejected

class UpgradeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.s=TrustaService(Config(db_path=str(Path(self.tmp.name)/'bank.db'),signing_key=b'x'*32,communication_version=3,banking_version=5,safety_profile='competition'))
  self.model=ScriptedToolClient({});self.chat=ChatService(self.s,self.model);self.session=self.chat.create_session('demo','test');self.rev=self.session['revision'];self.i=0
 def say(self,text,calls):
  self.model.script[text]=calls;self.i+=1
  r=self.chat.send(self.session['session_id'],'demo',str(self.i),self.rev,text);self.rev=r['revision'];return r
 def test_finance_agent_requires_confirmation_then_reconciles_overview(self):
  start=self.s.transaction.balance('demo','acct-demo')['balance']
  args={'operation':'LOAN_DRAW','amount':{'value':'100','span':'100元'},'months':{'value':3,'span':'3期'}}
  r=self.say('贷款100元，分3期',[{'name':'propose_finance','arguments':args}]);c=next(c for c in r['reply']['cards'] if c['type']=='confirm')
  self.assertEqual(self.s.transaction.balance('demo','acct-demo')['balance'],start)
  result=self.chat.decide(self.session['session_id'],'demo',c['action_id'],c['action_hash'],True)
  self.assertEqual(self.s.transaction.balance('demo','acct-demo')['balance'],start+10000)
  v=ActivityFeed(self.chat).overview('demo');self.assertEqual(v['finance']['debt_minor'],10000);self.assertEqual(v['finance']['next_due_minor'],3364)
  self.assertEqual(v['finance']['assets_at_cost_minor']-v['finance']['net_at_cost_minor'],10000)
 def test_scope_rejects_without_requesting_model(self):
  r=self.say('帮我写一首诗',[]);self.assertIn('只处理 TRUSTA',r['reply']['text']);self.assertEqual(self.model.prompts,[])
 def test_clarification_cannot_smuggle_general_prose(self):
  r=self.say('嗯',[{'name':'ask_user','arguments':{'slot':'other','question':'这里是编程教程'}}]);self.assertNotIn('编程教程',r['reply']['text'])
 def test_archive_invalidates_pending_without_balance_change(self):
  f=Finance(self.chat);t=f.create('demo',{'type':'loan','card_id':'card-demo','amount':10000,'months':3})['contract'];p=f.propose('demo',{'contract_id':t['id'],'operation':'LOAN_DRAW'});c=next(c for c in p['cards'] if c['type']=='confirm')
  f.archive('demo',{'contract_id':t['id']});before=self.s.transaction.balance('demo','acct-demo')['balance']
  with self.assertRaises(Rejected) as caught:f.decide('demo',{k:c[k] for k in ('task_id','action_id','action_hash')}|{'approve':True})
  self.assertEqual(caught.exception.code,'POLICY_BLOCKED');self.assertEqual(self.s.transaction.balance('demo','acct-demo')['balance'],before)
  self.assertEqual(f.propose('demo',{'contract_id':t['id'],'operation':'LOAN_DRAW'})['status'],'BLOCKED')

 def test_completed_gold_cannot_be_proposed_again(self):
  from backend.banking.finance import errors
  sample={'target':{'type':'gold_sell','status':'COMPLETED','amount':100,'expires_at':9999999999,'units':1},'card':{'status':'ACTIVE','online_enabled':True,'spending_limit':10000},'account':{'balance':10000},'position':{'units':100}}
  self.assertIn('FINANCE_STATE_INVALID',errors('GOLD_SELL',sample,{'amount':100,'currency':'CNY'},time.time()))
 def test_trial_reservation_survives_restarts_and_expires(self):
  f=str(Path(self.tmp.name)/'budget.db');self.assertTrue(reserve(f,10000,1024,now=1))
  calls=1
  while reserve(f,10000,1024,now=2):calls+=1
  self.assertLess(calls,20);self.assertFalse(reserve(f,10000,1024,now=3));self.assertFalse(reserve(f,1,1,now=86402))
 def test_finance_read_and_fabricated_amount(self):
  r=self.say('看看我的黄金和贷款',[{'name':'get_finance','arguments':{}}]);self.assertIn('待还本金',r['reply']['text'])
  r=self.say('贷款100元，分3期',[{'name':'propose_finance','arguments':{'operation':'LOAN_DRAW','amount':{'value':'10000','span':'100元'},'months':{'value':3,'span':'3期'}}}]);self.assertFalse(any(c['type']=='confirm' for c in r['reply']['cards']))

 def test_selected_tools_keep_actual_subscription_and_mixed_business_names(self):
  from backend.chat.tool_selection import relevant_tools
  chosen=relevant_tools('取消订阅，给张三转100元，再查黄金')
  self.assertTrue({'propose_subscription_cancel','propose_transfer','get_finance'}<=chosen)
  self.assertNotIn('propose_cancel_subscription',chosen)

 def test_budget_stops_before_http(self):
  from backend.chat.llm import DeepSeekToolClient, LLMUnavailable
  with patch.dict('os.environ',{'TRUSTA_CHAT_LLM_TRIAL_BUDGET_FILE':str(Path(self.tmp.name)/'limit.db')}), patch('backend.chat.trial_budget.reserve',return_value=False), patch('backend.chat.llm.build_opener') as opener:
   client=DeepSeekToolClient(api_key='unit-test-placeholder',model='deepseek-flash')
   with self.assertRaises(LLMUnavailable) as caught:client.complete('system',[])
   self.assertEqual(caught.exception.code,'LLM_BUDGET_EXHAUSTED');opener.assert_not_called()

if __name__=='__main__':unittest.main()
