import copy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from backend.domain import canonical
from backend.llm.configuration import LLMSettings,ProviderConfig
from backend.llm.contracts import InferenceError,ProviderResponse
from benchmarks.provider_evaluation import DATASET,guard_candidate,grounded_output,run_evaluation
from scripts.phase5_demo import demo


class ProviderEvaluationTests(unittest.TestCase):
    def test_curated_offline_controls_honestly_report_rejected_hallucinations(self):
        report=run_evaluation()
        self.assertTrue(report['passed'])
        self.assertEqual(report['evaluation_mode'],'OFFLINE_FIXTURES')
        self.assertFalse(report['real_model_experiment_verified'])
        self.assertFalse(report['bank_tools_available'])
        self.assertEqual(report['metrics']['parameters']['numerator'],13)
        rejected=[row for row in report['cases'] if row['actual']['guard']=='REJECT']
        self.assertEqual(len(rejected),2)
        self.assertTrue(all(row['actual']['guard_reason'] for row in rejected))

    def test_invented_identity_risk_and_authorization_fields_rejected(self):
        original=grounded_output('冻结card-demo')
        for field,value in (('target_id','card-1234'),('risk_level','5'),('approved','true'),('balance','999999')):
            changed=copy.deepcopy(original)
            changed['fields']=[{'name':field,'value':value}]
            with self.assertRaises(InferenceError):
                guard_candidate('冻结card-demo',changed)

    def test_two_provider_implementations_are_replaceable_without_bank_authority(self):
        class CandidateProvider:
            def infer(self,request):
                text=json.loads(request.messages[-1]['content'])['text']
                return ProviderResponse(canonical(grounded_output(text)))
        for name,model in (('enterprise','private-model-a'),('local','local-model-b')):
            settings=LLMSettings(mode='shadow',providers=(ProviderConfig(name,kind='http',model=model,
                endpoint='http://127.0.0.1:12345/infer',max_sensitivity='restricted',data_policy_reviewed=True),))
            report=run_evaluation(settings,name,CandidateProvider())
            self.assertTrue(report['passed'])
            self.assertEqual(report['provider'],name)
            self.assertFalse(report['bank_tools_available'])
            self.assertFalse(report['real_model_experiment_verified'])

    def test_provider_timeout_has_explicit_failure_without_fallback_fabrication(self):
        class TimeoutProvider:
            def infer(self,request):
                raise InferenceError('LLM_TIMEOUT',retryable=True)
        report=run_evaluation(provider=TimeoutProvider())
        self.assertFalse(report['passed'])
        self.assertTrue(all(row['actual']['guard_reason']=='LLM_TIMEOUT' and row['actual']['candidate'] is None for row in report['cases']))

    def test_actual_mapped_http_and_openai_compatible_protocols_with_fixture_server(self):
        observed=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append(body['model'])
                text=json.loads(body['messages'][-1]['content'])['text']
                content=canonical(grounded_output(text))
                payload={'choices':[{'finish_reason':'stop','message':{'content':content}}]} if self.path=='/chat' else {'output':{'text':content}}
                data=canonical(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            for kind,path in (('http','/infer'),('openai_compatible','/chat')):
                cfg=ProviderConfig(kind,kind=kind,model='synthetic-protocol-fixture',endpoint=f'http://127.0.0.1:{server.server_port}'+path,
                                   max_sensitivity='restricted',data_policy_reviewed=True)
                report=run_evaluation(LLMSettings(mode='shadow',providers=(cfg,)),cfg.name)
                self.assertTrue(report['passed'],[(row['case_id'],row['failure_reason']) for row in report['cases'] if not row['passed']])
                self.assertFalse(report['real_model_experiment_verified'])
            self.assertEqual(len(observed),2*len(json.loads(DATASET.read_text(encoding='utf-8'))))
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()


class SixScenarioTests(unittest.TestCase):
    def test_six_http_scenarios_restart_proofs_and_time_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            report=demo(directory)
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['scenarios']),6)
        self.assertEqual(len(report['record_proofs']),11)
        self.assertEqual(report['time_scenario']['transaction_count'],1)
        self.assertFalse(report['real_bank_connected'])
        self.assertFalse(report['real_llm_evaluated'])
