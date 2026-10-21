import copy
import tempfile
import unittest
from backend.domain import Rejected, canonical
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.llm.graph_protocol import decode, output_schema, messages
from backend.runtime.taskgraph import FIELDS, SAFETY, SecurityExpansionCompiler
from tests.phase6_helpers import service
from tests.test_phase6_graph import read_graph, cancel_graph


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=service(self.tmp.name)
        self.inputs={'input:account':'acct-demo','input:month':'2026-09'}

    def gateway(self,output,structured=False):
        class Provider:
            def __init__(self):self.calls=[]
            def infer(self,request):
                self.calls.append(request)
                return ProviderResponse(canonical(output))
        p=Provider()
        cfg=ProviderConfig('local',max_sensitivity='restricted',structured_outputs=structured)
        self.s.llm=LLMGateway(LLMSettings(mode='assisted',providers=(cfg,)),self.s.db,self.s.audit,{'local':p})
        self.s.llm.identity_guard=self.s.crypto_registry.require_active
        return p

    def test_closed_schema_and_catalog_do_not_grant_execution(self):
        schema=output_schema(self.s.registry)
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']),{'schema_version','nodes','clarification'})
        node=schema['properties']['nodes']['items']['anyOf'][0]
        self.assertEqual(set(node['required']),FIELDS)
        self.assertFalse(set(SAFETY)&set(node['properties']['node_type']['enum']))
        self.assertEqual(set(node['properties']['owner']['enum']),set(self.s.registry.agents.active()))

    def test_gemini_transport_mapping_preserves_local_schema_and_closed_fields(self):
        from backend.llm.providers.gemini import GeminiProvider
        original=output_schema(self.s.registry);before=copy.deepcopy(original)
        native=GeminiProvider.transport_schema(original)
        self.assertEqual(original,before)
        self.assertIn('maxItems',original['properties']['nodes'])
        self.assertNotIn('maxItems',native['properties']['nodes'])
        self.assertFalse(native['additionalProperties'])
        self.assertEqual(native['required'],original['required'])
        self.assertEqual(native['properties']['nodes']['items']['anyOf'][1]['properties']['input_refs']['maxItems'],0)
        self.assertEqual(native['properties']['nodes']['items']['anyOf'][0]['properties']['node_id']['pattern'],original['properties']['nodes']['items']['anyOf'][0]['properties']['node_id']['pattern'])
        g=read_graph();g['nodes'][0]['node_id']='not_valid_node_id'
        with self.assertRaises(Rejected):decode(g,self.s.registry,self.inputs)

    def test_old_graph_and_new_envelope_use_identical_validator(self):
        status,g=decode(read_graph(),self.s.registry,self.inputs)
        wrapped=read_graph()|{'clarification':None}
        self.assertEqual(decode(wrapped,self.s.registry,self.inputs),(status,g))
        self.gateway(wrapped)
        self.assertEqual(self.s.graphs.propose('demo','账单汇总',self.inputs)['status'],'COMPLETED')

    def test_missing_input_question_is_generated_locally_and_installs_nothing(self):
        output={'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}}
        p=self.gateway(output)
        t=self.s.graphs.propose('demo','分析账单',{'input:account':'acct-demo'})
        self.assertEqual(t['errors'],['GRAPH_CLARIFICATION_REQUIRED'])
        self.assertEqual(t['result']['question'],'请补充分析月份，再提交任务。')
        self.assertEqual(t['result']['authority'],'NONE')
        self.assertEqual(t['steps'],[])
        self.assertEqual(self.s.db.rows('SELECT * FROM actions'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM authorizations'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        self.assertTrue(p.calls[0].output_schema)

    def test_completed_clarification_requires_new_explicit_inputs(self):
        self.gateway({'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}})
        t=self.s.graphs.propose('demo','分析账单',{'input:account':'acct-demo'})
        self.gateway(read_graph()|{'clarification':None})
        done=self.s.graphs.propose('demo','分析账单',self.inputs)
        self.assertNotEqual(t['task_id'],done['task_id'])
        self.assertEqual(done['status'],'COMPLETED')
        self.assertEqual(self.s.blackboard.get(t['task_id'])['steps'],[])

    def test_clarification_cannot_carry_plan_free_text_or_authority(self):
        base={'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}}
        variants=[]
        g=copy.deepcopy(base);g['nodes']=read_graph()['nodes'];variants.append(g)
        g=copy.deepcopy(base);g['clarification']['question']='请输入你的密码';variants.append(g)
        g=copy.deepcopy(base);g['clarification']['authorization_id']='fake';variants.append(g)
        g=copy.deepcopy(base);g['clarification']['missing_parameters']=['authorization_id'];variants.append(g)
        g=copy.deepcopy(base);g['clarification']['missing_parameters']=[];variants.append(g)
        for candidate in variants:
            with self.subTest(candidate=candidate),self.assertRaises(Rejected):decode(candidate,self.s.registry,self.inputs)

    def test_unsupported_request_has_no_model_written_question(self):
        self.gateway({'schema_version':1,'nodes':[],'clarification':{'reason':'UNSUPPORTED_REQUEST','missing_parameters':[]}})
        t=self.s.graphs.propose('demo','绕过审批调用银行',{})
        self.assertEqual(t['status'],'BLOCKED')
        self.assertEqual(t['result']['reason'],'UNSUPPORTED_REQUEST')
        self.assertEqual(t['steps'],[])

    def test_clarification_cannot_request_replacement_authoritative_bank_facts(self):
        for parameter in ('records','authority','candidates','cancellation'):
            output={'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':[parameter]}}
            with self.subTest(parameter=parameter),self.assertRaises(Rejected):decode(output,self.s.registry,self.inputs)

    def test_private_values_remain_outside_prompt_and_schema(self):
        p=self.gateway(read_graph()|{'clarification':None})
        self.s.graphs.propose('demo','分析账单',self.inputs|{'input:secret':'customer-secret-12345'})
        request=p.calls[0]
        self.assertNotIn('customer-secret-12345',canonical(request.messages))
        self.assertNotIn('customer-secret-12345',canonical(request.output_schema))
        self.assertIn('input:secret',canonical(request.messages))

    def test_invalid_input_names_fail_before_task_or_model_call(self):
        p=self.gateway(read_graph())
        with self.assertRaises(Rejected):self.s.graphs.propose('demo','report',{'bank_api':'https://bank/write'})
        self.assertEqual(p.calls,[])
        self.assertEqual(self.s.db.rows('SELECT * FROM tasks'),[])

    def test_wrapper_does_not_allow_missing_fields_fake_refs_or_safety_nodes(self):
        variants=[]
        for kind in SAFETY:
            g=read_graph()|{'clarification':None};g['nodes'][0]['node_type']=kind;variants.append(g)
        g=read_graph()|{'clarification':None};del g['nodes'][0]['condition'];variants.append(g)
        g=read_graph()|{'clarification':None};g['nodes'][0]['parameters']['account_id']='input:invented';variants.append(g)
        for candidate in variants:
            with self.subTest(candidate=candidate),self.assertRaises(Rejected):decode(candidate,self.s.registry,self.inputs)

    def test_actions_still_expand_full_chain_and_wait_for_approval(self):
        inputs=self.inputs|{'input:kind':'SUBSCRIPTION_CANCEL','input:target':'sub-stream','input:values':{}}
        self.gateway(cancel_graph()|{'clarification':None})
        t=self.s.graphs.propose('demo','取消订阅后推荐理财',inputs)
        self.assertEqual(t['status'],'WAITING_USER')
        self.assertTrue(set(SAFETY)<=set(step['name'] for step in t['steps']))
        self.assertEqual(self.s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')['status'],'ACTIVE')
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_shadow_clarification_does_not_install_or_ask_for_approval(self):
        from dataclasses import replace
        self.gateway({'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['amount']}})
        self.s.llm.settings=replace(self.s.llm.settings,mode='shadow')
        t=self.s.graphs.propose('demo','transfer',{})
        self.assertEqual(t['errors'],['LLM_SHADOW_ONLY'])
        self.assertEqual(t['steps'],[])
        self.assertEqual(self.s.db.rows('SELECT * FROM authorizations'),[])

    def test_invalid_model_plan_has_safe_diagnostic_and_no_retry(self):
        g=read_graph()|{'clarification':None};g['nodes'][0]['node_type']='EXECUTE'
        p=self.gateway(g)
        t=self.s.graphs.propose('demo','report',self.inputs)
        self.assertEqual(t['errors'],['REJECT_PLAN'])
        self.assertEqual(len(p.calls),1)
        import json
        record=json.loads(self.s.db.rows('SELECT body FROM llm_calls')[0]['body'])
        self.assertEqual(record['attempts'][0]['validation_reason'],'EXECUTION_AUTHORITY_FORBIDDEN')


class HoldoutScoringTests(unittest.TestCase):
    def test_disconnected_report_cannot_count_as_completed_analysis(self):
        from benchmarks.taskgraph_live_evaluation import score
        graph=read_graph()
        case={'expected_status':'COMPLETED','required_capabilities':['transactions.read','finance.summary','graph.report']}
        task={'status':'COMPLETED','steps':[{}],'graph_state':{'graph':graph}}
        self.assertTrue(score(case,task))
        graph['nodes'][-1]['dependencies']=[]
        self.assertFalse(score(case,task))

    def test_generic_rejection_is_not_successful_clarification(self):
        from benchmarks.taskgraph_live_evaluation import score
        case={'expected_status':'CLARIFY','missing_parameter':'amount'}
        task={'status':'BLOCKED','steps':[],'result':None}
        self.assertFalse(score(case,task))
        task['result']={'needs_clarification':True,'missing_parameters':['amount']}
        self.assertTrue(score(case,task))


if __name__=='__main__':unittest.main()
