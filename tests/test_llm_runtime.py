"""Real loopback HTTP integration and persistent SQLite fault/security checks.

No external model APIs, banking execution, chain writes or fabricated API success.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import yaml

from backend.llm.configuration import ProviderConfig
from backend.llm.contracts import InferenceError
from llm import LLMModelConfig, LLMResponse, LLMStructuredResponse, ToolCall, ToolDefinition
from llm.gateway import LLMGateway
from llm.hashing import digest, text_hash
from llm.prompts import PromptRegistry, PromptTemplate
from llm.router import ModelRoute, ModelRouter, RoutingContext
from llm.runtime import LLMRuntime
from llm.tools import tool_envelope
from llm.trace import LLMTraceStore, ZERO_HASH

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['amount'],
          'properties': {'amount': {'type': 'integer', 'minimum': 1, 'maximum': 9}}}
MESSAGES = ({'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'synthetic'})
ARGUMENTS = {'type': 'object', 'additionalProperties': False, 'required': ['account'],
             'properties': {'account': {'type': 'string', 'minLength': 1}}}
TOOL = ToolDefinition('read_account', ARGUMENTS, 'account:read')


class PromptTests(unittest.TestCase):
    def test_version_file_loading_and_duplicate_yaml_rejection(self):
        registry = PromptRegistry()
        template = registry.load(Path(__file__).resolve().parents[1] /
                                 'llm/prompts/templates/structured_candidate.v1.yaml')
        self.assertEqual(template.version, '1.0.0')
        rendered = template.render({'output_contract': json.dumps(SCHEMA), 'request': 'synthetic'})
        self.assertIn('synthetic', rendered.messages[1]['content'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.yaml'
            path.write_text('name: task\nname: changed\nversion: 1\nmessages: []', encoding='utf-8')
            with self.assertRaisesRegex(InferenceError, 'LLM_INVALID_PROMPT'):
                registry.load(path)

    def test_explicit_versions_and_hashes(self):
        registry = PromptRegistry()
        a = PromptTemplate('task', '1', [{'role': 'user', 'content': '{request}'}])
        b = PromptTemplate('task', '2', [{'role': 'user', 'content': 'Task: {request}'}])
        registry.register(a)
        registry.register(b)
        self.assertNotEqual(a.template_hash, b.template_hash)
        self.assertEqual(a.render({'request': 'hello'}).prompt_hash,
                         registry.get('task', '1').render({'request': 'hello'}).prompt_hash)
        self.assertNotEqual(a.render({'request': 'hello'}).prompt_hash, a.render({'request': 'other'}).prompt_hash)

    def test_version_cannot_be_silently_overwritten(self):
        registry = PromptRegistry()
        registry.register(PromptTemplate('task', '1', MESSAGES))
        with self.assertRaisesRegex(InferenceError, 'LLM_PROMPT_VERSION_CONFLICT'):
            registry.register(PromptTemplate('task', '1', [{'role': 'user', 'content': 'changed'}]))
        with self.assertRaisesRegex(InferenceError, 'LLM_PROMPT_NOT_FOUND'):
            registry.get('task', 'latest')

    def test_template_and_render_snapshots_are_immutable(self):
        messages = [{'role': 'user', 'content': '{value}'}]
        template = PromptTemplate('task', '1', messages)
        hash_before = template.template_hash
        messages[0]['content'] = 'modified'
        template.messages[0]['content'] = 'modified'
        rendered = template.render({'value': '{evil.__class__}'})
        rendered.messages[0]['content'] = 'modified'
        self.assertEqual(rendered.messages[0]['content'], '{evil.__class__}')
        self.assertEqual(template.template_hash, hash_before)

    def test_template_rejects_attribute_index_format_and_missing_variables(self):
        for field in ['{a.__class__}', '{a[0]}', '{}', '{a!r}', '{a:1000000000}']:
            with self.assertRaises(InferenceError):
                PromptTemplate('task', '1', [{'role': 'user', 'content': field}])
        template = PromptTemplate('task', '1', [{'role': 'user', 'content': '{a}'}])
        for variables in [{}, {'a': 'ok', 'extra': 'x'}, {'a': 1}, {'a': 'x' * 100001}]:
            with self.assertRaises(InferenceError):
                template.render(variables)


class StructuredToolTests(unittest.TestCase):
    def test_structured_response_and_snapshot(self):
        response = LLMStructuredResponse.parse(LLMResponse('{"amount":3}'), SCHEMA)
        response.data['amount'] = 9
        self.assertEqual(response.data, {'amount': 3})
        self.assertEqual(response.response_hash, text_hash('{"amount":3}'))
        self.assertEqual(response.schema_hash, digest(SCHEMA))

    def test_invalid_json_schema_duplicate_keys_and_extra_fields_rejected(self):
        for content in ['{"amount":"3"}', '{"amount":3,"execute":true}',
                        '{"amount":3,"amount":4}', '{"amount":NaN}', 'not json', '{"amount":0}']:
            with self.assertRaises(InferenceError):
                LLMStructuredResponse.parse(LLMResponse(content), SCHEMA)

    def test_format_validation_and_remote_refs_fail_closed(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_OUTPUT_SCHEMA'):
            LLMStructuredResponse.parse(LLMResponse('"invalid"'), {'type': 'string', 'format': 'ipv4'})
        with self.assertRaisesRegex(InferenceError, 'LLM_REMOTE_SCHEMA_FORBIDDEN'):
            LLMStructuredResponse.parse(LLMResponse('{}'), {'$ref': 'http://127.0.0.1/schema'})

    def test_tool_request_is_data_and_has_no_execution_hook(self):
        content = json.dumps({'data': {'amount': 3}, 'tool_requests': [
            {'name': 'read_account', 'arguments': {'account': 'synthetic'}, 'permission_scope': 'account:read'}]})
        result = LLMStructuredResponse.parse(LLMResponse(content), SCHEMA, [TOOL])
        call = result.tool_calls[0]
        self.assertIsInstance(call, ToolCall)
        self.assertEqual(call.arguments, {'account': 'synthetic'})
        self.assertFalse(hasattr(call, 'execute'))
        call.arguments['account'] = 'changed'
        self.assertEqual(call.as_dict()['arguments'], {'account': 'synthetic'})

    def test_tool_name_scope_arguments_and_extra_fields_are_bound(self):
        base = {'name': TOOL.name, 'arguments': {'account': 'x'}, 'permission_scope': TOOL.permission_scope}
        for mutation in [{'name': 'pay'}, {'permission_scope': 'account:write'},
                         {'arguments': {'account': 3}}, {'arguments': {'account': 'x', 'amount': 100}},
                         {'execute': True}]:
            item = dict(base, **mutation)
            with self.assertRaisesRegex(InferenceError, 'LLM_OUTPUT_SCHEMA'):
                LLMStructuredResponse.parse(LLMResponse(json.dumps(
                    {'data': {'amount': 3}, 'tool_requests': [item]})), SCHEMA, [TOOL])

    def test_nested_local_refs_survive_tool_envelope(self):
        local = {'type': 'object', 'required': ['account'], 'additionalProperties': False,
                 'properties': {'account': {'$ref': '#/$defs/identifier'}},
                 '$defs': {'identifier': {'type': 'string', 'pattern': '^id-'}}}
        tool = ToolDefinition('read', local, 'account:read')
        body = {'data': {'account': 'id-1'}, 'tool_requests': [
            {'name': 'read', 'permission_scope': 'account:read', 'arguments': {'account': 'id-2'}}]}
        result = LLMStructuredResponse.parse(LLMResponse(json.dumps(body)), local, [tool])
        self.assertEqual(result.data['account'], 'id-1')
        body['tool_requests'][0]['arguments']['account'] = 'invalid'
        with self.assertRaises(InferenceError):
            LLMStructuredResponse.parse(LLMResponse(json.dumps(body)), local, [tool])

    def test_tool_bounds_and_duplicate_definitions(self):
        with self.assertRaises(InferenceError):
            tool_envelope(SCHEMA, [TOOL, TOOL])
        with self.assertRaises(InferenceError):
            ToolDefinition('read', {'type': 'string'}, 'account:read')


def route(name, **kwargs):
    return ModelRoute(LLMModelConfig(ProviderConfig(name, kind='openai_compatible', model='explicit-model',
        endpoint='http://127.0.0.1:1/', **kwargs), 1, 100))


class RouterTests(unittest.TestCase):
    def test_task_complexity_cost_latency_selection(self):
        routes = [route('cheap', max_complexity='simple', cost_per_1k_tokens_usd=.001, expected_latency_ms=10),
                  route('suitable', cost_per_1k_tokens_usd=.01, expected_latency_ms=20),
                  route('expensive', cost_per_1k_tokens_usd=.1, expected_latency_ms=30)]
        router = ModelRouter(routes, max_fallbacks=0)
        self.assertEqual(router.select(RoutingContext('TRANSFER', complexity='simple'), MESSAGES, SCHEMA)
                         .configs[0].provider.name, 'cheap')
        self.assertEqual(router.select(RoutingContext('TRANSFER', complexity='complex'), MESSAGES, SCHEMA)
                         .configs[0].provider.name, 'suitable')
        restricted = ModelRouter([replace(routes[1], task_types=('BILL_ANALYSIS',))])
        with self.assertRaisesRegex(InferenceError, 'LLM_NO_ELIGIBLE_PROVIDER'):
            restricted.select(RoutingContext('TRANSFER'), MESSAGES, SCHEMA)

    def test_latency_cost_unknown_price_and_sensitivity_rejection(self):
        for model, context in [
            (route('slow', expected_latency_ms=100), RoutingContext('TRANSFER', max_latency_ms=50)),
            (route('unknown'), RoutingContext('TRANSFER', max_cost_usd=.1)),
            (route('public'), RoutingContext('TRANSFER', sensitivity='restricted')),
            (route('standard'), RoutingContext('TRANSFER', security_level='enhanced')),
            (route('price', cost_per_1k_tokens_usd=1), RoutingContext('TRANSFER', max_cost_usd=.001))]:
            with self.assertRaisesRegex(InferenceError, 'LLM_NO_ELIGIBLE_PROVIDER'):
                ModelRouter([model]).select(context, MESSAGES, SCHEMA)

    def test_total_fallback_budget_and_domain_are_bound(self):
        models = [route('a', cost_per_1k_tokens_usd=.01), route('b', cost_per_1k_tokens_usd=.01),
                  route('c', locality='external', cost_per_1k_tokens_usd=.01)]
        router = ModelRouter(models, max_fallbacks=2)
        initial = router.select(RoutingContext('TRANSFER'), MESSAGES, SCHEMA)
        self.assertEqual([c.provider.name for c in initial.configs], ['a', 'b'])
        self.assertIn(('c', 'FALLBACK_TRUST_DOMAIN'), initial.rejected)
        capped = router.select(RoutingContext('TRANSFER', max_cost_usd=initial.estimated_total_cost_usd * .75),
                               MESSAGES, SCHEMA)
        self.assertEqual(len(capped.configs), 1)

    def test_bad_route_input_and_disabled_are_rejected(self):
        for context in [RoutingContext('TRANSFER', max_cost_usd=float('nan')),
                        RoutingContext('TRANSFER', max_latency_ms=True), RoutingContext('TRANSFER', complexity='strongest')]:
            with self.assertRaises(InferenceError):
                ModelRouter([route('a')]).select(context, MESSAGES, SCHEMA)
        with self.assertRaisesRegex(InferenceError, 'LLM_DISABLED'):
            ModelRouter([route('a')], enabled=False).select(RoutingContext('TRANSFER'), MESSAGES, SCHEMA)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trace.sqlite3'
        self.store = LLMTraceStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def finish(self, store, rid):
        return store.finish(rid, status='SUCCEEDED', provider='test', model='explicit-model', latency=.1,
            prompt_hash=ZERO_HASH, response_hash=ZERO_HASH, attempts=[{'provider': 'test',
                'model': 'explicit-model', 'response_hash': ZERO_HASH, 'error_code': None}])

    def test_concurrent_claim_only_one_winner(self):
        def claim(_):
            try:
                LLMTraceStore(self.path).claim('shared', ZERO_HASH, ZERO_HASH, ZERO_HASH)
                return 'winner'
            except InferenceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(16)))
        self.assertEqual(results.count('winner'), 1)
        self.assertEqual(results.count('LLM_DUPLICATE_REQUEST'), 15)
        self.assertTrue(self.store.verify())

    def test_parallel_append_and_restart_recovery_without_resending(self):
        def run(i):
            store = LLMTraceStore(self.path)
            store.claim(str(i), ZERO_HASH, ZERO_HASH, ZERO_HASH)
            self.finish(store, str(i))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(run, range(20)))
        self.store.claim('interrupted', ZERO_HASH, ZERO_HASH, ZERO_HASH)
        restarted = LLMTraceStore(self.path)
        self.assertEqual(restarted.unfinished(), ('interrupted',))
        self.assertTrue(restarted.verify())
        with self.assertRaisesRegex(InferenceError, 'LLM_DUPLICATE_REQUEST'):
            restarted.claim('interrupted', ZERO_HASH, ZERO_HASH, ZERO_HASH)

    def test_trace_tampering_and_truncation_are_detected(self):
        self.store.claim('one', ZERO_HASH, ZERO_HASH, ZERO_HASH)
        tail = self.finish(self.store, 'one')
        self.assertTrue(self.store.verify(tail))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE llm_trace_events SET payload=replace(payload,'explicit-model','tampered-model') WHERE seq=2")
            db.commit()
        self.assertFalse(self.store.verify(tail))

    def test_retained_tail_detects_full_journal_rewrite(self):
        self.store.claim('one', ZERO_HASH, ZERO_HASH, ZERO_HASH)
        tail = self.finish(self.store, 'one')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DELETE FROM llm_trace_events')
            db.execute('DELETE FROM llm_requests')
            db.commit()
        self.assertTrue(self.store.verify())  # unpinned local hash chain has this explicit limitation
        self.assertFalse(self.store.verify(tail))

    def test_prompt_binding_terminal_transition_and_metadata_bounds(self):
        self.store.claim('one', ZERO_HASH, ZERO_HASH, ZERO_HASH)
        with self.assertRaisesRegex(InferenceError, 'LLM_TRACE_STATE_CONFLICT'):
            self.store.finish('one', status='SUCCEEDED', provider='test', model='m', latency=0,
                prompt_hash='1' * 64, response_hash=None, attempts=[{'provider': 'test', 'model': 'm',
                'response_hash': None, 'error_code': None}])
        self.finish(self.store, 'one')
        with self.assertRaisesRegex(InferenceError, 'LLM_TRACE_STATE_CONFLICT'):
            self.finish(self.store, 'one')
        with self.assertRaisesRegex(InferenceError, 'LLM_INVALID_TRACE'):
            self.store.claim('secret\nvalue', ZERO_HASH, ZERO_HASH, ZERO_HASH)
        self.assertTrue(self.store.verify())


class RuntimeHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.calls.append((self.path, body))
                content = '{"amount":3}'
                if self.path == '/slow':
                    time.sleep(.15)
                if self.path == '/invalid':
                    content = '{"amount":"3"}'
                if self.path == '/format':
                    content = '"invalid-ip"'
                if self.path in {'/propose', '/escalate'}:
                    content = json.dumps({'data': {'amount': 3}, 'tool_requests': [{'name': 'read_account',
                        'arguments': {'account': 'synthetic'},
                        'permission_scope': 'account:write' if self.path == '/escalate' else 'account:read'}]})
                payload = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                           'model': 'fixture-model', 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
                if self.path == '/native-tool':
                    payload['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'pay'}}]
                if self.path == '/refusal':
                    payload['choices'][0]['message']['refusal'] = 'No'
                data = json.dumps(payload).encode()
                self.send_response(503 if self.path == '/unavailable' else 200)
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
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trace.sqlite3'
        self.trace = LLMTraceStore(self.path)
        self.prompts = PromptRegistry()
        self.prompts.register(PromptTemplate('task', '1', [{'role': 'system', 'content': 'Return JSON.'},
                                                    {'role': 'user', 'content': '{request}'}]))
        self.server.calls.clear()

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, path='/ok', fallback=None, timeout=1):
        configs = []
        for index, suffix in enumerate([path] + ([fallback] if fallback else [])):
            provider = ProviderConfig(f'p{index}', kind='openai_compatible', model='explicit-model',
                endpoint=f'http://127.0.0.1:{self.server.server_port}{suffix}',
                cost_per_1k_tokens_usd=.001 + index * .001, expected_latency_ms=10)
            configs.append(ModelRoute(LLMModelConfig(provider, timeout, 100)))
        return LLMRuntime(self.prompts, ModelRouter(configs), self.trace)

    def generate(self, runtime, rid='request-1', schema=SCHEMA, tools=()):
        return runtime.generate(request_id=rid, prompt_name='task', prompt_version='1',
            variables={'request': 'synthetic'}, response_schema=schema,
            routing=RoutingContext('TRANSFER'), tool_definitions=tools)

    def test_end_to_end_prompt_schema_trace_provider_binding(self):
        result = self.generate(self.runtime())
        self.assertEqual(result.data, {'amount': 3})
        self.assertEqual(result.response.provider, 'p0')
        tail = self.trace.events()[-1]
        self.assertEqual(tail['response_hash'], result.response_hash)
        self.assertEqual(tail['provider'], 'p0')
        self.assertEqual(tail['model'], 'explicit-model')
        self.assertTrue(self.trace.verify(tail['record_hash']))
        body = self.server.calls[0][1]
        self.assertEqual(body['messages'], list(MESSAGES))
        self.assertNotIn('tools', body)
        self.assertNotIn('synthetic', json.dumps(self.trace.events()))

    def test_yaml_switch_without_runtime_or_prompt_changes(self):
        path = Path(self.temp.name) / 'llm.yaml'
        for name, suffix in [('deepseek', '/ok'), ('glm', '/ok')]:
            path.write_text(yaml.safe_dump({'provider': name, 'model': name + '-configured',
                'endpoint': f'http://127.0.0.1:{self.server.server_port}{suffix}', 'timeout': 1,
                'gateway': {'mode': 'shadow', 'max_fallbacks': 0}, 'locality': 'local'}), encoding='utf-8')
            router = ModelRouter.from_config(path, task_routes={name: ['TRANSFER']})
            result = self.generate(LLMRuntime(self.prompts, router, self.trace), rid=name)
            self.assertEqual(result.response.provider, name)
            self.assertEqual(self.trace.events()[-1]['model'], name + '-configured')
        first, second = [body for _, body in self.server.calls]
        self.assertEqual(first['messages'], second['messages'])
        self.assertEqual(self.trace.events()[1]['prompt_hash'], self.trace.events()[3]['prompt_hash'])

    def test_disabled_yaml_cannot_make_provider_calls(self):
        path = Path(self.temp.name) / 'disabled.yaml'
        path.write_text(yaml.safe_dump({'provider': 'glm', 'model': 'configured',
            'endpoint': f'http://127.0.0.1:{self.server.server_port}/ok',
            'gateway': {'mode': 'disabled'}}), encoding='utf-8')
        with self.assertRaisesRegex(InferenceError, 'LLM_DISABLED'):
            self.generate(LLMRuntime(self.prompts, ModelRouter.from_config(path), self.trace))
        self.assertEqual(self.server.calls, [])

    def test_rejected_output_hash_retained_without_content(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_OUTPUT_SCHEMA'):
            self.generate(self.runtime('/invalid', '/ok'))
        event = self.trace.events()[-1]
        self.assertEqual(event['response_hash'], text_hash('{"amount":"3"}'))
        self.assertNotIn('"amount"', json.dumps(self.trace.events()))
        self.assertTrue(self.trace.verify())

    def test_prompt_and_route_failures_do_not_send_requests(self):
        runtime = self.runtime()
        with self.assertRaisesRegex(InferenceError, 'LLM_PROMPT_NOT_FOUND'):
            runtime.generate(request_id='x', prompt_name='task', prompt_version='absent', variables={},
                response_schema=SCHEMA, routing=RoutingContext('TRANSFER'))
        with self.assertRaisesRegex(InferenceError, 'LLM_NO_ELIGIBLE_PROVIDER'):
            runtime.generate(request_id='x', prompt_name='task', prompt_version='1', variables={'request': 'x'},
                response_schema=SCHEMA, routing=RoutingContext('TRANSFER', sensitivity='restricted'))
        self.assertEqual(self.server.calls, [])

    def test_actual_timeout_fallback_has_shared_deadline(self):
        started = time.monotonic()
        result = self.generate(self.runtime('/slow', '/ok', timeout=.2))
        self.assertEqual(result.response.provider, 'p1')
        self.assertLess(time.monotonic() - started, .3)
        self.assertEqual([p for p, _ in self.server.calls], ['/slow', '/ok'])
        self.assertIn('LLM_TIMEOUT', [a['error_code'] for a in self.trace.events()[-1]['attempts']])

    def test_unavailable_fallback_and_provider_trace(self):
        result = self.generate(self.runtime('/unavailable', '/ok'))
        self.assertEqual(result.response.provider, 'p1')
        self.assertEqual([p for p, _ in self.server.calls], ['/unavailable', '/ok'])
        self.assertTrue(self.trace.verify())

    def test_schema_format_tool_scope_and_refusal_never_fallback(self):
        for path, schema, tools in [('/invalid', SCHEMA, ()),
                ('/format', {'type': 'string', 'format': 'ipv4'}, ()),
                ('/escalate', SCHEMA, (TOOL,)), ('/refusal', SCHEMA, ()), ('/native-tool', SCHEMA, ())]:
            self.server.calls.clear()
            with self.assertRaises(InferenceError):
                self.generate(self.runtime(path, '/ok'), rid=path[1:], schema=schema, tools=tools)
            self.assertEqual([p for p, _ in self.server.calls], [path])
            self.assertEqual(self.trace.events()[-1]['status'], 'REJECTED')
        self.assertTrue(self.trace.verify())

    def test_invalid_schema_is_rejected_before_http(self):
        with self.assertRaises(InferenceError):
            self.generate(self.runtime(), schema={'$ref': 'http://127.0.0.1:1/secret'})
        self.assertEqual(self.server.calls, [])

    def test_tool_proposal_does_not_enable_native_tools(self):
        result = self.generate(self.runtime('/propose'), tools=[TOOL])
        self.assertEqual(result.tool_calls[0].name, 'read_account')
        self.assertNotIn('tools', self.server.calls[0][1])
        with self.assertRaisesRegex(InferenceError, 'LLM_TOOL_CALL_FORBIDDEN'):
            LLMGateway(self.runtime().router.routes[0].config).generate(MESSAGES, SCHEMA,
                [{'name': TOOL.name, 'permission_scope': TOOL.permission_scope}])

    def test_concurrent_duplicate_request_hits_provider_once(self):
        runtime = self.runtime('/slow', timeout=1)
        def run(_):
            try:
                return self.generate(runtime).data
            except InferenceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(results.count('LLM_DUPLICATE_REQUEST'), 7)
        self.assertEqual(len(self.server.calls), 1)
        self.assertTrue(self.trace.verify())

    def test_restart_prevents_reexecuting_a_completed_request(self):
        self.generate(self.runtime())
        self.trace = LLMTraceStore(self.path)
        with self.assertRaisesRegex(InferenceError, 'LLM_DUPLICATE_REQUEST'):
            self.generate(self.runtime())
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.trace.unfinished(), ())

    def test_all_unavailable_retains_failure_trace(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_PROVIDER_UNAVAILABLE'):
            self.generate(self.runtime('/unavailable', '/unavailable'))
        self.assertEqual(self.trace.events()[-1]['status'], 'FAILED')
        self.assertEqual(self.trace.unfinished(), ())
        self.assertTrue(self.trace.verify())

    def test_trace_persistence_failure_returns_no_candidate(self):
        runtime = self.runtime()
        original = self.trace.finish
        def fail(*args, **kwargs):
            raise InferenceError('LLM_TRACE_UNAVAILABLE')
        self.trace.finish = fail
        with self.assertRaisesRegex(InferenceError, 'LLM_TRACE_UNAVAILABLE'):
            self.generate(runtime)
        self.trace.finish = original
        self.assertEqual(self.trace.unfinished(), ('request-1',))
        with self.assertRaisesRegex(InferenceError, 'LLM_DUPLICATE_REQUEST'):
            self.generate(runtime)
        self.assertEqual(len(self.server.calls), 1)
