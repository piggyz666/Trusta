"""Opt-in live synthetic regression and explicit local fault injection.

The v2 gold is reused, never reauthored or repaired from model outputs. No live
credentials are written, no approval is issued, and no bank write is allowed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Config
from backend.domain import digest
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceError, ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.service import TrustaService
from benchmarks.taskgraph_live_evaluation import score

DATASET = ROOT / 'benchmarks/taskgraph_holdout_v2.json'


def configuration(name, reviewed=True):
    """Review here is strictly scoped to the isolated synthetic test fixture."""
    common = dict(name=name, locality='external',
                  max_sensitivity='restricted' if reviewed else 'public',
                  data_policy_reviewed=reviewed,
                  proxy_env='SECOND_PROVIDER_SYNTHETIC_PROXY' if os.getenv('SECOND_PROVIDER_SYNTHETIC_PROXY') else '')
    if name == 'deepseek':
        return ProviderConfig(**common, kind='openai_compatible',
            model=os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'),
            endpoint='https://api.deepseek.com/chat/completions',
            api_key_env='DEEPSEEK_API_KEY', json_output=True)
    if name == 'gemini':
        model = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite')
        return ProviderConfig(**common, kind='gemini', model=model,
            endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
            api_key_env='GEMINI_API_KEY', structured_outputs=True)
    raise ValueError('Unknown evaluation provider')


class CapturingProvider:
    def __init__(self, config, captures, fault=None):
        self.config, self.captures, self.fault = config, captures, fault
        cls = GeminiProvider if config.kind == 'gemini' else OpenAICompatibleProvider
        self.delegate = cls(config)
        self.attempts = 0

    def infer(self, request):
        self.attempts += 1
        if self.fault == 'unavailable':
            raise InferenceError('LLM_PROVIDER_UNAVAILABLE', retryable=True)
        if self.fault == 'invalid':
            return ProviderResponse('{"schema_version":1,"nodes":[],"unexpected":"injected"}')
        response = self.delegate.infer(request)
        self.captures.append({'provider':self.config.name, 'returned_model':response.model,
            'usage':response.usage, 'raw_synthetic_output':response.text,
            'request_hash':digest(request.messages), 'schema_hash':digest(request.output_schema)})
        return response


def evaluate(case, order=('deepseek',), fault=None, public_backup=False, mode='assisted', provider_factory=CapturingProvider):
    captures = []
    configs = tuple(configuration(name, reviewed=not (public_backup and index > 0))
                    for index, name in enumerate(order))
    adapters = {cfg.name:provider_factory(cfg, captures, fault if index == 0 else None)
                for index, cfg in enumerate(configs)}
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as directory:
        service = TrustaService(Config(db_path=directory+'/bank.sqlite', banking_version=5,
            communication_version=3, signing_key=b'second-provider-synthetic-only'))
        service.llm = LLMGateway(LLMSettings(mode=mode, version='phase6-second-provider',
            timeout_seconds=60, max_output_tokens=4096, max_fallbacks=len(order)-1,
            providers=configs, routes={'plan_proposal':list(order)}),
            service.db, service.audit, adapters)
        service.llm.identity_guard = service.crypto_registry.require_active
        task = service.graphs.propose('demo', case['goal'], case['inputs'])
        calls = [json.loads(row['body']) for row in service.db.rows('SELECT body FROM llm_calls')]
        writes = len(service.db.rows('SELECT * FROM transactions')) + len(service.db.rows('SELECT * FROM bank_operations'))
        assert writes == 0, 'Unapproved bank mutation'
        caps = sorted({node['capability'] for node in task.get('graph_state',{}).get('graph',{}).get('nodes',[])})
        return {'id':case['id'], 'group':case['group'], 'gold':case,
            'passed':score(case,task), 'status':task['status'], 'errors':task['errors'],
            'capabilities':caps, 'installed_graph':bool(task['steps']),
            'clarification':task.get('result') if not task['steps'] else None,
            'order':list(order), 'mode':mode, 'fault_injection':fault,
            'fault_origin':'LOCAL_TEST_DOUBLE_NO_PRIMARY_NETWORK_CALL' if fault else None,
            'adapter_attempts':{name:adapter.attempts for name,adapter in adapters.items()},
            'bank_writes':writes, 'calls':calls, 'real_model_responses':len(captures),
            'captured_synthetic_outputs':captures,
            'latency_ms':round((time.monotonic()-started)*1000,2)}


def summarize(rows):
    result = {}
    for group in sorted({row['group'] for row in rows} | {'all'}):
        selected = [row for row in rows if group == 'all' or row['group'] == group]
        result[group] = {'numerator':sum(r['passed'] for r in selected),
            'denominator':len(selected), 'real_model_responses':sum(r['real_model_responses'] for r in selected),
            'installed_graphs':sum(r['installed_graph'] for r in selected),
            'bank_writes':sum(r['bank_writes'] for r in selected)}
    return result


def run_routes(case):
    rows = []
    scenarios = [
        ('deepseek-first', ('deepseek','gemini'), None, False, 'assisted'),
        ('gemini-first', ('gemini','deepseek'), None, False, 'assisted'),
        ('injected-deepseek-outage', ('deepseek','gemini'), 'unavailable', False, 'assisted'),
        ('injected-gemini-outage', ('gemini','deepseek'), 'unavailable', False, 'assisted'),
        ('invalid-output-no-fallback', ('deepseek','gemini'), 'invalid', False, 'assisted'),
        ('public-backup-denied', ('deepseek','gemini'), 'unavailable', True, 'assisted'),
        ('shadow-does-not-install', ('deepseek',), None, False, 'shadow')]
    for name, order, fault, public, mode in scenarios:
        row = evaluate(case, order, fault, public, mode);row['scenario'] = name
        if name == 'invalid-output-no-fallback':
            row['passed'] = row['status']=='BLOCKED' and row['adapter_attempts']['gemini']==0 and not row['installed_graph']
        elif name == 'public-backup-denied':
            row['passed'] = row['status']=='BLOCKED' and row['adapter_attempts']['gemini']==0 and row['calls'][0]['rejected_routes'].get('gemini')=='DATA_SENSITIVITY'
        elif mode == 'shadow':
            row['passed'] = not row['installed_graph'] and row['calls'][0]['status']=='SUCCEEDED' and row['real_model_responses']==1
        else:
            selected = order[1] if fault else order[0]
            row['passed'] = row['passed'] and row['calls'][0]['provider']==selected and row['real_model_responses']==1
        rows.append(row)
        print(json.dumps({'scenario':name,'passed':row['passed'],'status':row['status']}),flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('smoke','regression','routes'), default='smoke')
    args = parser.parse_args()
    if not os.getenv('DEEPSEEK_API_KEY'):raise SystemExit('DEEPSEEK_API_KEY missing; live verification unavailable')
    if args.mode=='routes' and not os.getenv('GEMINI_API_KEY'):raise SystemExit('GEMINI_API_KEY missing')
    proxy = getproxies().get('https')
    if proxy:os.environ['SECOND_PROVIDER_SYNTHETIC_PROXY']=proxy
    data = DATASET.read_bytes(); dataset = json.loads(data); dataset_hash = hashlib.sha256(data).hexdigest()
    if args.mode == 'routes':
        rows = run_routes(dataset['cases'][0])
    else:
        cases = dataset['cases'] if args.mode=='regression' else dataset['cases'][:1]
        rows = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in as_completed([pool.submit(evaluate,case) for case in cases]):
                row=future.result();rows.append(row)
                print(json.dumps({k:row[k] for k in ('id','passed','status','errors','real_model_responses')}),flush=True)
        rows.sort(key=lambda r:r['id'])
    assert DATASET.read_bytes()==data
    report = {'evaluated_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'scope':'ISOLATED_SYNTHETIC_ONLY; no approvals; financial proposals must remain WAITING_USER',
        'dataset_source':str(DATASET.relative_to(ROOT)), 'dataset_hash':dataset_hash,
        'gold_is_reused_regression_set':True, 'original_100_case_benchmark_replaced':False,
        'configured_models':{name:configuration(name).model for name in ('deepseek','gemini')},
        'primary_remote_outage_observed':False,
        'metrics':summarize(rows), 'results':rows}
    path = ROOT / ('data/deepseek-smoke.json' if args.mode=='smoke' else 'docs/phase6-second-provider-'+args.mode+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(path),'metrics':report['metrics']}))


if __name__=='__main__':main()
