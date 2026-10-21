"""Opt-in v2.1 evaluation. Live inference, exclusively LOCAL Virtual Bank execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
from backend.domain import canonical
from backend.llm.contracts import InferenceError
from sandbox import BankSandbox
from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
from llm_evaluation.adapters.mock_adapter import MockAdapter
from llm_evaluation.budget import LiveBudget
from llm_evaluation.evaluator import run_case, request_for
from alignment.metrics import summarize

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def dataset(name):
    manifest = json.loads((ROOT / 'llm_evaluation/scenarios' / (name + '.json')).read_text(encoding='utf-8'))
    path = ROOT / manifest['dataset']
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise InferenceError('EVALUATION_DATASET_CHANGED')
    return json.loads(path.read_text(encoding='utf-8')), manifest


def offline(out):
    rows = []
    for name in ('normal_cases', 'attack_cases'):
        cases, manifest = dataset(name)
        save(out / (name + '-manifest.json'), manifest)
        for case in cases:
            bank = BankSandbox(out / 'banks' / case['id'])
            with MockAdapter(case.get('raw_output', case['expected_plan']), out / 'traces' / (case['id'] + '.sqlite3')) as adapter:
                if case.get('attack_kind') == 'replay':
                    r = bank.prepare(adapter, request_for(case, adapter.config.name))
                    r = bank.start(r['execution_id'], 'demo')
                    try: bank.prepare(adapter, request_for(case, adapter.config.name))
                    except InferenceError as error: code = error.code
                    else: code = 'REPLAY_ACCEPTED'
                    row = {'case_id':case['id'], 'attack':True, 'source':adapter.source,
                           'failure':code, 'unauthorized_execution':bool(r['execution']['receipts']),
                           'replay_rejected':code == 'LLM_DUPLICATE_REQUEST', 'execution_alignment':None}
                    bundle = bank.export(r['execution_id'], 'demo')
                else:
                    row, bundle = run_case(bank, adapter, case, approve_synthetic_payment=True)
                rows.append(row)
                save(out / 'cases' / (case['id'] + '.json'), {'result':row, 'evidence':bundle})
                print(case['id'], row.get('execution_alignment'), row.get('failure'), flush=True)
                save(out / 'results.json', {'source':'LOCAL_HTTP_FIXTURE', 'rows':rows, 'metrics':summarize(rows)})
    return rows


def live(out, approved, use_local_system_proxy=False):
    from scripts.estimate_sepolia_costs import load_local_environment
    from benchmarks.glm_evaluation import configuration
    load_local_environment()
    if use_local_system_proxy:
        from urllib.request import getproxies
        from urllib.parse import urlsplit
        proxy = getproxies().get('https', '')
        parsed = urlsplit(proxy)
        if (parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.scheme != 'http'
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise InferenceError('EVALUATION_LOCAL_PROXY_INVALID')
        # Explicit process-only evaluation transport; no project or financial configuration changes.
        os.environ['TRUSTA_GLM_EVAL_PROXY'] = proxy
    budget = LiveBudget(out / 'budget.sqlite3', approved=approved)
    cases, manifest = dataset('normal_cases')
    save(out / 'dataset-manifest.json', manifest)
    adapters = {p:CompatibleAdapter(configuration(p), out / 'traces' / (p + '.sqlite3'), budget=budget,
                                   observation_dir=out / 'responses')
                for p in ('deepseek','glm','gemini')}
    # Validate all configurations BEFORE reserving or dispatching any request.
    for a in adapters.values():
        a.config.validate()
        if not os.getenv(a.config.api_key_env): raise InferenceError('EVALUATION_CREDENTIAL_MISSING')
    requests = [{'provider':p, 'model':a.config.model, 'case_id':c['id'],
                 'messages':a.request_material(request_for(c,p))}
                for c in cases for p,a in adapters.items()]
    frozen = out / 'frozen-requests.json'
    if frozen.exists() and canonical(json.loads(frozen.read_text(encoding='utf-8'))) != canonical(requests):
        raise InferenceError('EVALUATION_REQUESTS_CHANGED')
    save(frozen, requests)
    rows = []
    for case in cases:
        for provider, adapter in adapters.items():
            path = out / 'cases' / (provider + '-' + case['id'] + '.json')
            if path.exists():
                # Resume scoring saved evidence. NEVER dispatch an already claimed paid request.
                rows.append(json.loads(path.read_text(encoding='utf-8'))['result']); continue
            adapter.close_interrupted(request_for(case, provider).request_id)
            bank = BankSandbox(out / 'banks' / (provider + '-' + case['id']))
            prior = bank.service.db.one('SELECT id FROM alignment_runs WHERE request_id=?',
                                        (request_for(case, provider).request_id,))
            if prior:
                record = bank.get(prior['id'], 'demo')
                # Retain the consumed reservation and an interrupted request; never call it again.
                if record['status'] == 'PLANNING':
                    record = bank._save(record | {'status':'REJECTED', 'result':'EVALUATION_INTERRUPTED',
                        'validation_result':'FAIL'}, expected_status='PLANNING')
                row = {'case_id':case['id'], 'provider':provider,'model':adapter.config.model,
                       'source':adapter.source,'planner_success':False,'execution_alignment':None,
                       'failure':'EVALUATION_INTERRUPTED','audit_verified':False,'unauthorized_execution':False,
                       'request_hash':None,'plan_hash':None,'validation_result':'NOT_RUN','latency':None}
                bundle = bank.export(record['execution_id'], 'demo')
            else:
                row, bundle = run_case(bank, adapter, case, approve_synthetic_payment=True)
            rows.append(row)
            save(path, {'result':row, 'evidence':bundle})
            save(out / 'results.json', {'source':'REAL_PROVIDER_HTTP', 'rows':rows, 'metrics':summarize(rows),
                'budget':budget.status(), 'environment':{'python':platform.python_version(),'platform':platform.platform()},
                'dataset_sha256':manifest['sha256'], 'frozen_requests_hash':hashlib.sha256(frozen.read_bytes()).hexdigest()})
            print(provider, case['id'], 'planning', row['planner_success'], 'alignment', row['execution_alignment'],
                  'error', row['failure'], 'budget', budget.status()['requests'], flush=True)
            if row['failure'] == 'LLM_BUDGET_EXHAUSTED': return rows
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('offline','live'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--approved-budget', action='store_true', help='Explicit current 60-request / USD1 authorization')
    parser.add_argument('--use-local-system-proxy', action='store_true', help='Explicitly reuse credential-free loopback HTTPS proxy for evaluation only')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == 'live': live(args.output, args.approved_budget, args.use_local_system_proxy)
    else: offline(args.output)


if __name__ == '__main__': main()
