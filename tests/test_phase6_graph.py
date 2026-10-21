from copy import deepcopy
import tempfile
import time
import unittest
from backend.runtime.taskgraph import node, TaskGraphValidator, SecurityExpansionCompiler, SAFETY
from backend.domain import Rejected,digest,canonical
from tests.phase6_helpers import service

def read_graph():
    return {'schema_version':1,'nodes':[node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),
        node('Sum','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),node('Report','REPORT','supervisor','graph.report',['Sum'])]}

def cancel_graph():
    return {'schema_version':1,'nodes':[node('Cancel','PROPOSE_ACTION','service','service.propose',parameters={'action_type':'input:kind','account_id':'input:account','target_id':'input:target','values':'input:values'}),
                                      node('Wealth','ANALYZE','wealth','wealth.recommend',['Cancel']),node('Report','REPORT','supervisor','graph.report',['Wealth'])]}

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.s=service(self.temp.name)
        self.inputs={'input:account':'acct-demo','input:month':'2026-09','input:kind':'SUBSCRIPTION_CANCEL','input:target':'sub-stream','input:values':{}}

    def test_read_analysis_report_uses_bank_facts(self):
        t=self.s.graphs.create('demo','report',read_graph(),self.inputs)
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(t['result']['Sum:result']['net_spend'],542500)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_compiler_inserts_every_security_stage_and_redirects_dependencies(self):
        c=SecurityExpansionCompiler(self.s.registry).compile(cancel_graph(),self.inputs)
        self.assertEqual([r['name'] for r in c['steps'][1:9]],list(SAFETY))
        self.assertEqual(c['steps'][9]['dependencies'],['CancelXRECEIPT'])

    def test_cancel_then_wealth_is_paused_until_independent_approval(self):
        t=self.s.graphs.create('demo','cancel then wealth',cancel_graph(),self.inputs)
        self.assertEqual(t['status'],'WAITING_USER');self.assertNotIn('Wealth:result',t['graph_state']['outputs'])
        self.assertEqual(self.s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')['status'],'ACTIVE')
        r=t['graph_state']['actions']['Cancel']
        done=self.s.graphs.authorize(t['task_id'],'demo','Cancel',digest(r['action']),True)
        self.assertEqual(done['status'],'COMPLETED');self.assertEqual(len(done['receipts']),1);self.assertIn('Wealth:result',done['result'])

    def test_multiple_actions_require_independent_hash_and_auth(self):
        g=cancel_graph();second=node('Freeze','PROPOSE_ACTION','service','service.propose',['Cancel'],{'action_type':'input:kind2','account_id':'input:account','target_id':'input:target2','values':'input:values'})
        g['nodes']=[g['nodes'][0],second,node('Report','REPORT','supervisor','graph.report',['Freeze'])]
        inputs=self.inputs|{'input:kind2':'CARD_FREEZE','input:target2':'card-demo'}
        t=self.s.graphs.create('demo','two actions',g,inputs);r=t['graph_state']['actions']['Cancel']
        t=self.s.graphs.authorize(t['task_id'],'demo','Cancel',digest(r['action']),True)
        self.assertEqual(t['status'],'WAITING_USER');r2=t['graph_state']['actions']['Freeze']
        self.assertNotEqual(r['authorization']['authorization_id'],r2['authorization']['authorization_id'])
        with self.assertRaises(Rejected): self.s.graphs.authorize(t['task_id'],'demo','Freeze',digest(r['action']),True)
        self.assertEqual(self.s.graphs.authorize(t['task_id'],'demo','Freeze',digest(r2['action']),False)['status'],'COMPLETED')
        self.assertEqual(self.s.gateway.read('service','demo','card.get',resource_id='card-demo')['status'],'ACTIVE')

    def test_direct_execution_and_safety_nodes_rejected(self):
        for kind in SAFETY:
            g=read_graph();g['nodes'][0]['node_type']=kind
            with self.subTest(kind=kind),self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs)

    def test_cycles_missing_dependencies_and_fake_capabilities(self):
        variants=[]
        g=read_graph();g['nodes'][0]['dependencies']=['Report'];variants.append(g)
        g=read_graph();g['nodes'][0]['dependencies']=['Missing'];variants.append(g)
        g=read_graph();g['nodes'][0]['capability']='payments.create';variants.append(g)
        for g in variants:
            with self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs)

    def test_untrusted_bank_facts_and_output_provenance_rejected(self):
        g=read_graph();g['nodes'][1]['parameters']={'records':'input:records'};g['nodes'][1]['input_refs']=['input:records']
        with self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs|{'input:records':[]})
        g=read_graph();g['nodes'][1]['dependencies']=[]
        with self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs)

    def test_raw_endpoint_db_code_fake_authorization_rejected(self):
        for key,value in [('endpoint','https://bank/pay'),('db','sqlite'),('authorization_id','forged'),('code','exec(x)')]:
            g=read_graph();g['nodes'][0][key]=value
            with self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs)

    def test_parallel_writes_rejected(self):
        g=cancel_graph();g['nodes'].append(node('CancelTwo','PROPOSE_ACTION','service','service.propose',parameters=g['nodes'][0]['parameters']))
        with self.assertRaises(Rejected): TaskGraphValidator(self.s.registry).validate(g,self.inputs)

    def test_real_parallel_reads_and_join(self):
        g={'schema_version':1,'nodes':[node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),node('Subscriptions','READ','service','subscriptions.read'),node('Join','JOIN','supervisor','graph.join',['Read','Subscriptions'])]}
        t=self.s.graphs.create('demo','parallel read',g,self.inputs)
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(set(t['result']['Join:result']),{'Read','Subscriptions'})

    def test_parallel_frontier_workers_actually_overlap(self):
        import threading
        barrier=threading.Barrier(2);workers=set();original=self.s.graphs._semantic
        def measured(n,t,state):
            if n['node_type']=='READ': workers.add(threading.get_ident());barrier.wait(timeout=2)
            return original(n,t,state)
        self.s.graphs._semantic=measured
        g={'schema_version':1,'nodes':[node('A','READ','service','subscriptions.read'),node('B','READ','wealth','investments.products.read'),node('Join','JOIN','supervisor','graph.join',['A','B'])]}
        self.assertEqual(self.s.graphs.create('demo','parallel',g,{})['status'],'COMPLETED');self.assertEqual(len(workers),2)

    def test_false_branch_skips_financial_action_without_receipt(self):
        g=cancel_graph();g['nodes'].insert(0,node('Decision','BRANCH','supervisor','decision.boolean',parameters={'value':'input:choice'}))
        g['nodes'][1]['dependencies']=['Decision'];g['nodes'][1]['condition']={'ref':'Decision:result','equals':True}
        t=self.s.graphs.create('demo','conditional cancel',g,self.inputs|{'input:choice':False})
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(t['receipts'],[])
        self.assertEqual(self.s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')['status'],'ACTIVE')

    def test_user_selection_is_not_financial_authorization(self):
        g=cancel_graph();g['nodes'].insert(0,node('Choice','WAIT_USER','supervisor','wait.user',parameters={'deadline':'input:deadline'}));g['nodes'][1]['dependencies']=['Choice']
        t=self.s.graphs.create('demo','choose then authorize',g,self.inputs|{'input:deadline':time.time()+30})
        self.assertEqual(t['status'],'WAITING_USER')
        t=self.s.graphs.signal(t['task_id'],'demo','Choice',True)
        self.assertEqual(t['status'],'WAITING_USER');self.assertEqual(t['receipts'],[])

    def test_wait_time_persists_and_resumes(self):
        g={'schema_version':1,'nodes':[node('Time','WAIT_TIME','supervisor','wait.time',parameters={'execute_at':'input:due'}),node('Report','REPORT','supervisor','graph.report',['Time'])]}
        t=self.s.graphs.create('demo','time',g,{'input:due':time.time()+1});self.assertEqual(t['status'],'WAITING_TIME')
        self.s=service(self.temp.name);time.sleep(1.05)
        self.assertEqual(self.s.graphs.advance(t['task_id'],'demo')['status'],'COMPLETED')

    def test_restart_preserves_pending_graph_authorization(self):
        t=self.s.graphs.create('demo','cancel',cancel_graph(),self.inputs);r=t['graph_state']['actions']['Cancel']
        self.s=service(self.temp.name)
        self.assertEqual(self.s.graphs.authorize(t['task_id'],'demo','Cancel',digest(r['action']),True)['status'],'COMPLETED')

    def test_graph_storage_seal_rejects_tampered_input(self):
        t=self.s.graphs.create('demo','cancel',cancel_graph(),self.inputs);t['graph_state']['inputs']['input:target']='sub-gym'
        with self.s.db.transaction() as conn: conn.execute('UPDATE tasks SET body=? WHERE id=?',(canonical(t),t['task_id']))
        with self.assertRaises(Rejected): self.s.graphs.advance(t['task_id'],'demo')

    def test_unknown_bank_resource_never_installed(self):
        with self.assertRaises(Rejected): self.s.graphs.create('demo','fake card',cancel_graph(),self.inputs|{'input:target':'invented'})

    def test_runtime_cannot_omit_verification_or_override_compiler(self):
        t=self.s.graphs.create('demo','cancel',cancel_graph(),self.inputs)
        t['steps']=[p for p in t['steps'] if p['name']!='VERIFY']
        with self.s.db.transaction() as conn: conn.execute('UPDATE tasks SET body=? WHERE id=?',(canonical(t),t['task_id']))
        with self.assertRaises(Rejected): self.s.graphs.advance(t['task_id'],'demo')

    def test_suspended_agent_and_graph_owner_isolation(self):
        t=self.s.graphs.create('demo','cancel',cancel_graph(),self.inputs)
        with self.assertRaises(Rejected): self.s.graphs.advance(t['task_id'],'other')
        self.s.crypto_registry.set_status('service','SUSPENDED')
        r=t['graph_state']['actions']['Cancel'];done=self.s.graphs.authorize(t['task_id'],'demo','Cancel',digest(r['action']),True)
        self.assertNotEqual(done['status'],'COMPLETED');self.assertEqual(done['receipts'],[])
