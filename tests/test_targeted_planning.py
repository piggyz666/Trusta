import copy
import json
import tempfile
import unittest
from dataclasses import replace
from backend.domain import Rejected,canonical
from backend.llm.compact_graph import output_schema,messages,normalize
from backend.llm.configuration import LLMSettings,ProviderConfig
from backend.llm.contracts import ProviderResponse,InferenceError
from backend.llm.gateway import LLMGateway
from backend.llm.proposal_dialogue import ProposalDialogue
from tests.phase6_helpers import service


def bill():
    return {'schema_version':2,'clarification':None,'calls':[
        {'node_id':'Tx','function':'finance/transaction.list','parameters':{'account_id':'input:account_id','month':'input:month'},'dependencies':[]},
        {'node_id':'Summary','function':'finance/finance.summary','parameters':{'records':'Tx:result'},'dependencies':['Tx']},
        {'node_id':'Report','function':'supervisor/graph.report','parameters':{},'dependencies':['Summary']}]}


class PlanningOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)
        self.inputs={'input:account_id':'acct-demo','input:month':'2026-09'}

    def gateway(self,outputs,repair=0,timeout=10):
        class Provider:
            def __init__(self):self.requests=[]
            def infer(self,request):
                self.requests.append(request)
                output=outputs[min(len(self.requests)-1,len(outputs)-1)]
                if isinstance(output,Exception):raise output
                return ProviderResponse(canonical(output),'fixture-model',{'input_tokens':12,'output_tokens':8})
        p=Provider();cfg=ProviderConfig('fixture',max_sensitivity='restricted')
        self.s.llm=LLMGateway(LLMSettings(mode='assisted',graph_format='compact_v2',graph_repair_attempts=repair,timeout_seconds=timeout,providers=(cfg,)),self.s.db,self.s.audit,{'fixture':p})
        self.s.llm.identity_guard=self.s.crypto_registry.require_active;return p

    def test_function_and_parameter_schema_bound(self):
        schema=output_schema(self.s.registry)
        for item in schema['properties']['calls']['items']['anyOf']:
            self.assertTrue(item['properties']['function']['enum'])
            self.assertFalse(item['properties']['parameters']['additionalProperties'])
        item=next(i for i in schema['properties']['calls']['items']['anyOf'] if 'finance/transaction.list' in i['properties']['function']['enum'])
        self.assertEqual(set(item['properties']['parameters']['required']),{'account_id','month'})

    def test_prompt_semantics_without_financial_values(self):
        p=messages(self.s.registry,'analyze',self.inputs|{'input:amount':987654321,'input:target_id':'secret-target'})
        raw=canonical(p);self.assertNotIn('987654321',raw);self.assertNotIn('secret-target',raw)
        self.assertIn('transaction.recurring_history is raw',raw)
        self.assertIn('EXACTLY',raw);self.assertIn('missing_parameters',raw)

    def test_repair_is_off_by_default_and_invalid_usage_retained(self):
        invalid=bill();invalid['calls'][1]['parameters']['records']='Tx:records'
        p=self.gateway([invalid,bill()]);t=self.s.graphs.propose('demo','summarize',self.inputs)
        self.assertEqual(len(p.requests),1);self.assertFalse(t['steps'])
        r=json.loads(self.s.db.one('SELECT body FROM llm_calls')['body'])
        self.assertEqual(r['attempts'][0]['usage']['output_tokens'],8)

    def test_one_repair_same_context_no_raw_or_customer_echo(self):
        invalid=bill();invalid['calls'][1]['parameters']['records']='Tx:records'
        p=self.gateway([invalid,bill()],1);t=self.s.graphs.propose('demo','summarize',self.inputs)
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(len(p.requests),2)
        self.assertLessEqual(p.requests[1].timeout_seconds,p.requests[0].timeout_seconds)
        feedback=p.requests[1].messages[-1]['content'];self.assertNotIn('acct-demo',feedback);self.assertNotIn('Tx:records',feedback)
        self.assertIn('PROVENANCE_OR_DEPENDENCY',feedback)

    def test_failed_repair_stops_at_two_calls(self):
        invalid=bill();invalid['calls'][1]['dependencies']=[]
        p=self.gateway([invalid],1);t=self.s.graphs.propose('demo','summarize',self.inputs)
        self.assertEqual(len(p.requests),2);self.assertEqual(t['status'],'BLOCKED')

    def test_transport_disconnect_retry_shares_total_budget(self):
        invalid=bill();invalid['calls'][1]['dependencies']=[]
        p=self.gateway([InferenceError('LLM_PROVIDER_UNAVAILABLE',True),invalid,bill()],1)
        t=self.s.graphs.propose('demo','summarize',self.inputs)
        self.assertEqual(len(p.requests),2);self.assertFalse(t['steps'])
        self.assertEqual(p.requests[0].messages,p.requests[1].messages)

    def test_authority_injection_not_repaired(self):
        invalid=bill();invalid['authorization_id']='forged'
        p=self.gateway([invalid,bill()],1);t=self.s.graphs.propose('demo','summarize',self.inputs)
        self.assertEqual(len(p.requests),1);self.assertFalse(t['steps'])

    def test_literal_money_not_repaired_or_executed(self):
        invalid={'schema_version':2,'clarification':None,'calls':[{'node_id':'Pay','function':'transaction/transfer.propose','parameters':{'amount':'1200','currency':'input:currency','account_id':'input:account_id','recipient_name':'input:recipient_name'},'dependencies':[]}]}
        p=self.gateway([invalid],1);t=self.s.graphs.propose('demo','prepare',self.inputs)
        self.assertFalse(t['steps']);self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),0)

    def test_repair_settings_bounded(self):
        for value in (True,-1,2,1.0):
            with self.assertRaises(Exception):LLMSettings(graph_repair_attempts=value).validate()

    def test_dialogue_preserves_slots_and_does_not_inherit_consent(self):
        question={'schema_version':2,'calls':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}}
        p=self.gateway([question,bill()]);dialogue=ProposalDialogue(self.s)
        t=dialogue.start('demo','summarize',{'input:account_id':'acct-demo'})
        resumed=dialogue.resume('demo',t['task_id'],t['revision'],{'input:month':'2026-09'})
        self.assertNotEqual(t['task_id'],resumed['task_id']);self.assertEqual(resumed['status'],'COMPLETED')
        self.assertEqual(resumed['graph_state']['inputs'],self.inputs);self.assertFalse(resumed['authorizations'])
        with self.assertRaises(Rejected):dialogue.resume('demo',t['task_id'],t['revision'],{'input:month':'2026-09'})

    def test_dialogue_owner_tamper_and_existing_slot_replacement_rejected(self):
        question={'schema_version':2,'calls':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}}
        self.gateway([question]);d=ProposalDialogue(self.s);t=d.start('demo','summarize',{'input:account_id':'acct-demo'})
        with self.assertRaises(Rejected):d.resume('alice',t['task_id'],t['revision'],{'input:month':'2026-09'})
        with self.assertRaises(Rejected):d.resume('demo',t['task_id'],t['revision'],{'input:account_id':'acct-alice'})
        context=copy.deepcopy(t['context']);context['proposal_dialogue']['body']['inputs']['input:account_id']='acct-alice'
        t=self.s.blackboard.update(t['task_id'],'runtime',context=context)
        with self.assertRaises(Rejected):d.resume('demo',t['task_id'],t['revision'],{'input:month':'2026-09'})

if __name__=='__main__':unittest.main()
