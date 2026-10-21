"""Offline evaluator safety/consistency tests; these never call model APIs."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from benchmarks.glm_evaluation import (Budget, CapturingProvider, FrozenRequestCollector,
    configuration, dimensions, request_document, save, sha, paired_request)
from benchmarks.final_live_evaluation import dataset, evaluate
from backend.llm.contracts import InferenceError, ProviderRequest


class GLMEvaluationTests(unittest.TestCase):
    def request(self):
        return ProviderRequest(({'role':'system','content':'JSON only'},
                                {'role':'user','content':'synthetic'}),{'type':'object'},4096,60)

    def ledger(self, path, **extra):
        save(path,dict(requests=0,reserved_usd=0,max_requests=100,max_usd=1,attempts=[],**extra))

    def test_failure_and_duplicate_reservation_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'budget.json';self.ledger(path)
            index,reserve=Budget(path).reserve('FC01','deepseek',self.request())
            Budget(path).settle(index,None,'LLM_TIMEOUT')
            with self.assertRaises(InferenceError):Budget(path).reserve('FC01','deepseek',self.request())
            state=json.loads(path.read_text());self.assertEqual(state['requests'],1)
            self.assertEqual(state['reserved_usd'],reserve)

    def test_request_and_dollar_limits_prevent_dispatch(self):
        for field,value in [('requests',100),('reserved_usd',1)]:
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'budget.json';self.ledger(path)
                state=json.loads(path.read_text());state[field]=value;save(path,state)
                with self.assertRaises(InferenceError):Budget(path).reserve('FC01','gemini',self.request())
                self.assertEqual(json.loads(path.read_text()),state)

    def test_unexpected_usage_stops_next_request(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'budget.json';self.ledger(path)
            index,_=Budget(path).reserve('FC01','deepseek',self.request())
            Budget(path).settle(index,{'input_tokens':1000000,'output_tokens':1000000},None)
            with self.assertRaises(InferenceError):Budget(path).reserve('FC02','glm',self.request())

    def test_input_drift_rejected_before_network_or_reservation(self):
        req=self.request()
        provider=CapturingProvider(configuration('glm'),{'id':'FC01'},request_document(paired_request(req)),None,[])
        with self.assertRaisesRegex(InferenceError,'EVALUATION_INPUT_DRIFT'):
            provider.infer(replace(req,max_output_tokens=2048))

    def test_all_provider_prompts_schemas_and_caps_are_identical(self):
        for case in dataset():
            hashes=[]
            for name in ('glm','deepseek','gemini'):
                collector=FrozenRequestCollector()
                row=evaluate(case,name,provider_config=configuration(name),provider=collector,captures=[],amended_metrics=True)
                self.assertEqual(len(collector.requests),1,case['id'])
                hashes.append(sha(collector.requests[0]))
                self.assertTrue(row['score']['audit_valid'])
                self.assertTrue(row['score']['bank_state_unchanged'])
            self.assertEqual(len(set(hashes)),1,case['id'])

    def test_all_five_dimensions_are_covered_without_new_gold(self):
        self.assertEqual(len(dataset()),23)
        all_dimensions={d for row in dataset() for d in dimensions(row)}
        self.assertEqual(all_dimensions,{'Planning','Banking Task','Transaction Safety','Prompt Injection','Multi-turn Context'})
        self.assertEqual(sum(bool(row.get('previous')) for row in dataset()),1)

    def test_gemini_wire_mapping_preserves_semantic_prompts(self):
        req=self.request()
        glm=CapturingProvider(configuration('glm'),{},None,None,[]).delegate.body(req)
        gemini=CapturingProvider(configuration('gemini'),{},None,None,[]).delegate.body(req)
        self.assertEqual(glm['messages'][0]['content'],gemini['systemInstruction']['parts'][0]['text'])
        self.assertEqual(glm['messages'][1]['content'],gemini['contents'][0]['parts'][0]['text'])
        self.assertNotIn('tools',glm);self.assertNotIn('tools',gemini)
        self.assertNotIn('responseJsonSchema',gemini['generationConfig'])

    def test_shared_transport_timeout_never_extends_gateway_deadline(self):
        self.assertEqual(paired_request(self.request()).timeout_seconds,55)
        with self.assertRaisesRegex(InferenceError,'EVALUATION_PREFLIGHT_TIMEOUT'):
            paired_request(replace(self.request(),timeout_seconds=54))

    def test_context_cases_preserve_same_input_for_all_providers(self):
        from benchmarks.glm_context_evaluation import context_dataset
        rows=context_dataset()
        self.assertEqual(len({r['id'] for r in rows}),10)
        for case in rows:
            hashes=[]
            for name in ('glm','deepseek','gemini'):
                collector=FrozenRequestCollector()
                row=evaluate(case,name,provider_config=configuration(name),provider=collector,captures=[],amended_metrics=True)
                self.assertEqual(len(collector.requests),1,case['id'])
                hashes.append(sha(collector.requests[0]))
                self.assertTrue(row['score']['bank_state_unchanged'])
                self.assertTrue(row['score']['audit_valid'])
            self.assertEqual(len(set(hashes)),1,case['id'])


if __name__=='__main__':unittest.main()
