"""Offline protocol acceptance across adapters; NOT a real model benchmark.

Uses an actual loopback HTTP server and synthetic, locked expected plans.
No bank client, executor, remote endpoint or API credentials are used.
"""
import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import uuid

from backend.llm.configuration import ProviderConfig
from evaluation.llm.metrics import EvaluationCase, ProviderStabilityEvaluator, summarize
from llm.contracts import LLMModelConfig
from llm.hashing import digest
from llm.planner import Planner, UserRequest
from llm.prompts import PromptRegistry
from llm.router import ModelRoute, ModelRouter, RoutingContext
from llm.runtime import LLMRuntime
from llm.trace import LLMTraceStore
from runtime.task_graph import TaskPlan
from validator import PlanningContext


def dataset():
    analysis = {'task_id': 'local1', 'goal': 'Analyze supplied month', 'steps': [
        {'node_id': 'Read', 'agent': 'finance', 'action': 'transaction.list',
         'parameters': {'account_id': 'input:account', 'month': 'input:month'}, 'dependency': []},
        {'node_id': 'Analyze', 'agent': 'finance', 'action': 'analysis',
         'parameters': {'records': 'Read:result'}, 'dependency': ['Read']}]}
    transfer = {'task_id': 'local1', 'goal': 'Propose supplied transfer', 'steps': [
        {'node_id': 'Propose', 'agent': 'transaction', 'action': 'create_transfer_plan',
         'parameters': {'account_id': 'input:account', 'recipient_name': 'input:recipient',
                        'amount': 'input:amount', 'currency': 'input:currency'}, 'dependency': []}]}
    return {'analysis': analysis, 'transfer': transfer}


@contextmanager
def fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            parts = self.path.split('/')
            provider, case = parts[1:3]
            plans = dataset()
            plan = plans['transfer'] if case == 'transfer' else plans['analysis']
            if case == 'illegal':
                plan['steps'][1]['action'] = 'payments.create'
            elif case == 'cycle':
                plan['steps'][0]['dependency'] = ['Analyze']
            elif case == 'changed-input':
                plan['steps'][0]['parameters']['account_id'] = 'input:other'
            content = 'not JSON' if case == 'invalid-json' else json.dumps(plan)
            if provider == 'gemini':
                payload = {'candidates': [{'content': {'parts': [{'text': content}]}, 'finishReason': 'STOP'}],
                           'modelVersion': 'synthetic-protocol-fixture'}
            else:
                payload = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                           'model': 'synthetic-protocol-fixture'}
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    trace = LLMTraceStore(output / ('trace-' + run_id + '.sqlite3'))
    rows, metrics = {}, {}
    env_name = 'TRUSTA_SYNTHETIC_LOOPBACK_EVAL_KEY'
    previous = os.environ.get(env_name)
    os.environ[env_name] = 'synthetic-loopback-only'
    try:
        with fixture_server() as server:
            for provider_name, kind in [('openai', 'openai'), ('gemini', 'gemini'),
                                        ('deepseek', 'openai_compatible'), ('glm', 'openai_compatible')]:
                rows[provider_name] = []
                for case_name in ('analysis', 'transfer', 'invalid-json', 'illegal', 'cycle', 'changed-input'):
                    provider = ProviderConfig(provider_name, kind=kind, model='synthetic-protocol-fixture',
                        endpoint=f'http://127.0.0.1:{server.server_port}/{provider_name}/{case_name}',
                        api_key_env=env_name, expected_latency_ms=10, cost_per_1k_tokens_usd=0)
                    planner = Planner(LLMRuntime(PromptRegistry(), ModelRouter([
                        ModelRoute(LLMModelConfig(provider, 2, 512))]), trace))
                    inputs = {'input:account': 'synthetic-account', 'input:other': 'synthetic-other',
                              'input:month': '2026-10'}
                    allowed = [('finance', 'transaction.list'), ('finance', 'analysis')]
                    if case_name == 'transfer':
                        inputs = {'input:account': 'synthetic-account', 'input:recipient': 'synthetic-recipient',
                                  'input:amount': 1, 'input:currency': 'EUR'}
                        allowed = [('transaction', 'create_transfer_plan')]
                    request = UserRequest(f'{run_id}:{provider_name}:{case_name}', 'Use the supplied task inputs',
                        PlanningContext('local1', inputs, allowed), RoutingContext('GENERAL_GRAPH'))
                    expected = TaskPlan.from_dict(dataset()['transfer' if case_name == 'transfer' else 'analysis'])
                    result = ProviderStabilityEvaluator(planner).evaluate(EvaluationCase(case_name, request, expected))
                    rows[provider_name].append(result)
                metrics[provider_name] = summarize(rows[provider_name])
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous
    baseline = rows['openai']
    checked = ('case_id', 'prompt_hash', 'schema_valid', 'taskgraph_valid', 'validator_rejected',
               'planner_success', 'error_code', 'execution_alignment')
    stable = all([{k: r[k] for k in checked} for r in values] ==
                 [{k: r[k] for k in checked} for r in baseline] for values in rows.values())
    oracle = {
        'analysis': (True, True, False, True, None),
        'transfer': (True, True, False, True, None),
        'invalid-json': (False, False, True, False, 'LLM_INVALID_OUTPUT'),
        'illegal': (True, False, True, False, 'LLM_PLAN_UNKNOWN_ACTION'),
        'cycle': (True, False, True, False, 'LLM_PLAN_CYCLE'),
        'changed-input': (True, True, False, False, None),
    }
    oracle_pass = all(tuple(r[k] for k in ('schema_valid', 'taskgraph_valid', 'validator_rejected',
                                          'planner_success', 'error_code')) == oracle[r['case_id']]
                      and r['execution_alignment'] == {'status': 'NOT_RUN', 'aligned': None}
                      for values in rows.values() for r in values)
    events = trace.events()
    report = {'scope': 'LOCAL_HTTP_PROTOCOL_ONLY', 'real_model_calls': 0, 'bank_writes': 0,
              'chain_broadcasts': 0, 'dataset_hash': digest(dataset()), 'run_id': run_id,
              'provider_behavior_stable': stable, 'expected_outcomes_pass': oracle_pass,
              'metrics': metrics, 'cases': rows,
              'trace_tail': events[-1]['record_hash'], 'trace_verified': trace.verify(events[-1]['record_hash'])}
    target = output / ('protocol-acceptance-' + run_id + '.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(target.resolve()), 'stable': stable,
                      'expected_outcomes_pass': oracle_pass, 'trace_verified': report['trace_verified']}))
    return 0 if stable and oracle_pass and report['trace_verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='data/llm-intelligence-v2/protocol')
    return run(parser.parse_args().output)


if __name__ == '__main__':
    raise SystemExit(main())
