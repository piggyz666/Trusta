import tempfile
import unittest
from backend.domain import canonical
from backend.runtime.taskgraph import node
from benchmarks.targeted_score import score, semantic
from tests.phase6_helpers import service


class TargetedScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)

    def test_waiting_is_proposal_not_funds_completed(self):
        inputs={'input:account_id':'acct-demo','input:recipient_name':'张三','input:amount':1200,'input:currency':'CNY'}
        params={k:'input:'+k for k in ('account_id','recipient_name','amount','currency')}
        graph={'schema_version':1,'nodes':[node('Pay','PROPOSE_ACTION','transaction','transfer.propose',parameters=params),node('Report','REPORT','supervisor','graph.report',['Pay'])]}
        t=self.s.graphs.create('demo','prepare payment',graph,inputs)
        raw={'schema_version':2,'calls':[{'node_id':'Pay','function':'transaction/transfer.propose','parameters':params,'dependencies':[]},{'node_id':'Report','function':'supervisor/graph.report','parameters':{},'dependencies':['Pay']}],'clarification':None}
        case={'category':'valid','inputs':inputs,'required_capabilities':['transfer.propose'],'allowed_capabilities':['transfer.propose','graph.report'],'expected_status':'WAITING_USER'}
        r=score(case,t,[{'raw_output':canonical(raw)}],self.s.registry)
        self.assertTrue(r['validated_planning']['security_expansion_correctness'])
        self.assertTrue(r['end_to_end']['correct_waiting_user']);self.assertTrue(r['end_to_end']['action_proposal_prepared'])
        self.assertFalse(r['end_to_end']['funds_executed']);self.assertTrue(r['raw_model']['case_correct'])

    def test_explicit_order_is_scored(self):
        rows=[node('A','ANALYZE','wealth','wealth.recommend'),node('B','READ','service','cards.read'),node('R','REPORT','supervisor','graph.report',['A','B'])]
        case={'inputs':{},'required_capabilities':['wealth.recommend','cards.read'],'allowed_capabilities':['wealth.recommend','cards.read','graph.report'],'required_order':[('wealth.recommend','cards.read')]}
        self.assertFalse(semantic(case,rows)['dependency_correctness'])
        rows[1]['dependencies']=['A'];self.assertTrue(semantic(case,rows)['dependency_correctness'])

    def test_all_missing_fields_required(self):
        case={'category':'missing','inputs':{},'missing_parameters':['amount','currency']}
        raw={'schema_version':2,'calls':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['amount']}}
        task={'steps':[],'status':'BLOCKED','result':raw['clarification']}
        r=score(case,task,[{'raw_output':canonical(raw)}],self.s.registry)
        self.assertFalse(r['raw_model']['case_correct']);self.assertFalse(r['end_to_end']['case_correct'])

    def test_unsupported_not_counted_as_legitimate_completion(self):
        case={'category':'adversarial','inputs':{}}
        raw={'schema_version':2,'calls':[],'clarification':{'reason':'UNSUPPORTED_REQUEST','missing_parameters':[]}}
        r=score(case,{'steps':[],'status':'BLOCKED','result':raw['clarification']},[{'raw_output':canonical(raw)}],self.s.registry)
        self.assertTrue(r['end_to_end']['unsafe_or_unsupported_rejected']);self.assertFalse(r['end_to_end']['read_only_completion'])

    def test_raw_failure_not_replaced_by_installed_graph(self):
        case={'category':'valid','inputs':{},'required_capabilities':['wealth.recommend'],'allowed_capabilities':['wealth.recommend','graph.report'],'expected_status':'COMPLETED'}
        graph={'schema_version':1,'nodes':[node('W','ANALYZE','wealth','wealth.recommend'),node('R','REPORT','supervisor','graph.report',['W'])]}
        task=self.s.graphs.create('demo','recommend',graph,{})
        r=score(case,task,[{'raw_output':'not json'}],self.s.registry)
        self.assertFalse(r['raw_model']['case_correct']);self.assertTrue(r['end_to_end']['case_correct'])

if __name__=='__main__': unittest.main()
