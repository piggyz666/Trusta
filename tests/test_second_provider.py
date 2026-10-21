from dataclasses import replace
from pathlib import Path
import unittest

from backend.domain import canonical
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceError, ProviderRequest, ProviderResponse
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from benchmarks.second_provider_evaluation import evaluate, summarize
from tests.test_phase6_graph import read_graph

ROOT = Path(__file__).resolve().parents[1]


class SecondProviderTests(unittest.TestCase):
    def config(self, **changes):
        return ProviderConfig('compatible', kind='openai_compatible',
            endpoint='https://example.invalid/chat/completions', locality='external', **changes)

    def test_json_mode_does_not_claim_schema_enforcement(self):
        adapter = OpenAICompatibleProvider(self.config(json_output=True))
        request = ProviderRequest(({'role':'user','content':'Return JSON'},), {'type':'object'}, 100, 1)
        self.assertEqual(adapter.body(request)['response_format'], {'type':'json_object'})
        self.assertTrue(adapter.describe_capabilities()['json_output'])
        self.assertFalse(adapter.describe_capabilities()['structured_outputs'])
        self.assertNotIn('tools', adapter.body(request))
        self.assertNotIn('response_format', OpenAICompatibleProvider(self.config()).body(request))

    def test_json_mode_invalid_and_conflicting_config_rejected(self):
        for cfg in (self.config(json_output='true'), self.config(json_output=1),
                    self.config(json_output=True, structured_outputs=True),
                    replace(self.config(json_output=True),kind='http'),
                    replace(self.config(json_output=True),kind='gemini'),
                    ProviderConfig('mock',json_output=True)):
            with self.subTest(cfg=cfg), self.assertRaises(InferenceError):cfg.validate()

    def test_public_example_never_enables_customer_data_egress(self):
        settings = LLMSettings.load(ROOT/'config/llm.deepseek.public-example.toml')
        self.assertEqual(settings.mode,'shadow')
        cfg=settings.providers[0]
        self.assertEqual(cfg.max_sensitivity,'public')
        self.assertFalse(cfg.data_policy_reviewed)
        self.assertTrue(cfg.json_output)
        self.assertEqual(cfg.api_key_env,'DEEPSEEK_API_KEY')

    def test_reasoning_content_is_not_returned_or_mistaken_for_plan(self):
        response=OpenAICompatibleProvider(self.config()).parse({'model':'deployment',
            'choices':[{'finish_reason':'stop','message':{'content':'{}','reasoning_content':'private-reasoning'}}]})
        self.assertEqual(response.text,'{}')
        self.assertNotIn('reasoning',canonical(response.__dict__))

    def fixture(self):
        return {'id':'fixture','group':'normal_read','goal':'账单汇总',
            'inputs':{'input:account':'acct-demo','input:month':'2026-09'},
            'expected_status':'COMPLETED','required_capabilities':['transactions.read','finance.summary','graph.report']}

    def provider(self, config, captures, fault):
        class Fixture:
            attempts=0
            def infer(self,request):
                self.attempts+=1
                if fault=='unavailable':raise InferenceError('LLM_PROVIDER_UNAVAILABLE',True)
                if fault=='invalid':return ProviderResponse('{"schema_version":1,"nodes":[],"unexpected":true}')
                # No capture entry: a fixture must never count as a live model.
                return ProviderResponse(canonical(read_graph()|{'clarification':None}))
        return Fixture()

    def test_route_switch_and_retryable_failure_use_same_core(self):
        for order in (('deepseek','gemini'),('gemini','deepseek')):
            for fault in (None,'unavailable'):
                with self.subTest(order=order,fault=fault):
                    row=evaluate(self.fixture(),order,fault,provider_factory=self.provider)
                    self.assertTrue(row['passed'])
                    self.assertEqual(row['calls'][0]['provider'],order[1] if fault else order[0])
                    self.assertEqual(row['adapter_attempts'][order[1]],1 if fault else 0)
                    self.assertEqual(row['real_model_responses'],0)
                    self.assertEqual(row['bank_writes'],0)

    def test_invalid_graph_never_falls_back(self):
        row=evaluate(self.fixture(),('deepseek','gemini'),'invalid',provider_factory=self.provider)
        self.assertEqual(row['status'],'BLOCKED')
        self.assertEqual(row['adapter_attempts']['gemini'],0)
        self.assertFalse(row['installed_graph'])
        self.assertEqual(row['calls'][0]['attempts'][0]['error_code'],'REJECT_PLAN')

    def test_fallback_never_weakens_sensitivity(self):
        row=evaluate(self.fixture(),('deepseek','gemini'),'unavailable',True,provider_factory=self.provider)
        self.assertEqual(row['status'],'BLOCKED')
        self.assertEqual(row['adapter_attempts']['gemini'],0)
        self.assertEqual(row['calls'][0]['rejected_routes']['gemini'],'DATA_SENSITIVITY')

    def test_shadow_plan_does_not_install_even_when_valid(self):
        row=evaluate(self.fixture(),mode='shadow',provider_factory=self.provider)
        self.assertFalse(row['installed_graph'])
        self.assertEqual(row['calls'][0]['status'],'SUCCEEDED')
        self.assertEqual(row['bank_writes'],0)

    def test_failures_stay_in_denominator_and_fixtures_are_not_live(self):
        rows=[evaluate(self.fixture(),provider_factory=self.provider),
              evaluate(self.fixture(),fault='invalid',provider_factory=self.provider)]
        metrics=summarize(rows)['all']
        self.assertEqual(metrics['numerator'],1)
        self.assertEqual(metrics['denominator'],2)
        self.assertEqual(metrics['real_model_responses'],0)


if __name__=='__main__':unittest.main()
