"""Real loopback HTTP protocol tests; no paid APIs, banking or chain writes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import yaml

from backend.config import Config
from backend.domain import uid
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceError, InferenceContext, InferenceRequest
from backend.service import TrustaService
from llm import BaseLLMProvider, LLMModelConfig, LLMRequest, LLMResponse
from llm.bridge import BackendProviderBridge
from llm.gateway import LLMGateway
from llm.providers import create_provider


MESSAGES = ({'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'synthetic'})
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['amount'],
          'properties': {'amount': {'type': 'integer', 'minimum': 1, 'maximum': 9}}}


class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.calls.append((self.path, body, dict(self.headers)))
                content = '{"amount":3}'
                if self.path == '/invalid':
                    content = '{"amount":"3"}'
                if self.path == '/duplicate':
                    content = '{"amount":3,"amount":4}'
                if self.path == '/intent':
                    content = '{"task_type":"TRANSFER","needs_clarification":false}'
                if self.path == '/echo':
                    content = json.dumps({'answer': body['messages'][-1]['content']})
                payload = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                           'model': 'fixture-model', 'usage': {'prompt_tokens': 11, 'completion_tokens': 7}}
                if self.path == '/tool':
                    payload['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'pay'}}]
                if self.path == '/length':
                    payload['choices'][0]['finish_reason'] = 'length'
                if self.path == '/refusal':
                    payload['choices'][0]['message']['refusal'] = 'No'
                if self.path == '/gemini':
                    payload = {'candidates': [{'content': {'parts': [{'text': content}]}, 'finishReason': 'STOP'}],
                               'modelVersion': 'fixture-gemini', 'usageMetadata':
                               {'promptTokenCount': 11, 'candidatesTokenCount': 5, 'thoughtsTokenCount': 2}}
                if self.path == '/slow':
                    time.sleep(.25)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/sink')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(503 if self.path == '/unavailable' else 200)
                data = json.dumps(payload).encode()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.server.calls = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(3)
        cls.server.server_close()

    def setUp(self):
        self.server.calls.clear()

    def config(self, kind='openai_compatible', path='/ok', name='primary', timeout=1, **kwargs):
        return LLMModelConfig(ProviderConfig(name, kind=kind, model='explicit-model',
                    endpoint=f'http://127.0.0.1:{self.server.server_port}{path}', **kwargs), timeout, 100)

    def test_provider_interface_for_all_three_adapters(self):
        for kind, path in [('openai', '/ok'), ('openai_compatible', '/ok'), ('gemini', '/gemini')]:
            config = self.config(kind, path, api_key_env='TEST_LLM_KEY')
            adapter = create_provider(kind)
            self.assertIsInstance(adapter, BaseLLMProvider)
            with patch.dict(os.environ, {'TEST_LLM_KEY': 'local-fixture-key'}):
                result = adapter.generate(MESSAGES, SCHEMA, (), config)
            self.assertIsInstance(result, LLMResponse)
            self.assertEqual(json.loads(result.content), {'amount': 3})
            self.assertEqual(result.usage, {'input_tokens': 11, 'output_tokens': 7})
            self.assertEqual(result.finish_reason, 'stop')
            self.assertGreater(result.latency, 0)

    def test_wire_schema_and_prompt_are_preserved(self):
        from backend.domain import canonical
        exact_limit = len(canonical(list(MESSAGES))) + len(canonical(SCHEMA))
        bounded = self.config(max_input_chars=exact_limit)
        self.assertEqual(json.loads(LLMGateway(bounded).generate(MESSAGES, SCHEMA).content), {'amount': 3})
        with self.assertRaisesRegex(InferenceError, 'LLM_REQUEST_TOO_LARGE'):
            LLMGateway(replace(bounded, provider=replace(bounded.provider, max_input_chars=exact_limit - 1))).generate(MESSAGES, SCHEMA)
        config = self.config('openai', structured_outputs=True)
        LLMGateway(config).generate(MESSAGES, SCHEMA)
        body = self.server.calls[-1][1]
        self.assertEqual(body['messages'], list(MESSAGES))
        self.assertEqual(body['response_format']['json_schema']['schema'], SCHEMA)
        self.assertEqual(body['max_completion_tokens'], 100)
        self.assertNotIn('max_tokens', body)
        self.assertNotIn('tools', body)
        LLMGateway(self.config(structured_outputs=True)).generate(MESSAGES, SCHEMA)
        self.assertEqual(self.server.calls[-1][1]['max_tokens'], 100)

    def test_schema_invalid_output_never_falls_back(self):
        gateway = LLMGateway(self.config(path='/invalid'), [self.config(name='fallback')])
        with self.assertRaisesRegex(InferenceError, 'LLM_OUTPUT_SCHEMA'):
            gateway.generate(MESSAGES, SCHEMA)
        self.assertEqual(len(self.server.calls), 1)

    def test_local_reference_and_nullable_schema(self):
        schema = {'$defs': {'amount': {'type': ['integer', 'null']}}, 'type': 'object',
                  'required': ['amount'], 'properties': {'amount': {'$ref': '#/$defs/amount'}}}
        self.assertEqual(json.loads(LLMGateway(self.config()).generate(MESSAGES, schema).content)['amount'], 3)

    def test_remote_reference_rejected_without_network(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_REMOTE_SCHEMA_FORBIDDEN'):
            LLMGateway(self.config()).generate(MESSAGES, {'$ref': 'https://example.invalid/secret'})
        self.assertEqual(self.server.calls, [])
        with self.assertRaisesRegex(InferenceError, 'LLM_INVALID_SCHEMA'):
            LLMGateway(self.config()).generate(MESSAGES, {'$ref': '#/$defs/missing'})
        self.assertEqual(self.server.calls, [])

    def test_invalid_schema_and_duplicate_output_fail_closed(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_INVALID_SCHEMA'):
            LLMGateway(self.config()).generate(MESSAGES, {'type': 'wrong'})
        self.assertEqual(self.server.calls, [])
        with self.assertRaises(InferenceError):
            LLMGateway(self.config(path='/duplicate')).generate(MESSAGES, SCHEMA)

    def test_transport_timeout(self):
        started = time.monotonic()
        with self.assertRaisesRegex(InferenceError, 'LLM_TIMEOUT'):
            LLMGateway(self.config(path='/slow', timeout=.08)).generate(MESSAGES, SCHEMA)
        self.assertLess(time.monotonic() - started, .22)

    def test_fallback_uses_shared_deadline(self):
        gateway = LLMGateway(self.config(path='/slow', timeout=.08), [self.config(name='fallback')])
        with self.assertRaisesRegex(InferenceError, 'LLM_TIMEOUT'):
            gateway.generate(MESSAGES, SCHEMA)
        self.assertEqual([p for p, _, _ in self.server.calls], ['/slow'])

    def test_retryable_transport_failure_uses_explicit_fallback(self):
        gateway = LLMGateway(self.config(path='/unavailable'), [self.config(name='fallback')])
        self.assertEqual(json.loads(gateway.generate(MESSAGES, SCHEMA).content), {'amount': 3})
        self.assertEqual([p for p, _, _ in self.server.calls], ['/unavailable', '/ok'])

    def test_refusal_tools_and_incomplete_are_terminal(self):
        for path, code in [('/refusal', 'LLM_REFUSED'), ('/tool', 'LLM_TOOL_CALL_FORBIDDEN'),
                           ('/length', 'LLM_INCOMPLETE_OUTPUT')]:
            self.server.calls.clear()
            with self.assertRaisesRegex(InferenceError, code):
                LLMGateway(self.config(path=path), [self.config(name='fallback')]).generate(MESSAGES, SCHEMA)
            self.assertEqual(len(self.server.calls), 1)

    def test_fallback_cannot_weaken_data_policy(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_FALLBACK_POLICY_MISMATCH'):
            LLMGateway(self.config(max_sensitivity='restricted', data_policy_reviewed=True),
                       [self.config(name='fallback', max_sensitivity='public')])
        self.assertEqual(self.server.calls, [])

    def test_redirect_not_followed(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_PROVIDER_HTTP_ERROR'):
            LLMGateway(self.config(path='/redirect')).generate(MESSAGES, SCHEMA)
        self.assertEqual([p for p, _, _ in self.server.calls], ['/redirect'])

    def test_tools_and_unknown_protocol_rejected_before_dispatch(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_TOOL_CALL_FORBIDDEN'):
            LLMGateway(self.config()).generate(MESSAGES, SCHEMA, [{'name': 'pay'}])
        with self.assertRaisesRegex(InferenceError, 'LLM_INVALID_CONFIG'):
            create_provider('unregistered')
        self.assertEqual(self.server.calls, [])

    def test_credentials_do_not_fall_back_or_appear_in_config(self):
        config = self.config(api_key_env='TEST_LLM_KEY')
        with patch.dict(os.environ, {'TEST_LLM_KEY': ''}):
            with self.assertRaisesRegex(InferenceError, 'LLM_PROVIDER_CREDENTIALS'):
                LLMGateway(config, [self.config(name='fallback')]).generate(MESSAGES, SCHEMA)
        with patch.dict(os.environ, {'TEST_LLM_KEY': 'do-not-log-this-secret'}):
            self.assertNotIn('do-not-log-this-secret', repr(config))
        self.assertEqual(self.server.calls, [])

    def test_schema_less_native_request_does_not_force_json(self):
        with patch.dict(os.environ, {'TEST_LLM_KEY': 'fixture'}):
            gateway = LLMGateway(self.config('gemini', '/gemini', structured_outputs=True, api_key_env='TEST_LLM_KEY'))
            gateway.generate(MESSAGES)
        generation = self.server.calls[-1][1]['generationConfig']
        self.assertNotIn('responseMimeType', generation)
        self.assertNotIn('responseJsonSchema', generation)

    def test_concurrent_calls_do_not_share_response_state(self):
        gateway = LLMGateway(self.config(path='/echo'))
        def call(i):
            return gateway.generate([{'role': 'user', 'content': str(i)}]).content
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(call, range(12)))
        self.assertEqual([json.loads(r)['answer'] for r in replies], [str(i) for i in range(12)])

    def write_config(self, path, provider='deepseek', **extra):
        value = dict(provider=provider, model='explicit-model',
                     endpoint=f'http://127.0.0.1:{self.server.server_port}/gemini' if provider == 'gemini'
                              else f'http://127.0.0.1:{self.server.server_port}/ok',
                     api_key='${TEST_LLM_KEY}', timeout=1, locality='local',
                     gateway={'mode': 'shadow', 'max_fallbacks': 0})
        value.update(extra)
        path.write_text(yaml.safe_dump(value), encoding='utf-8')

    def test_configuration_only_switches_all_providers(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TEST_LLM_KEY': 'fixture'}):
            path = Path(directory) / 'llm.yaml'
            for alias, kind in [('deepseek', 'openai_compatible'), ('glm', 'openai_compatible'),
                                ('gemini', 'gemini'), ('openai', 'openai')]:
                self.write_config(path, alias)
                settings = LLMSettings.load(path)
                self.assertEqual(settings.providers[0].kind, kind)
                self.assertEqual(settings.providers[0].api_key_env, 'TEST_LLM_KEY')
                result = LLMGateway.from_config(path).infer(LLMRequest(MESSAGES, SCHEMA))
                self.assertEqual(json.loads(result.content), {'amount': 3})

    def test_yaml_rejects_raw_secret_unknown_keys_duplicates_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'llm.yaml'
            self.write_config(path, api_key='secret-that-must-not-leak')
            with self.assertRaisesRegex(InferenceError, 'LLM_SECRET_REFERENCE_REQUIRED') as error:
                LLMSettings.load(path)
            self.assertNotIn('secret-that-must-not-leak', str(error.exception))
            for data in ['provider: glm\nprovider: deepseek\n', 'x: &x [a]\ny: *x\n',
                         '!!python/object:evil {}', 'x: [' + '[' * 40 + ']' * 40 + ']']:
                path.write_text(data, encoding='utf-8')
                with self.assertRaises(InferenceError):
                    LLMSettings.load(path)
            self.write_config(path, unexpected=True)
            with self.assertRaises(InferenceError):
                LLMSettings.load(path)

    def test_yaml_requires_existing_data_policy_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'llm.yaml'
            self.write_config(path, max_sensitivity='restricted')
            with self.assertRaisesRegex(InferenceError, 'LLM_DATA_POLICY_NOT_REVIEWED'):
                LLMSettings.load(path)

    def test_disabled_example_sends_nothing(self):
        gateway = LLMGateway.from_config('config/llm.yaml')
        with self.assertRaisesRegex(InferenceError, 'LLM_DISABLED'):
            gateway.generate(MESSAGES, SCHEMA)
        self.assertEqual(self.server.calls, [])

    def test_existing_business_gateway_uses_new_bridge_without_bank_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'llm.yaml'
            self.write_config(path, max_sensitivity='restricted', data_policy_reviewed=True,
                              endpoint=f'http://127.0.0.1:{self.server.server_port}/intent')
            with patch.dict(os.environ, {'TEST_LLM_KEY': 'fixture'}):
                service = TrustaService(Config(db_path=str(Path(directory) / 'bank.db'),
                                              signing_key=b'llm-test-key', llm_config_path=str(path)))
                self.assertIsInstance(service.llm.providers['deepseek'], BackendProviderBridge)
                task = service.create_task('给张三转 2000 元')
                body = service.blackboard.get(task['task_id'])
                result = service.llm.infer(InferenceRequest(uid(), InferenceContext(body['task_id'],
                    'demo', 'supervisor', 'TRANSFER', body['revision']), 'intent_normalization', {'text': body['user_goal']}))
                self.assertEqual(result.status, 'SUCCEEDED')
                self.assertEqual(service.db.rows('SELECT * FROM transfers'), [])
                self.assertTrue(service.audit.verify_chain())

    def test_replay_rejects_request_or_wire_drift(self):
        from benchmarks.llm_provider_eval import stored_provider
        from benchmarks.glm_evaluation import request_document
        from backend.llm.contracts import ProviderRequest
        config = self.config().provider
        request = ProviderRequest(MESSAGES, SCHEMA, 100, 60)
        frozen = request_document(replace(request, timeout_seconds=55))
        stored = {'raw_response': None, 'error': 'LLM_PROVIDER_UNAVAILABLE', 'transport_body': {}}
        captures = []
        adapter = stored_provider(config, frozen, stored, captures)
        with self.assertRaisesRegex(InferenceError, 'EVALUATION_WIRE_DRIFT'):
            adapter.infer(request)
        with self.assertRaisesRegex(InferenceError, 'EVALUATION_INPUT_DRIFT'):
            adapter.infer(replace(request, max_output_tokens=99))
        self.assertEqual(captures, [])

    def test_replay_preserves_real_transport_failure_without_network(self):
        from benchmarks.llm_provider_eval import stored_provider
        from benchmarks.glm_evaluation import request_document
        from backend.llm.contracts import ProviderRequest
        config = self.config().provider
        request = ProviderRequest(MESSAGES, SCHEMA, 100, 60)
        frozen = request_document(replace(request, timeout_seconds=55))
        wire = create_provider(config.kind).transport_class(config).body(request)
        stored = {'raw_response': None, 'error': 'LLM_PROVIDER_UNAVAILABLE', 'transport_body': wire}
        captures = []
        adapter = stored_provider(config, frozen, stored, captures)
        with self.assertRaisesRegex(InferenceError, 'LLM_PROVIDER_UNAVAILABLE'):
            adapter.infer(request)
        self.assertEqual(captures, [stored])
        self.assertEqual(self.server.calls, [])

    def test_live_evaluation_cannot_create_or_enlarge_budget(self):
        from benchmarks.llm_provider_eval import live_preflight
        documents = [{'dataset': [{'id': 'sample'}], 'requests': {'sample': {'max_output_tokens': 100}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'budget.json'
            for ledger in [dict(requests=100, max_requests=100, reserved_usd=0, max_usd=1, attempts=[]),
                           dict(requests=0, max_requests=100, reserved_usd=1, max_usd=1, attempts=[]),
                           dict(requests=0, max_requests=100, reserved_usd=0, max_usd=1,
                                attempts=[{'case': 'sample', 'provider': 'deepseek'}])]:
                path.write_text(json.dumps(ledger), encoding='utf-8')
                original = path.read_bytes()
                with self.assertRaises(InferenceError):
                    live_preflight('deepseek', documents, path)
                self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.server.calls, [])


if __name__ == '__main__':
    unittest.main()
