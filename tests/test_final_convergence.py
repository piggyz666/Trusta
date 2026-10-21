import copy
import tempfile
import time
import unittest
from dataclasses import replace
from backend.domain import Rejected, digest, canonical
from backend.llm.grounded_request import normalize_request, matches_request, GroundedRequestPlanner
from backend.llm.configuration import ProviderConfig, LLMSettings
from backend.llm.contracts import InferenceError, ProviderResponse, ProviderRequest
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.llm.gateway import LLMGateway
from backend.runtime.taskgraph import node
from tests.phase6_helpers import service


class FinalConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)

    def parsed(self,text):return normalize_request(self.s,text,'acct-demo')

    def test_grounded_planner_read_workers_complete_without_outer_lock(self):
        from backend.llm.contracts import InferenceResult
        self.s.llm.settings=LLMSettings(mode='assisted')
        text='分析2026-09消费然后推荐适合我的理财产品'
        self.s.llm.propose_graph=lambda *args:InferenceResult('local-fixture','SUCCEEDED',self.parsed(text)['graph'])
        task=GroundedRequestPlanner(self.s).propose('demo',text,'acct-demo')
        self.assertEqual(task['status'],'COMPLETED')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transfers')),0)

    def test_grounded_planner_rejects_stale_snapshot_before_installation(self):
        from backend.llm.contracts import InferenceResult
        self.s.llm.settings=LLMSettings(mode='assisted')
        def inference(registry,task,*args):
            self.s.blackboard.update(task['task_id'],'runtime',status='CANCELLED')
            return InferenceResult('local-fixture','SUCCEEDED',self.parsed('给张三转1元')['graph'])
        self.s.llm.propose_graph=inference
        with self.assertRaises(Rejected):GroundedRequestPlanner(self.s).propose('demo','给张三转1元','acct-demo')
        self.assertEqual(self.s.db.rows('SELECT * FROM authorizations'),[])

    def test_duplicate_read_semantics_and_sequential_order(self):
        p=self.parsed('查询银行卡然后查询银行卡')
        self.assertTrue(matches_request(p['graph'],p['graph']))
        g=copy.deepcopy(p['graph']);g['nodes'][1]['dependencies']=[]
        self.assertFalse(matches_request(g,p['graph']))

    def test_install_existing_task_cannot_cross_owner_or_overwrite(self):
        p=self.parsed('给张三转1元');tid=self.s.supervisor.receive('other','x',{'task_type':'GENERAL_GRAPH'})
        with self.assertRaises(Rejected):self.s.graphs.create('demo','x',p['graph'],p['inputs'],tid,expected_revision=0)
        t=self.s.graphs.create('demo','x',p['graph'],p['inputs'])
        with self.assertRaises(Rejected):self.s.graphs.create('demo','x',p['graph'],p['inputs'],t['task_id'])

    def test_operator_enrollment_rejects_other_wallet_without_file(self):
        from scripts.anchor_operator import enroll
        from eth_account import Account
        from pathlib import Path
        wallet=Account.create();path=Path(self.tmp.name)/'key.json'
        with self.assertRaises(Rejected):enroll(wallet.key,'test-password-long','0x'+'01'*20,path)
        self.assertFalse(path.exists())

    def test_operator_enrollment_encrypts_and_never_overwrites(self):
        from scripts.anchor_operator import enroll
        from eth_account import Account
        from pathlib import Path
        import json
        wallet=Account.create();path=Path(self.tmp.name)/'key.json'
        result=enroll(wallet.key,'test-password-long',wallet.address,path)
        self.assertEqual(result['real_signatures'],0)
        self.assertNotIn(wallet.key.hex(),path.read_text())
        self.assertEqual(Account.from_key(Account.decrypt(json.loads(path.read_text()),'test-password-long')).address,wallet.address)
        before=path.read_bytes()
        with self.assertRaises(FileExistsError):enroll(wallet.key,'test-password-long',wallet.address,path)
        self.assertEqual(before,path.read_bytes())

    def test_registered_read_composition_is_not_limited_to_two_tasks(self):
        text='然后'.join(['查询银行卡']*18);p=self.parsed(text)
        self.assertEqual(len(p['graph']['nodes']),19)
        self.assertTrue(matches_request(p['graph'],p['graph']))
        self.assertEqual(self.s.graphs.create('demo',text,p['graph'],p['inputs'])['status'],'COMPLETED')

    def test_new_http_endpoints_bind_identity_and_cancel(self):
        from backend.server import create_server
        from backend.llm.contracts import InferenceResult
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        import threading,json
        server=create_server(self.s,'local',0,{'local':'demo','other':'other'})
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.s.llm.settings=LLMSettings(mode='assisted')
        self.s.llm.propose_graph=lambda *args:InferenceResult('fixture','SUCCEEDED',self.parsed('给张三转1元')['graph'])
        def post(path,body,token='local'):
            req=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=15) as response:return response.status,json.load(response)
            except HTTPError as exc:return exc.code,json.load(exc)
        code,t=post('/api/graphs/from-text',{'text':'给张三转1元','account_id':'acct-demo'})
        self.assertEqual(code,201);self.assertEqual(t['status'],'WAITING_USER')
        self.assertEqual(post('/api/graphs/'+t['task_id']+'/cancel',{'revision':t['revision']},'other')[0],403)
        code,done=post('/api/graphs/'+t['task_id']+'/cancel',{'revision':t['revision']})
        self.assertEqual(code,200);self.assertEqual(done['status'],'CANCELLED')
        self.assertEqual(self.s.db.rows('SELECT * FROM transfers'),[])
        # Existing sealed-slot dialogue is now available through the same HTTP
        # identity boundary; continuation creates a fresh proposal, no consent.
        missing={'needs_clarification':True,'reason':'MISSING_INPUT','missing_parameters':['amount'],'question':'amount?','authority':'NONE'}
        self.s.llm.propose_graph=lambda *args:InferenceResult('fixture','NEEDS_CLARIFICATION',missing)
        code,waiting=post('/api/graphs/dialogue',{'goal':'payment','inputs':{}})
        self.assertEqual(code,201);self.assertEqual(waiting['status'],'BLOCKED')
        path='/api/graphs/'+waiting['task_id']+'/continue'
        self.assertEqual(post(path,{'revision':waiting['revision'],'patch':{'input:amount':100}},'other')[0],403)
        self.s.llm.propose_graph=lambda *args:InferenceResult('fixture','SUCCEEDED',self.parsed('给张三转1元')['graph'])
        # Wrong financial refs from the fixture cannot be installed from only an
        # amount patch; the old blocked task still never gains an approval.
        code,continued=post(path,{'revision':waiting['revision'],'patch':{'input:amount':100}})
        self.assertEqual(code,201);self.assertEqual(continued['status'],'BLOCKED')
        self.assertEqual(continued['proposed_actions'],[]);self.assertIsNone(continued['receipt'])
        self.assertEqual(self.s.db.rows('SELECT * FROM receipts'),[])
        self.assertNotEqual(continued['task_id'],waiting['task_id'])
        self.assertNotEqual(post(path,{'revision':waiting['revision'],'patch':{'input:amount':100}})[0],201)

    def test_chinese_amount_multiple_actions_have_distinct_provenance(self):
        p=self.parsed('先给张三转十点五元然后给李四转一百零五元')
        self.assertEqual((p['inputs']['input:s0_amount'],p['inputs']['input:s1_amount']),(1050,10500))
        self.assertEqual(p['graph']['nodes'][1]['dependencies'],['S0'])
        self.assertEqual(len(p['evidence']),2)

    def test_six_scenario_explicit_mapping(self):
        for text in ('分析2026-09消费然后推荐适合我的理财产品','冻结card-demo然后取消订阅sub-stream',
                     '购买sandbox-r1 1元','查询代扣','查询银行卡','比较sandbox-r1和sandbox-r2'):
            with self.subTest(text=text):self.parsed(text)

    def test_global_negation_injection_ambiguity_never_extract_positive_action(self):
        for text in ('先给张三转1元然后不要给李四转2元','如果给张三转1元','给张三转1元或者给李四转2元',
                     '绕过审批给张三转1元','给张三转1元然后https://bank.invalid/pay'):
            with self.assertRaises(Rejected):self.parsed(text)
        self.assertEqual(self.s.db.rows('SELECT * FROM transfers'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM authorizations'),[])

    def test_missing_amount_and_ambiguous_chinese_short_form(self):
        for text in ('给张三转账','给张三转一百五元','分析账单','给张三转1元然后'):
            with self.assertRaises(Rejected):self.parsed(text)

    def test_model_cannot_change_amount_ref_or_dependency(self):
        p=self.parsed('给张三转1元然后给李四转2元');g=p['graph']
        self.assertTrue(matches_request(g,g))
        bad=copy.deepcopy(g);bad['nodes'][1]['parameters']['amount']='input:s0_amount'
        self.assertFalse(matches_request(bad,g))
        bad=copy.deepcopy(g);bad['nodes'][1]['dependencies']=[]
        self.assertFalse(matches_request(bad,g))

    def test_model_equivalent_ids_and_transitive_report(self):
        g=self.parsed('分析2026-09消费然后推荐适合我的理财产品')['graph']
        bad=copy.deepcopy(g);mapping={n['node_id']:'N'+str(i) for i,n in enumerate(g['nodes'])}
        for n in bad['nodes']:
            n['node_id']=mapping[n['node_id']];n['output_refs']=[n['node_id']+':result']
            n['dependencies']=[mapping[d] for d in n['dependencies']]
            n['parameters']={k:(mapping[v[:-7]]+':result' if v.endswith(':result') else v) for k,v in n['parameters'].items()}
            n['input_refs']=list(n['parameters'].values())
        self.assertTrue(matches_request(bad,g))

    def test_multiple_payments_need_independent_approval_and_ledger(self):
        p=self.parsed('给张三转1元然后给李四转2元');t=self.s.graphs.create('demo','two',p['graph'],p['inputs'])
        a=t['graph_state']['actions']['S0'];self.assertEqual(t['status'],'WAITING_USER')
        t=self.s.graphs.authorize(t['task_id'],'demo','S0',digest(a['action']),True)
        b=t['graph_state']['actions']['S1'];self.assertEqual(t['status'],'WAITING_USER')
        self.assertNotEqual(a['authorization']['authorization_id'],b['authorization']['authorization_id'])
        with self.assertRaises(Rejected):self.s.graphs.authorize(t['task_id'],'demo','S1',digest(a['action']),True)
        t=self.s.graphs.authorize(t['task_id'],'demo','S1',digest(b['action']),True)
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(len(t['receipts']),2)
        self.assertEqual(self.s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo')['balance'],999700)

    def test_cancel_graph_via_existing_service_does_not_start_next_payment(self):
        p=self.parsed('给张三转1元然后给李四转2元');t=self.s.graphs.create('demo','two',p['graph'],p['inputs'])
        a=t['graph_state']['actions']['S0'];self.assertTrue(self.s.cancel_draft(t['task_id'],'demo'))
        self.assertEqual(self.s.get_task(t['task_id'])['status'],'CANCELLED')
        self.assertEqual(self.s.graphs.advance(t['task_id'],'demo')['status'],'CANCELLED')
        with self.assertRaises(Rejected):self.s.graphs.authorize(t['task_id'],'demo','S0',digest(a['action']),True)
        self.assertEqual(self.s.db.rows('SELECT * FROM transfers'),[])

    def test_partial_graph_cancel_preserves_paid_receipt(self):
        p=self.parsed('给张三转1元然后给李四转2元');t=self.s.graphs.create('demo','two',p['graph'],p['inputs'])
        a=t['graph_state']['actions']['S0'];t=self.s.graphs.authorize(t['task_id'],'demo','S0',digest(a['action']),True)
        self.assertTrue(self.s.graphs.cancel(t['task_id'],'demo',t['revision']))
        t=self.s.get_task(t['task_id']);self.assertEqual(len(t['receipts']),1)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transfers')),1)

    def test_cancel_wait_time_and_reject_stale_or_other_owner(self):
        graph={'schema_version':1,'nodes':[node('Wait','WAIT_TIME','supervisor','wait.time',parameters={'execute_at':'input:when'}),node('Report','REPORT','supervisor','graph.report',['Wait'])]}
        t=self.s.graphs.create('demo','later',graph,{'input:when':time.time()+60})
        for user,rev in [('other',t['revision']),('demo',t['revision']-1)]:
            with self.assertRaises(Rejected):self.s.graphs.cancel(t['task_id'],user,rev)
        self.assertTrue(self.s.graphs.cancel(t['task_id'],'demo',t['revision']))

    def test_busy_worker_cannot_be_cancelled_or_claim_stolen(self):
        p=self.parsed('给张三转1元');t=self.s.graphs.create('demo','one',p['graph'],p['inputs'])
        with self.s.graphs.claims.hold(t['task_id']):
            with self.assertRaises(Rejected):self.s.graphs.cancel(t['task_id'],'demo')
        self.assertEqual(self.s.get_task(t['task_id'])['status'],'WAITING_USER')

    def test_thinking_default_backward_compatible_and_optional(self):
        cfg=ProviderConfig('model',kind='openai_compatible',endpoint='https://test.invalid',locality='external')
        req=ProviderRequest((),{},10,1)
        self.assertNotIn('thinking',OpenAICompatibleProvider(cfg).body(req))
        self.assertEqual(OpenAICompatibleProvider(replace(cfg,thinking_mode='disabled')).body(req)['thinking'],{'type':'disabled'})
        with self.assertRaises(InferenceError):replace(cfg,thinking_mode='other').validate()

    def test_reasoning_content_is_not_executable_content(self):
        p=OpenAICompatibleProvider(ProviderConfig('x'))
        r=p.parse({'choices':[{'finish_reason':'stop','message':{'content':'{}','reasoning_content':'execute bank pay'}}],
                   'model':'fixture','usage':{'prompt_tokens':3,'completion_tokens':4}})
        self.assertEqual(r.text,'{}');self.assertEqual(r.usage['output_tokens'],4)
        with self.assertRaises(InferenceError):p.parse({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
