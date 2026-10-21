"""Actual loopback provider requests, frozen graph compilation and negative cases.

No live LLM costs, bank execution or chain transactions.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from backend.llm.configuration import ProviderConfig
from backend.llm.contracts import InferenceError
from backend.runtime.taskgraph import SAFETY
from evaluation.llm.metrics import EvaluationCase, ProviderStabilityEvaluator, execution_alignment, summarize
from llm.contracts import LLMModelConfig
from llm.hashing import snapshot
from llm.planner import Planner, UserRequest
from llm.prompts import PromptRegistry
from llm.router import ModelRoute, ModelRouter, RoutingContext
from llm.runtime import LLMRuntime
from llm.trace import LLMTraceStore
from memory import MemoryStore
from runtime.task_graph import TaskGraph, TaskNode, TaskPlan
from schemas import task_plan_schema
from validator import PlanValidator, PlanningContext


def row(node_id, agent='finance', action='analysis', parameters=None, dependency=()):
    return {'node_id': node_id, 'agent': agent, 'action': action,
            'parameters': parameters or {}, 'dependency': list(dependency)}


def analysis_plan():
    return {'task_id': 'task1', 'goal': 'Analyze the supplied month', 'steps': [
        row('Read', action='transactions.read', parameters={'account_id': 'input:account', 'month': 'input:month'}),
        row('Analyze', parameters={'records': 'Read:result'}, dependency=['Read'])]}


def context(kind='analysis'):
    if kind == 'analysis':
        return PlanningContext('task1', {'input:account': 'sensitive-account-value', 'input:month': '2026-10'},
                               [('finance', 'transactions.read'), ('finance', 'analysis')])
    if kind == 'transfer':
        return PlanningContext('task1', {'input:account': 'acct', 'input:recipient': 'recipient-sensitive',
            'input:amount': 1, 'input:currency': 'EUR'}, [('transaction', 'create_transfer_plan'),
                                                          ('supervisor', 'graph.report')])


def transfer_plan():
    return {'task_id': 'task1', 'goal': 'Propose transfer', 'steps': [
        row('Propose', agent='transaction', action='create_transfer_plan', parameters={
            'account_id': 'input:account', 'recipient_name': 'input:recipient',
            'amount': 'input:amount', 'currency': 'input:currency'}),
        row('Report', agent='supervisor', action='graph.report', dependency=['Propose'])]}


class GraphValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = PlanValidator()

    def validate(self, payload=None, scope=None):
        return self.validator.validate(TaskPlan.from_dict(payload or analysis_plan()), scope or context())

    def test_native_amount_units_and_parallel_writes_are_not_relaxed(self):
        original = context('transfer')
        values = original.inputs
        values['input:amount'] = '0.01'
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_GROUNDED_VALIDATION'):
            self.validate(transfer_plan(), PlanningContext('task1', values, original.allowed_actions))
        payload = transfer_plan()
        second = dict(payload['steps'][0], node_id='Second')
        payload['steps'].append(second)
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_GROUNDED_VALIDATION'):
            self.validate(payload, original)
        second['dependency'] = ['Propose']
        self.assertEqual(len(self.validate(payload, original).compiled['steps']), 19)

    def test_constructed_invalid_taskplan_is_rejected_without_leaking_values(self):
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_SCHEMA'):
            self.validator.validate(TaskPlan('task1', 'goal', ('sensitive-not-a-node',)), context())

    def test_plan_and_node_snapshot_and_unified_schema(self):
        payload = analysis_plan()
        plan = TaskPlan.from_dict(payload)
        original = plan.plan_hash
        payload['steps'][0]['parameters']['account_id'] = 'input:other'
        plan.steps[0].parameters['account_id'] = 'input:other'
        self.assertEqual(plan.plan_hash, original)
        self.assertEqual(task_plan_schema()['$defs']['TaskNode']['properties']['agent']['enum'],
                         ['supervisor', 'planner', 'finance', 'wealth', 'transaction', 'service'])

    def test_schema_rejects_unknown_agent_bad_json_fields_and_literal_money(self):
        for mutation in [{'agent': 'executor'}, {'agent': 'new_agent'}, {'execute': True},
                         {'parameters': {'amount': '0.01'}}, {'dependency': ['Read', 'Read']}]:
            payload = analysis_plan()
            payload['steps'][1].update(mutation)
            with self.assertRaises(InferenceError):
                TaskPlan.from_dict(payload)

    def test_topology_and_parallel_layers(self):
        nodes = [TaskNode(**row('Join', agent='supervisor', action='graph.join', dependency=['A', 'B'])),
                 TaskNode(**row('B', action='wealth.read')), TaskNode(**row('A', action='account.read'))]
        graph = TaskGraph(nodes)
        self.assertEqual([n.node_id for n in graph.topological_sort()], ['B', 'A', 'Join'])
        self.assertEqual([[n.node_id for n in group] for group in graph.parallel_groups()], [['B', 'A'], ['Join']])

    def test_self_cycle_and_multi_node_cycle_are_rejected(self):
        for rows in [[row('A', dependency=['A'])], [row('A', dependency=['B']), row('B', dependency=['A'])]]:
            with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_CYCLE'):
                TaskGraph([TaskNode(**r) for r in rows])

    def test_duplicate_unknown_dependency_and_depth_limit(self):
        for rows, code in [([row('A'), row('A')], 'LLM_PLAN_DUPLICATE_NODE'),
                           ([row('A', dependency=['Missing'])], 'LLM_PLAN_UNKNOWN_DEPENDENCY'),
                           ([row(f'N{i}', dependency=[f'N{i-1}'] if i else []) for i in range(21)], 'LLM_PLAN_GRAPH_DEPTH')]:
            with self.assertRaisesRegex(InferenceError, code):
                TaskGraph([TaskNode(**r) for r in rows])

    def test_real_native_validation_and_reference_provenance(self):
        valid = self.validate()
        self.assertEqual(valid.compiled['authority'], 'DETERMINISTIC_COMPILER')
        for ref in ['input:invented', 'Analyze:result']:
            payload = analysis_plan()
            payload['steps'][1]['parameters']['records'] = ref
            with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_GROUNDED_VALIDATION'):
                self.validate(payload)

    def test_dependency_required_for_ancestor_data(self):
        payload = analysis_plan()
        payload['steps'][1]['dependency'] = []
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_GROUNDED_VALIDATION'):
            self.validate(payload)

    def test_capability_unknown_action_and_scope_escalation(self):
        for action in ['forecast', 'transfer.create', 'payments.create']:
            payload = analysis_plan()
            payload['steps'][1]['action'] = action
            with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_UNKNOWN_ACTION'):
                self.validate(payload)
        payload = analysis_plan()
        payload['steps'][1]['action'] = 'finance.anomalies'
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_ACTION_PERMISSION'):
            self.validate(payload)

    def test_task_id_and_parameters_are_bound(self):
        payload = analysis_plan()
        payload['task_id'] = 'other'
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_TASK_BINDING'):
            self.validate(payload)
        payload = analysis_plan()
        payload['steps'][1]['parameters'] = {}
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_PARAMETERS'):
            self.validate(payload)

    def test_native_compiler_preserves_all_financial_safety_steps(self):
        valid = self.validate(transfer_plan(), context('transfer'))
        steps = valid.compiled['steps']
        self.assertEqual([s['name'] for s in steps[1:9]], list(SAFETY))
        self.assertEqual(steps[-1]['dependencies'], ['ProposeXRECEIPT'])
        self.assertFalse(hasattr(valid, 'execute'))

    def test_read_contracts_with_same_capability_do_not_expand_permission(self):
        scope = PlanningContext('task1', {'input:card': 'card-sensitive'}, [('service', 'card.list')])
        payload = {'task_id': 'task1', 'goal': 'Read cards', 'steps': [row('Read', agent='service', action='card.list')]}
        self.validate(payload, scope)
        payload['steps'][0]['action'] = 'card.get'
        payload['steps'][0]['parameters'] = {'resource_id': 'input:card'}
        with self.assertRaisesRegex(InferenceError, 'LLM_PLAN_ACTION_PERMISSION'):
            self.validate(payload, scope)

    def test_untrusted_context_cannot_register_permissions(self):
        for allowed in [[], [('executor', 'banking.transfer')], [('finance', 'forecast')]]:
            with self.assertRaises(InferenceError):
                candidate = PlanningContext('task1', {}, allowed)
                self.validator.catalog(candidate)

    def test_all_six_agents_retained_and_inactive_capabilities_not_invented(self):
        self.assertEqual(self.validator.registry.agents.active(),
                         ['finance', 'planner', 'service', 'supervisor', 'transaction', 'wealth'])
        self.assertNotIn(('planner', 'execute'), self.validator.specifications())


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.memory = MemoryStore(self.path, max_records_per_scope=2)
        self.scope = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def test_preferences_are_positive_allowlist_and_scopes_are_opaque(self):
        self.memory.save_preference(self.scope, {'language': 'zh-CN', 'verbosity': 'concise'})
        for prefs in [{'api_key': 'secret'}, {'language': 'private@example.com'}, {'risk_profile': 'high'}, {'account': '123'}]:
            with self.assertRaisesRegex(InferenceError, 'LLM_MEMORY_SENSITIVE_OR_UNSUPPORTED'):
                self.memory.save_preference(self.scope, prefs)
        with self.assertRaisesRegex(InferenceError, 'LLM_MEMORY_SCOPE'):
            self.memory.read('private@example.com')

    def test_validated_structure_drops_goal_ids_parameters_and_raw_failure(self):
        valid = PlanValidator().validate(TaskPlan.from_dict(analysis_plan()), context())
        self.memory.save_successful_plan(self.scope, valid)
        self.memory.save_failure(self.scope, 'LLM_PLAN_CYCLE')
        records = snapshot(self.memory.read(self.scope))
        for sensitive in ['sensitive-account-value', '2026-10', 'task1', 'Analyze the supplied month', 'input:account']:
            self.assertNotIn(sensitive, records)
        self.assertIn('NOT_RUN', records)
        with self.assertRaises(InferenceError):
            self.memory.save_failure(self.scope, 'LLM_KEY_SK_PRIVATE')
        with self.assertRaises(InferenceError):
            self.memory.save_successful_plan(self.scope, valid.plan)

    def test_retention_restart_isolation_forget_and_preferences_survive(self):
        self.memory.save_preference(self.scope, {'language': 'en'})
        for _ in range(4):
            self.memory.save_failure(self.scope, 'LLM_TIMEOUT')
        restarted = MemoryStore(self.path, max_records_per_scope=2)
        self.assertEqual(len(restarted.read(self.scope)), 3)
        self.assertEqual(restarted.read(str(uuid.uuid4())), [])
        restarted.forget(self.scope)
        self.assertEqual(restarted.read(self.scope), [])

    def test_parallel_memory_operations_preserve_bounded_records(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: self.memory.save_failure(self.scope, 'LLM_TIMEOUT'), range(15)))
        self.assertEqual(len(self.memory.read(self.scope)), 2)


class PlannerHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.calls.append((self.path, body))
                plan = transfer_plan() if self.path == '/transfer' else analysis_plan()
                if self.path == '/cycle':
                    plan['steps'][0]['dependency'] = ['Analyze']
                if self.path == '/illegal':
                    plan['steps'][1]['action'] = 'payments.create'
                if self.path == '/escalate':
                    plan['steps'][1]['action'] = 'finance.anomalies'
                if self.path == '/agent':
                    plan['steps'][1]['agent'] = 'executor'
                content = json.dumps(plan)
                if self.path == '/invalid-json':
                    content = 'not JSON'
                if self.path == '/slow':
                    time.sleep(.16)
                if self.path.startswith('/gemini'):
                    payload = {'candidates': [{'content': {'parts': [{'text': content}]}, 'finishReason': 'STOP'}],
                               'modelVersion': 'local-fixture'}
                else:
                    payload = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}], 'model': 'local-fixture'}
                encoded = json.dumps(payload).encode()
                self.send_response(503 if self.path == '/unavailable' else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
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
        self.trace_path = Path(self.temp.name) / 'trace.sqlite3'
        self.trace = LLMTraceStore(self.trace_path)
        self.server.calls.clear()
        self.credentials = patch.dict(os.environ, {'TEST_PLANNER_KEY': 'synthetic-loopback-only'})
        self.credentials.start()

    def tearDown(self):
        self.credentials.stop()
        self.temp.cleanup()

    def planner(self, path='/ok', *, name='primary', kind='openai_compatible', fallback=None, timeout=1):
        configs = []
        for i, target in enumerate([path] + ([fallback] if fallback else [])):
            provider = ProviderConfig(name if i == 0 else 'fallback', kind=kind, model=f'model-{name}',
                endpoint=f'http://127.0.0.1:{self.server.server_port}{target}',
                api_key_env='TEST_PLANNER_KEY',
                cost_per_1k_tokens_usd=.001 + i * .001, expected_latency_ms=10)
            configs.append(ModelRoute(LLMModelConfig(provider, timeout, 512)))
        runtime = LLMRuntime(PromptRegistry(), ModelRouter(configs), self.trace)
        return Planner(runtime)

    def request(self, rid='r1', kind='analysis'):
        return UserRequest(rid, 'Analyze supplied inputs' if kind == 'analysis' else 'Propose the supplied transfer',
                           context(kind), RoutingContext('GENERAL_GRAPH', max_latency_ms=1000))

    def test_actual_planner_generation_and_trace_validation_is_atomic(self):
        plan = self.planner().generate(self.request())
        self.assertIsInstance(plan, TaskPlan)
        self.assertEqual(plan.steps[1].action, 'analysis')
        event = self.trace.events()[-1]
        self.assertEqual(event['status'], 'SUCCEEDED')
        self.assertEqual(event['validation_result'], {'schema': 'PASS', 'plan': 'PASS'})
        self.assertTrue(self.trace.verify(event['record_hash']))

    def test_input_values_are_not_sent_or_saved_in_trace(self):
        self.planner().generate(self.request())
        wire = snapshot(self.server.calls[0][1])
        for secret in ['sensitive-account-value', '2026-10']:
            self.assertNotIn(secret, wire)
            self.assertNotIn(secret, snapshot(self.trace.events()))
        self.assertIn('input:account', wire)

    def test_semantic_invalid_plan_never_returns_or_falls_back(self):
        for path, code in [('/illegal', 'LLM_PLAN_UNKNOWN_ACTION'), ('/escalate', 'LLM_PLAN_ACTION_PERMISSION'),
                           ('/cycle', 'LLM_PLAN_CYCLE')]:
            self.server.calls.clear()
            with self.assertRaisesRegex(InferenceError, code):
                self.planner(path, fallback='/ok').generate(self.request(rid=path[1:]))
            self.assertEqual([p for p, _ in self.server.calls], [path])
            event = self.trace.events()[-1]
            self.assertEqual(event['status'], 'REJECTED')
            self.assertEqual(event['validation_result'], {'schema': 'PASS', 'plan': 'FAIL'})

    def test_illegal_agent_and_invalid_json_are_terminal(self):
        for path in ['/agent', '/invalid-json']:
            self.server.calls.clear()
            with self.assertRaises(InferenceError):
                self.planner(path, fallback='/ok').generate(self.request(rid=path[1:]))
            self.assertEqual([p for p, _ in self.server.calls], [path])
            self.assertEqual(self.trace.events()[-1]['validation_result'], {'schema': 'FAIL', 'plan': 'NOT_RUN'})

    def test_timeout_and_unavailable_fallback_preserve_contract(self):
        for path, timeout in [('/slow', .25), ('/unavailable', 1)]:
            self.server.calls.clear()
            planner = self.planner(path, fallback='/ok', timeout=timeout)
            planner.generate(self.request(rid=path[1:]))
            self.assertEqual([p for p, _ in self.server.calls], [path, '/ok'])
            self.assertEqual(self.trace.events()[-1]['provider'], 'fallback')

    def test_provider_switch_keeps_prompts_schema_validator_and_behavior(self):
        hashes, outputs = [], []
        for name, kind, path in [('deepseek', 'openai_compatible', '/ok'), ('glm', 'openai_compatible', '/ok'),
                                 ('gemini', 'gemini', '/gemini'), ('openai', 'openai', '/ok')]:
            plan = self.planner(path, name=name, kind=kind).generate(self.request(rid=name))
            outputs.append(plan.as_dict())
            hashes.append(self.trace.events()[-1]['prompt_hash'])
        self.assertEqual(len(set(hashes)), 1)
        self.assertTrue(all(p == outputs[0] for p in outputs))

    def test_transfer_is_proposal_and_native_safety_pipeline_remains(self):
        validated = self.planner('/transfer').generate_validated(self.request(kind='transfer'))
        self.assertEqual(validated.graph['nodes'][0]['node_type'], 'PROPOSE_ACTION')
        self.assertEqual(len(validated.compiled['steps']), 10)
        self.assertNotIn('tools', self.server.calls[0][1])

    def test_schema_only_success_is_not_planning_or_execution_success(self):
        evaluator = ProviderStabilityEvaluator(self.planner('/illegal'))
        result = evaluator.evaluate(EvaluationCase('attack', self.request(), TaskPlan.from_dict(analysis_plan())))
        self.assertTrue(result['schema_valid'])
        self.assertFalse(result['taskgraph_valid'])
        self.assertFalse(result['planner_success'])
        self.assertTrue(result['validator_rejected'])
        self.assertEqual(result['execution_alignment']['status'], 'NOT_RUN')

    def test_evaluation_success_requires_expected_semantics_and_no_execution_claim(self):
        evaluator = ProviderStabilityEvaluator(self.planner())
        expected = TaskPlan.from_dict(analysis_plan())
        case = EvaluationCase('normal', self.request(), expected)
        result = evaluator.evaluate(case)
        metrics = summarize([result])
        self.assertEqual(metrics['planner_success_rate'], 1)
        self.assertIsNone(metrics['execution_alignment'])
        self.assertEqual(metrics['execution_cases'], 0)

    def test_evaluation_without_expected_plan_is_not_task_success(self):
        result = ProviderStabilityEvaluator(self.planner()).evaluate(EvaluationCase('unscored', self.request()))
        self.assertIsNone(result['planner_success'])
        self.assertIsNone(summarize([result])['planner_success_rate'])

    def test_trace_verifier_cli_and_tampering(self):
        self.planner().generate(self.request())
        tail = self.trace.events()[-1]['record_hash']
        script = Path(__file__).resolve().parents[1] / 'trace/planning_trace.py'
        done = subprocess.run([sys.executable, str(script), '--db', str(self.trace_path), '--tail', tail],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['trace_integrity'], 'PASS')
        with closing(sqlite3.connect(self.trace_path)) as db:
            db.execute("UPDATE llm_trace_events SET payload=replace(payload,'\"plan\":\"PASS\"','\"plan\":\"FAIL\"') WHERE seq=2")
            db.commit()
        self.assertFalse(self.trace.verify(tail))

    def test_memory_does_not_silently_change_prompts_or_grant_actions(self):
        planner = self.planner()
        planner.generate(self.request())
        first = self.trace.events()[-1]['prompt_hash']
        memory = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        memory.save_preference(str(uuid.uuid4()), {'verbosity': 'detailed'})
        planner.generate(self.request(rid='r2'))
        self.assertEqual(self.trace.events()[-1]['prompt_hash'], first)


class AlignmentTests(unittest.TestCase):
    def test_unverified_receipts_and_missing_safety_steps_never_count_as_execution(self):
        validated = PlanValidator().validate(TaskPlan.from_dict(transfer_plan()), context('transfer'))
        first = validated.compiled['steps'][0]
        observation = {'step_id': first['step_id'], 'owner': first['owner'], 'status': 'CONFIRMED',
                       'plan_hash': validated.plan.plan_hash, 'evidence': 'untrusted'}
        self.assertEqual(execution_alignment(validated)['status'], 'NOT_RUN')
        self.assertEqual(execution_alignment(validated, [observation])['status'], 'UNVERIFIED')
        self.assertFalse(execution_alignment(validated, [observation], evidence_verifier=lambda _: False)['aligned'])

    def test_wrong_plan_hash_and_out_of_order_steps_fail_before_evidence_verifier(self):
        validated = PlanValidator().validate(TaskPlan.from_dict(transfer_plan()), context('transfer'))
        step = validated.compiled['steps'][-1]
        observation = {'step_id': step['step_id'], 'owner': step['owner'], 'status': 'CONFIRMED',
                       'plan_hash': 'incorrect', 'evidence': 'untrusted'}
        def forbidden(_):
            self.fail('Invalid binding must be rejected before evidence processing')
        self.assertEqual(execution_alignment(validated, [observation], evidence_verifier=forbidden)['status'], 'MISMATCH')
        observation['plan_hash'] = validated.plan.plan_hash
        self.assertEqual(execution_alignment(validated, [observation], evidence_verifier=forbidden)['status'], 'MISMATCH')

    def test_empty_evaluation_denominators_are_explicit(self):
        metrics = summarize([])
        self.assertEqual(metrics['cases'], 0)
        self.assertIsNone(metrics['planner_success_rate'])
        self.assertIsNone(metrics['execution_alignment'])


if __name__ == '__main__':
    unittest.main()
