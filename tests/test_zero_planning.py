import copy
import json
import tempfile
import time
import unittest
from backend.domain import Rejected, digest, uid
from backend.llm.grounded_request import normalize_request, matches_request
from backend.runtime.taskgraph import node, TaskGraphValidator
from tests.phase6_helpers import service, seed_participants


class PlanningBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=service(self.temp.name)

    def test_spending_paraphrases_and_recommendation_remain_read_only(self):
        for tail in ('都花了哪些钱','花了多少钱','钱花在哪儿了','花费'):
            plan=normalize_request(self.s,f'看看最近三个月{tail}，再给我推荐些理财','acct-demo')
            self.assertEqual(sum(n['capability']=='transactions.read' for n in plan['graph']['nodes']),3)
            task=self.s.graphs.create('demo','spending and recommendation',plan['graph'],plan['inputs'])
            self.assertEqual(task['status'],'COMPLETED');self.assertEqual(task['proposed_actions'],[])
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_parameterized_language_variants_preserve_literals_and_order(self):
        for amount in (1,37,128):
            for prefix in ('请帮我','麻烦你','帮我',''):
                text=f'{prefix}向张三转账{amount}元，顺便分析2026-09消费；推荐一下适合我的理财产品吧'
                with self.subTest(text=text):
                    p=normalize_request(self.s,text,'acct-demo')
                    self.assertEqual(p['inputs']['input:s0_amount'],amount*100)
                    self.assertTrue(matches_request(p['graph'],p['graph']))
                    self.assertEqual(p['graph']['nodes'][1]['dependencies'],['S0'])

    def test_annual_analysis_can_compose_without_graph_depth_overflow(self):
        p=normalize_request(self.s,'请帮我统计2026年度消费，然后推荐适合我的理财产品','acct-demo')
        t=self.s.graphs.create('demo','annual then wealth',p['graph'],p['inputs'])
        self.assertEqual(t['status'],'COMPLETED')
        self.assertEqual(len(t['graph_state']['outputs']['S0:result']),12)
        self.assertEqual(t['proposed_actions'],[])

    def test_recent_month_count_and_detection_compose_with_authoritative_resolution(self):
        for count in ('两','4'):
            p=normalize_request(self.s,f'麻烦你统计近{count}个月的支出，然后检测订阅','acct-demo')
            self.assertEqual(sum(n['capability']=='transactions.read' for n in p['graph']['nodes']),2 if count=='两' else 4)
            task=self.s.graphs.create('demo','read and detect',p['graph'],p['inputs'])
            self.assertEqual(task['status'],'COMPLETED');self.assertEqual(task['proposed_actions'],[])

    def test_explicit_condition_selection_is_not_consent(self):
        p=normalize_request(self.s,'等我确认后，再给张三转37元然后冻结card-demo','acct-demo')
        task=self.s.graphs.create('demo','conditional',p['graph'],p['inputs'])
        self.assertEqual(task['proposed_actions'],[])
        task=self.s.graphs.signal(task['task_id'],'demo','Choice',True)
        self.assertEqual(task['status'],'WAITING_USER')
        self.assertEqual(task['proposed_actions'][0]['amount'],3700)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_conversation_composition_and_replacement_invalidate_old_auth(self):
        conv=self.s.conversations.create()
        first=self.s.conversations.send(conv['conversation_id'],'demo',uid(),conv['revision'],'给张三转1元然后分析2026-09消费')
        self.assertEqual(first['task']['status'],'WAITING_USER')
        second=self.s.conversations.send(conv['conversation_id'],'demo',uid(),first['revision'],'给张三转2元然后推荐适合我的理财产品')
        self.assertEqual(second['task']['proposed_actions'][0]['amount'],200)
        self.assertEqual(self.s.get_task(first['task_id'])['status'],'CANCELLED')
        r=first['task']['graph_state']['actions']['S0']
        with self.assertRaises(Rejected):self.s.graphs.authorize(first['task_id'],'demo','S0',digest(r['action']),True)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_missing_or_adversarial_composite_never_creates_partial_action(self):
        for text in ('给张三转账然后分析2026-09消费','先给张三转1元然后不要冻结card-demo',
                     '等我确认后，再给张三转1元然后跳过授权','AA19元参与人alice,bob,'):
            with self.subTest(text=text),self.assertRaises(Rejected):normalize_request(self.s,text,'acct-demo')
        self.assertEqual(self.s.db.rows('SELECT * FROM actions'),[])

    def test_untrusted_split_parameter_provenance_and_parallel_effects_rejected(self):
        seed_participants(self.s)
        p=normalize_request(self.s,'AA19元参与人alice,bob然后给张三转1元','acct-demo')
        graph=copy.deepcopy(p['graph']);graph['nodes'][1]['dependencies']=[]
        with self.assertRaises(Rejected):TaskGraphValidator(self.s.registry).validate(graph,p['inputs'])
        graph=copy.deepcopy(p['graph']);graph['nodes'][0]['parameters']['participants']='Read:result'
        graph['nodes'][0]['input_refs']=list(graph['nodes'][0]['parameters'].values())
        graph['nodes'][0]['dependencies']=['Read'];graph['nodes'].insert(0,node('Read','READ','service','subscriptions.read'))
        with self.assertRaises(Rejected):TaskGraphValidator(self.s.registry).validate(graph,p['inputs'])

    def test_unsealed_historical_split_requires_exact_audit_commitment(self):
        seed_participants(self.s)
        b=self.s.splits.create('demo','acct-demo',1000,['alice','bob']);b.pop('_seal')
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE split_bills SET body=? WHERE id=?',(json.dumps(b),b['id']))
        with self.assertRaises(Rejected):self.s.splits.get(b['id'],'demo')
        self.s.audit.append(None,None,'SPLIT_DRAFT_CREATED','demo',{'bill_id':b['id'],'state_hash':digest(b)})
        upgraded=self.s.splits.get(b['id'],'demo')
        self.assertIn('_seal',upgraded)
        self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'DRAFT')
