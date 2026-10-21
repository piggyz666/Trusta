"""Opt-in, once-only paired real-provider evaluation; no transaction authorization.

Uses the existing final evaluator and its already published score amendments.
All semantic ProviderRequests are frozen before any network call. A returned
response is replayed only for local E2E scoring, never described as a second
live conversation. Run offline preparation before the explicit live command.
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.final_live_evaluation import dataset, evaluate
from backend.domain import canonical
from backend.llm.configuration import ProviderConfig
from backend.llm.contracts import InferenceError
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from scripts.estimate_sepolia_costs import load_local_environment

OUT = ROOT / 'data/model-evaluation/glm'
PROVIDERS = ('glm', 'deepseek', 'gemini')
PRICES = {'glm': (0, 0), 'deepseek': (.3, 1.2), 'gemini': (.25, 1.5)}
SOURCES = {
    'glm': 'https://docs.bigmodel.cn/cn/guide/start/pricing',
    'deepseek': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'gemini': 'https://ai.google.dev/gemini-api/docs/pricing',
}


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def configuration(name):
    common = dict(name=name, locality='external', max_sensitivity='restricted',
                  data_policy_reviewed=True, max_input_chars=40000)
    # Explicit transport-only proxy; never publish its URL or any credentials.
    if name != 'glm' and os.getenv('TRUSTA_GLM_EVAL_PROXY'):
        common['proxy_env'] = 'TRUSTA_GLM_EVAL_PROXY'
    if name == 'glm':
        return ProviderConfig(**common, kind='openai_compatible', model='glm-4-flash-250414',
            endpoint='https://open.bigmodel.cn/api/paas/v4/chat/completions',
            api_key_env='GLM_API_KEY', json_output=True)
    if name == 'deepseek':
        return ProviderConfig(**common, kind='openai_compatible', model='deepseek-flash',
            endpoint='https://api.deepseek.com/chat/completions',
            api_key_env='DEEPSEEK_API_KEY', json_output=True)
    if name == 'gemini':
        return ProviderConfig(**common, kind='gemini', model='gemini-3.1-flash-lite',
            endpoint='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent',
            api_key_env='GEMINI_API_KEY')
    raise ValueError('UNKNOWN_PROVIDER')


def protected_sources():
    files = list((ROOT / 'backend').rglob('*.py'))
    files += list((ROOT / 'contracts').rglob('*')) if (ROOT / 'contracts').exists() else []
    files += [p for p in (ROOT / 'scripts').glob('*.py') if 'anchor' in p.name or 'sepolia' in p.name or 'metamask' in p.name]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files) if p.is_file() and '__pycache__' not in p.parts}


def request_document(request):
    return asdict(request)


def paired_request(request):
    # Gateway subtracts local preflight elapsed time from its 60 s deadline.
    # Use a common SHORTER 55 s transport budget, never extend that deadline.
    if request.timeout_seconds < 55:raise InferenceError('EVALUATION_PREFLIGHT_TIMEOUT')
    return replace(request, timeout_seconds=55)


class FrozenRequestCollector:
    def __init__(self):
        self.requests = []

    def infer(self, request):
        self.requests.append(request_document(paired_request(request)))
        raise InferenceError('OFFLINE_REQUEST_FREEZE')


class Budget:
    """Persistent reservation BEFORE dispatch, never refunded after failures.

Single live runner holds an exclusive filesystem lease. Crash reservations
remain spent. No resume/retry can duplicate a case; a new run needs a new
explicitly approved budget, not a deleted ledger.
"""
    def __init__(self, path):
        self.path = Path(path)

    def reserve(self, case, name, request):
        doc = json.loads(self.path.read_text(encoding='utf-8'))
        input_price, output_price = PRICES[name]
        input_bound = len(canonical(request_document(request)).encode()) + 4096
        reservation = (input_bound * input_price + request.max_output_tokens * output_price) / 1e6
        if (doc['requests'] >= doc['max_requests'] or
                doc['reserved_usd'] + reservation > doc['max_usd'] or
                any(a['case'] == case and a['provider'] == name for a in doc['attempts'])):
            raise InferenceError('LLM_BUDGET_OR_DUPLICATE_STOP')
        index = len(doc['attempts'])
        doc['requests'] += 1
        doc['reserved_usd'] += reservation
        doc['attempts'].append(dict(case=case, provider=name, reservation_usd=reservation,
                                   request_hash=sha(request_document(request)), status='RESERVED'))
        save(self.path, doc)
        return index, reservation

    def settle(self, index, usage, error):
        doc = json.loads(self.path.read_text(encoding='utf-8'))
        attempt = doc['attempts'][index]
        measured = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                     for k in ('input_tokens', 'output_tokens'))
        rates = PRICES[attempt['provider']]
        cost = sum(usage[k] * rate for k, rate in zip(('input_tokens', 'output_tokens'), rates)) / 1e6 if measured else None
        attempt.update(status='FINISHED', usage=usage, error=error, estimated_usage_cost_usd=cost)
        # Unexpected usage is never silently ignored; permanently exhaust budget.
        if cost is not None and cost > attempt['reservation_usd'] + 1e-12:
            doc['reserved_usd'] += cost - attempt['reservation_usd']
            doc['max_requests'] = doc['requests']
            doc['stop_reason'] = 'PROVIDER_USAGE_EXCEEDED_RESERVATION'
        save(self.path, doc)
        return cost


class CapturingProvider:
    def __init__(self, config, case, frozen, budget, captures):
        self.config, self.case, self.frozen = config, case, frozen
        self.budget, self.captures = budget, captures
        self.payload = None
        cls = GeminiProvider if config.kind == 'gemini' else OpenAICompatibleProvider
        owner = self
        class Delegate(cls):
            def parse(self, payload):
                owner.payload = payload
                return super().parse(payload)
        self.delegate = Delegate(config)

    def infer(self, request):
        request=paired_request(request)
        if sha(request_document(request)) != sha(self.frozen):
            raise InferenceError('EVALUATION_INPUT_DRIFT')
        index, reserve = self.budget.reserve(self.case['id'], self.config.name, request)
        started = time.perf_counter()
        response, error = None, None
        try:
            response = self.delegate.infer(request)
            return response
        except Exception as exc:
            error = getattr(exc, 'code', 'PROVIDER_ERROR')
            raise
        finally:
            usage = response.usage if response else None
            if usage is None and isinstance(self.payload, dict):
                if self.config.kind == 'gemini':
                    u = self.payload.get('usageMetadata', {})
                    if all(type(u.get(k)) is int for k in ('promptTokenCount', 'candidatesTokenCount')):
                        usage = dict(input_tokens=u['promptTokenCount'],
                                     output_tokens=u['candidatesTokenCount'] + u.get('thoughtsTokenCount', 0))
                else:
                    u = self.payload.get('usage', {})
                    if all(type(u.get(k)) is int for k in ('prompt_tokens', 'completion_tokens')):
                        usage = dict(input_tokens=u['prompt_tokens'], output_tokens=u['completion_tokens'])
            cost = self.budget.settle(index, usage, error)
            record = dict(prompt=request.messages, output_schema=request.output_schema,
                request_hash=sha(request_document(request)), raw_response=self.payload,
                raw_output=response.text if response else None, usage=usage,
                returned_model=response.model if response else None, configured_model=self.config.model,
                error=error, latency_ms=round((time.perf_counter()-started)*1000, 3),
                cost_usd_estimate=cost, reservation_usd=reserve,
                transport_body=self.delegate.body(request))
            self.captures.append(record)
            save(OUT / 'calls' / (self.case['id'] + '-' + self.config.name + '.json'), record)


def dimensions(case):
    out = ['Banking Task']
    if case['mode'] != 'B_INTENT':out.append('Planning')
    if case['id'] in {'FC01','FC04','FC05','FC06','FC07','FC09','FC11','FC12','FC13','FC14','FC16','FC17','FC18','FC19','FC20','FC22','FC23'}:
        out.append('Transaction Safety')
    if case['id'] in {'FC12','FC18'}:out.append('Prompt Injection')
    if case.get('previous'):out.append('Multi-turn Context')
    return out


def diagnosis(row):
    calls = row['captured']
    if not calls:return 'evaluation_or_gateway_preflight'
    error = calls[0]['error']
    if error in {'LLM_TIMEOUT','LLM_PROVIDER_UNAVAILABLE','LLM_PROVIDER_CREDENTIALS','LLM_PROVIDER_HTTP_ERROR'}:
        return 'provider_transport_or_service'
    if error:return 'provider_output_protocol'
    if not row['score']['raw_correct']:return 'model_candidate_or_prompt_alignment'
    if not row['score']['e2e_expected_outcome']:return 'candidate_runtime_contract'
    return 'pass'


def summarize(results):
    summaries = {}
    for name in PROVIDERS:
        rows = [r for r in results if r['provider'] == name]
        calls = [x for r in rows for x in r['captured']]
        lat = sorted(c['latency_ms'] for c in calls)
        summaries[name] = dict(cases=len(rows), requests=len(calls),
            raw_correct=sum(r['score']['raw_correct'] for r in rows),
            validator_accepted=sum(r['score']['validator_accepted'] for r in rows),
            e2e_expected_outcome=sum(r['score']['e2e_expected_outcome'] for r in rows),
            unauthorized_bank_writes=sum(r['score']['unauthorized_bank_writes'] for r in rows),
            api_errors=sum(bool(c['error']) for c in calls),
            p50_ms=statistics.median(lat) if lat else None,
            p95_ms=lat[max(0, math.ceil(len(lat)*.95)-1)] if lat else None,
            measured_cost_usd=sum(c['cost_usd_estimate'] or 0 for c in calls),
            dimensions={d:dict(count=len(rr := [r for r in rows if d in dimensions(r['case'])]),
                raw_correct=sum(r['score']['raw_correct'] for r in rr),
                e2e=sum(r['score']['e2e_expected_outcome'] for r in rr))
                for d in ('Planning','Banking Task','Transaction Safety','Prompt Injection','Multi-turn Context')})
    return summaries


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'freeze.json').exists():raise SystemExit('FREEZE_ALREADY_EXISTS_PRESERVE_EVIDENCE')
    requests = {}
    for case in dataset():
        collector = FrozenRequestCollector()
        evaluate(case, 'glm', provider_config=configuration('glm'), provider=collector,
                 captures=[], amended_metrics=True)
        if len(collector.requests) != 1:raise RuntimeError('ONE_REQUEST_PER_CASE_REQUIRED:' + case['id'])
        requests[case['id']] = collector.requests[0]
    freeze = dict(created_at=datetime.now(timezone.utc).isoformat(), dataset=dataset(), requests=requests,
        request_hashes={key:sha(value) for key,value in requests.items()},
        dataset_hash=sha(dataset()), models={n:asdict(configuration(n)) for n in PROVIDERS},
        settings=dict(mode='assisted', version='final-convergence-1', timeout_seconds=60,
                      max_output_tokens=4096, max_fallbacks=0, graph_repair_attempts=0, graph_format='compact_v2'),
        prices_usd_per_million=PRICES, pricing_sources=SOURCES,
        protected_sources=protected_sources(),
        scoring='final_live_evaluation + existing LLM_Score_Amendment (absent fields, flat clarification, known payee/numeral validity)',
        evaluator_sha256=hashlib.sha256((ROOT/'benchmarks/final_live_evaluation.py').read_bytes()).hexdigest(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        tool_policy='same capability catalog/output schema; no provider tools; no bank authorization',
        comparison='exact semantic request parity, shared 55 s transport timeout within Gateway 60 s; JSON mode for all; native Gemini wire mapping only; no schema enforcement advantage',
        limitations=['author-created existing 23 cases, previously seen, not blind gold',
                     'single context carry-over case; local stored-response E2E replay',
                     'financial success means WAITING_USER proposal, no payment authorized'])
    save(OUT/'freeze.json', freeze)
    print(json.dumps(dict(cases=len(requests), frozen=True, providers=list(PROVIDERS)), ensure_ascii=False))


def live():
    load_local_environment()
    proxy = getproxies().get('https')
    if proxy:os.environ['TRUSTA_GLM_EVAL_PROXY'] = proxy
    for name in PROVIDERS:
        if not os.getenv(configuration(name).api_key_env):raise SystemExit('MISSING_CREDENTIAL:' + name)
    freeze = json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    if freeze['protected_sources'] != protected_sources():raise SystemExit('FROZEN_BACKEND_CHANGED')
    if freeze['evaluator_sha256'] != hashlib.sha256((ROOT/'benchmarks/final_live_evaluation.py').read_bytes()).hexdigest():raise SystemExit('EVALUATOR_CHANGED')
    if freeze['runner_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise SystemExit('RUNNER_CHANGED')
    ledger = OUT/'budget.json'
    if ledger.exists():raise SystemExit('ONCE_ONLY_RUN_ALREADY_RESERVED')
    with (OUT/'live.lock').open('x', encoding='utf-8') as lease:
        lease.write(str(os.getpid()))
    try:
        save(ledger, dict(authorization='user:100 requests/total USD1 synthetic GLM DeepSeek Gemini',
                         requests=0, reserved_usd=0, max_requests=100, max_usd=1, attempts=[]))
        (OUT/'calls').mkdir(exist_ok=True)
        results=[]
        for case in freeze['dataset']:
            for name in PROVIDERS:
                cfg=configuration(name);captures=[]
                provider=CapturingProvider(cfg,case,freeze['requests'][case['id']],Budget(ledger),captures)
                row=evaluate(case,name,provider_config=cfg,provider=provider,captures=captures,amended_metrics=True)
                row['failure_category']=diagnosis(row)
                results.append(row)
                save(OUT/'results.json', dict(summary=summarize(results),results=results))
                print(case['id'],name,json.dumps(row['score'],ensure_ascii=False),flush=True)
                state=json.loads(ledger.read_text(encoding='utf-8'))
                if state['requests'] >= state['max_requests'] or state['reserved_usd'] >= state['max_usd']:
                    print('BUDGET_STOP',flush=True);break
            else:continue
            break
        parity={case['id']:len({c['request_hash'] for r in results if r['case']['id']==case['id'] for c in r['captured']}) == 1
                for case in freeze['dataset']}
        save(OUT/'integrity.json', dict(protected_sources_unchanged=freeze['protected_sources']==protected_sources(),
            semantic_input_parity=parity, result_sha256=hashlib.sha256((OUT/'results.json').read_bytes()).hexdigest(),
            external_bank_writes=0, external_blockchain_broadcasts=0,
            note='All service instances are fresh temporary synthetic Virtual Bank databases; no authorization issued.'))
        print(json.dumps(summarize(results),ensure_ascii=False),flush=True)
    finally:
        (OUT/'live.lock').unlink(missing_ok=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','live'])
    args=parser.parse_args()
    prepare() if args.mode=='prepare' else live()
