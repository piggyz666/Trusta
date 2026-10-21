import tempfile,unittest
from unittest.mock import patch
from backend.llm.configuration import ProviderConfig,LLMSettings
from backend.llm.gateway import LLMGateway
from backend.llm.contracts import ProviderResponse,InferenceError
from backend.domain import canonical
from tests.phase6_helpers import service
from tests.test_phase6_graph import read_graph

class Provider:
    def __init__(self,graph,error=None):self.graph=graph;self.error=error;self.calls=0
    def infer(self,request):
        self.calls+=1
        if self.error:raise self.error
        return ProviderResponse(canonical(self.graph))
class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)
        self.inputs={'input:account':'acct-demo','input:month':'2026-09'}
    def setup_gateway(self,providers,configs=None):
        configs=configs or tuple(ProviderConfig(name=name,max_sensitivity='restricted') for name in providers)
        self.s.llm=LLMGateway(LLMSettings(mode='assisted',providers=configs),self.s.db,self.s.audit,providers)
        self.s.llm.identity_guard=self.s.crypto_registry.require_active
    def test_real_gateway_route_to_validator_and_runtime(self):
        p=Provider(read_graph());self.setup_gateway({'local':p})
        t=self.s.graphs.propose('demo','read and analyze September',self.inputs)
        self.assertEqual(t['status'],'COMPLETED');self.assertEqual(p.calls,1)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM llm_calls')),1)
    def test_public_fallback_never_receives_restricted_data(self):
        p=Provider(read_graph());self.setup_gateway({'public':p},(ProviderConfig(name='public'),))
        t=self.s.graphs.propose('demo','analyze my bank history',self.inputs)
        self.assertEqual(t['status'],'BLOCKED');self.assertEqual(p.calls,0)
    def test_malicious_graph_rejected_without_execution(self):
        g=read_graph();g['nodes'][0]['node_type']='EXECUTE';self.setup_gateway({'local':Provider(g)})
        t=self.s.graphs.propose('demo','fake system authority',self.inputs)
        self.assertEqual(t['status'],'BLOCKED');self.assertFalse(t['steps']);self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
    def test_retryable_fixture_fallback_is_distinguished_from_live(self):
        first=Provider({},InferenceError('LLM_PROVIDER_UNAVAILABLE',True));second=Provider(read_graph())
        self.setup_gateway({'first':first,'second':second})
        self.assertEqual(self.s.graphs.propose('demo','report',self.inputs)['status'],'COMPLETED');self.assertEqual((first.calls,second.calls),(1,1))
    def test_invalid_output_does_not_fallback(self):
        first=Provider({});second=Provider(read_graph());self.setup_gateway({'first':first,'second':second})
        self.assertEqual(self.s.graphs.propose('demo','report',self.inputs)['status'],'BLOCKED');self.assertEqual(second.calls,0)
    def test_disabled_gateway_is_fail_closed(self):
        t=self.s.graphs.propose('demo','report',self.inputs);self.assertEqual(t['status'],'BLOCKED');self.assertEqual(t['errors'],['LLM_DISABLED'])
        from dataclasses import replace
        self.setup_gateway({'local':Provider(read_graph())});self.s.llm.settings=replace(self.s.llm.settings,mode='shadow')
        t=self.s.graphs.propose('demo','shadow report',self.inputs)
        self.assertEqual(t['errors'],['LLM_SHADOW_ONLY']);self.assertEqual(t['steps'],[])
    def test_restricted_user_values_are_not_projected(self):
        p=Provider(read_graph());self.setup_gateway({'local':p})
        original=p.infer
        def checked(req):
            self.assertNotIn('private-account-id',canonical(req.messages));return original(req)
        p.infer=checked
        t=self.s.graphs.propose('demo','report',self.inputs|{'input:secret':'private-account-id'})
        self.assertEqual(t['status'],'COMPLETED')
