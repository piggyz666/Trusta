"""Read-only historical diagnosis and LOCAL archived-response replay.

No live mode, credentials, new budgets, inference retries or prompt repairs.
Replays travel through the existing Provider/Gateway/Planner/Virtual Bank.
Archived truncation cannot be recovered by changing the replay token budget.
"""
import argparse
from collections import Counter
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import threading
from backend.llm.contracts import InferenceError
from backend.llm.validation import strict_json
from benchmarks.glm_evaluation import configuration
from llm.hashing import digest, text_hash
from llm.providers.common import protocol_diagnostics
from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
from llm_evaluation.evaluator import run_case, request_for
from llm_evaluation.run import dataset, save
from sandbox import BankSandbox
from schemas import task_plan_schema

ARCHIVE = Path('data/intelligence-v21/live')


def historical_payload(row):
    path = ARCHIVE / 'responses' / (digest(row['provider'] + ':' + row['case_id']) + '.json')
    if not path.exists():
        return None, None
    doc = json.loads(path.read_text(encoding='utf-8'))
    return doc.get('provider_payload'), {'file':path.as_posix(), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def describe(payload, provider):
    """Separate observed transport completeness from Schema and task validity."""
    if payload is None:
        return {'provider_reached':None, 'complete_response':None, 'schema_echo':None,
                'usage':None, 'finish_reason':None, 'response_hash':None}
    try:
        if provider == 'gemini':
            candidate = payload['candidates'][0]
            finish = candidate.get('finishReason')
            text = ''.join(p.get('text', '') for p in candidate['content']['parts'] if not p.get('thought'))
            usage = payload.get('usageMetadata')
            complete = finish == 'STOP'
        else:
            candidate = payload['choices'][0]
            finish = candidate.get('finish_reason')
            text = candidate['message'].get('content') or ''
            usage = payload.get('usage')
            complete = finish == 'stop'
        try:
            echo = strict_json(text) == task_plan_schema()
        except InferenceError:
            echo = False
        return {'provider_reached':True, 'complete_response':complete, 'schema_echo':echo,
                'finish_reason':finish, 'response_hash':text_hash(text), 'usage':usage,
                'diagnostics':protocol_diagnostics(payload, 'gemini' if provider == 'gemini' else 'openai_compatible')}
    except (KeyError, TypeError, IndexError):
        return {'provider_reached':True, 'complete_response':False, 'schema_echo':False,
                'usage':None, 'finish_reason':None, 'response_hash':None}


class ArchivedProtocolAdapter(CompatibleAdapter):
    """Evaluation fixture sends an unchanged archived HTTP payload, not a model."""
    def __init__(self, provider, payload, trace, tokens):
        owner = self
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        config = replace(configuration(provider), locality='local', proxy_env='',
            api_key_env='TRUSTA_ARCHIVE_FIXTURE_KEY' if provider == 'gemini' else '',
            endpoint=f'http://127.0.0.1:{self.server.server_port}/')
        super().__init__(config, trace, timeout=5, max_output_tokens=tokens)
        self.source = 'ARCHIVED_REAL_RESPONSE_REPLAY'

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()


def diagnosis(rows):
    observations = []
    for row in rows:
        payload, evidence = historical_payload(row)
        obs = describe(payload, row['provider'])
        failure = row['failure']
        category = ('OUTPUT_CAPACITY' if obs['finish_reason'] in {'length', 'MAX_TOKENS'} else
                    'SCHEMA_ECHO' if obs['schema_echo'] else
                    'TRANSPORT_OR_SERVICE' if failure in {'LLM_TIMEOUT','LLM_PROVIDER_UNAVAILABLE'} else
                    'INTERRUPTED' if failure == 'EVALUATION_INTERRUPTED' else
                    'STRUCTURE_OR_SEMANTICS' if failure else 'EXECUTED_NOT_NECESSARILY_GOAL_COMPLETE')
        observations.append({'provider':row['provider'], 'case_id':row['case_id'],
            'historical_error':failure, 'historical_goal_match':row['planner_success'],
            'category':category, 'evidence':evidence, **obs})
    return observations


def summarize(rows):
    result = {}
    for p in ('deepseek','glm','gemini'):
        rs = [r for r in rows if r['provider'] == p]
        result[p] = {'cases':len(rs), 'replayed':sum(r['replay_status'] != 'NOT_RUN' for r in rs),
            'goal_matched':sum(r.get('planner_success') is True for r in rs),
            'independent_verified':sum(r.get('audit_verified') is True for r in rs),
            'goal_and_proof':sum(r.get('planner_success') is True and r.get('audit_verified') is True for r in rs),
            'failures':dict(Counter(r.get('failure') or 'EXECUTED' for r in rs))}
    return result


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'replay-results.json').exists() or (output / 'banks').exists():
        raise InferenceError('EVALUATION_PRESERVE_RESULTS')
    cases, manifest = dataset('normal_cases')
    dataset('attack_cases')  # verify the second frozen manifest too
    cases = {c['id']:c for c in cases}
    historical = json.loads((ARCHIVE / 'results.json').read_text(encoding='utf-8'))
    frozen = {(r['provider'],r['case_id']):digest(r['messages']) for r in
              json.loads((ARCHIVE / 'frozen-requests.json').read_text(encoding='utf-8'))}
    save(output / 'root-causes.json', diagnosis(historical['rows']))
    # Do not load .env or provider secrets. The only key is an explicit LOCAL fixture.
    old_key = os.environ.get('TRUSTA_ARCHIVE_FIXTURE_KEY')
    os.environ['TRUSTA_ARCHIVE_FIXTURE_KEY'] = 'public-local-fixture'
    results = {'raw':[], 'stabilized':[]}
    try:
        for historical_row in historical['rows']:
            payload, source = historical_payload(historical_row)
            for track, tokens in (('raw',768), ('stabilized',2048)):
                provider, cid = historical_row['provider'], historical_row['case_id']
                obs = describe(payload, provider)
                row = {'provider':provider, 'case_id':cid, 'track':track,
                    'scope':'ARCHIVED_REAL_RESPONSE_REPLAY', 'new_paid_calls':0,
                    'source':source, 'historical_latency':historical_row.get('latency'),
                    'original_score':historical_row, **obs}
                if payload is None:
                    row.update(replay_status='NOT_RUN', failure='ARCHIVED_RESPONSE_MISSING',
                        schema_valid=None, dag_valid=None, planner_success=None, audit_verified=None,
                        controlled_execution=False, clarification=False, waiting_for_authorization=False)
                else:
                    case = cases[cid]
                    with ArchivedProtocolAdapter(provider, payload, output / 'traces' / track / (provider+'-'+cid+'.sqlite3'), tokens) as adapter:
                        if digest(adapter.request_material(request_for(case, provider))) != frozen[(provider,cid)]:
                            raise InferenceError('EVALUATION_PROMPT_DRIFT')
                        bank = BankSandbox(output / 'banks' / track / (provider+'-'+cid))
                        scored, proof = run_case(bank, adapter, case, approve_synthetic_payment=True)
                        events = adapter.trace.events()
                        terminal = events[-1] if events else {}
                        validation = terminal.get('validation_result', {})
                        row.update(scored, source=source, replay_status='REPLAYED',
                            schema_valid=validation.get('schema') == 'PASS', dag_valid=validation.get('plan') == 'PASS',
                            controlled_execution=proof is not None,
                            clarification=False, waiting_for_authorization=scored['failure'] == 'WAITING_USER')
                        row['observed_prompt_hash'] = terminal.get('prompt_hash')
                        # Same existing prompt rendering; no test-case-specific adaptation.
                        row['rendered_messages_hash'] = digest(adapter.request_material(request_for(case, provider)))
                        if proof is not None:
                            save(output / 'proofs' / track / (provider+'-'+cid+'.json'), proof)
                            key = bank.root / 'audit-public-key.pem'
                            target = output / 'proofs' / track / (provider+'-'+cid+'.pem')
                            target.write_bytes(key.read_bytes())
                results[track].append(row)
                save(output / 'cases' / track / (provider+'-'+cid+'.json'), row)
                print(track, provider, cid, row['replay_status'], row['failure'], flush=True)
    finally:
        if old_key is None:
            os.environ.pop('TRUSTA_ARCHIVE_FIXTURE_KEY', None)
        else:
            os.environ['TRUSTA_ARCHIVE_FIXTURE_KEY'] = old_key
    for raw, stabilized in zip(results['raw'], results['stabilized']):
        if raw.get('rendered_messages_hash') != stabilized.get('rendered_messages_hash'):
            raise InferenceError('EVALUATION_PROMPT_DRIFT')
        for field in ('planner_success','execution_alignment','audit_verified','failure'):
            if raw.get(field) != stabilized.get(field):
                raise InferenceError('EVALUATION_ARCHIVE_SCORE_DRIFT')
    results.update(summary={k:summarize(results[k]) for k in ('raw','stabilized')},
        dataset_sha256=manifest['sha256'], new_paid_calls=0,
        stabilized_live='NOT_RUN', real_model_attacks='NOT_RUN',
        cost_usd=0, scope='ARCHIVED_REAL_RESPONSE_REPLAY',
        limitation='Changing a replay budget cannot extend a truncated historical response. Network errors with no payload stay NOT_RUN. No fresh model improvement claimed.')
    save(output / 'replay-results.json', results)
    return results['summary']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
