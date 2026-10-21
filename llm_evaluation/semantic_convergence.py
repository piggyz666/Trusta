"""v3 preflight/offline acceptance; historical scores and records are read only."""
import argparse
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
from collections import Counter
from llm.configuration import load_settings
from llm.credentials import refresh_credentials
from llm.hashing import digest, snapshot
from llm_evaluation.evaluator import request_for, run_case
from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
from llm_evaluation.adapters.mock_adapter import MockAdapter
from llm_evaluation.run import dataset, save
from evaluation.llm.semantic import equivalent
from runtime.task_graph import TaskPlan
from validator import PlanValidator
from sandbox import BankSandbox
from llm_evaluation.budget import LiveBudget
from contextlib import closing
from backend.llm.contracts import InferenceError


class SemanticBudget(LiveBudget):
    """Separate manifest-bound 120 request/$3 ledger; never opens old ledgers."""
    def __init__(self, out):
        pin=json.loads((out/'manifest-pin.json').read_text(encoding='utf-8'))['sha256']
        body=(out/'manifest.json').read_bytes()
        if hashlib.sha256(body).hexdigest()!=pin:
            raise InferenceError('EVALUATION_MANIFEST_CHANGED')
        manifest=json.loads(body)
        auth=json.loads((out/'authorization.json').read_text(encoding='utf-8'))
        required={'approved':True,'max_requests':120,'max_usd':3,'manifest_sha256':pin,'retries':0,'fallbacks':0}
        if any(auth.get(k)!=v or type(auth.get(k)) is not type(v) for k,v in required.items()):
            raise InferenceError('LLM_BUDGET_APPROVAL_REQUIRED')
        if manifest['full_plan_reserved_usd']>3 or manifest['request_count']!=120:
            raise InferenceError('EVALUATION_FULL_PLAN_UNFUNDED')
        self.allowed={r['request_id']:r for r in manifest['requests']}
        if len(self.allowed)!=120: raise InferenceError('EVALUATION_DUPLICATE_REQUEST')
        self.path=str((out/'budget.sqlite3').resolve())
        with closing(self._db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), max_requests INTEGER, max_microusd INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (request_id TEXT PRIMARY KEY, provider TEXT, microusd INTEGER, request_hash TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS approval (id INTEGER PRIMARY KEY CHECK(id=1), manifest_hash TEXT, approval_hash TEXT)')
            db.execute('INSERT OR IGNORE INTO budget VALUES(1,120,3000000)')
            db.execute('INSERT OR IGNORE INTO approval VALUES(1,?,?)',(pin,digest(auth)))
            if db.execute('SELECT max_requests,max_microusd FROM budget').fetchone()!=(120,3000000) or db.execute('SELECT manifest_hash,approval_hash FROM approval').fetchone()!=(pin,digest(auth)):
                raise InferenceError('LLM_BUDGET_IMMUTABLE')

    def reserve(self, request_id, provider, messages, output_tokens, *, schema=None):
        expected=self.allowed.get(request_id)
        if not expected or provider!=expected['provider'] or digest(messages)!=expected['request_hash'] or output_tokens!=2048 or schema is not None:
            raise InferenceError('EVALUATION_REQUEST_CHANGED')
        return super().reserve(request_id,provider,messages,output_tokens,schema=schema)

PROVIDERS = ('deepseek', 'kimi', 'mimo')


def fixture_proposal(case):
    """Explicit Gold-derived offline fixture, never a measured model response."""
    specs = PlanValidator().specifications()
    steps = case['expected_plan']['steps']
    positions = {n['node_id']: i for i, n in enumerate(steps)}
    return {'version': 3, 'clarification': None, 'calls': [
        {'agent': n['agent'], 'action': specs[(n['agent'], n['action'])].permission_action,
         'parameters': {k: ref if ref.startswith('input:') else 'call:'+str(positions[ref[:-7]])
                        for k, ref in n['parameters'].items()}} for n in steps]}


def profiles():
    return {p: load_settings(Path('config') / ('llm.'+p+'.semantic-v3.yaml')) for p in PROVIDERS}


def write_once(path, value):
    encoded = snapshot(value).encode()
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError('immutable acceptance artifact changed')
    else:
        with path.open('xb') as stream:
            stream.write(encoded)


def preflight(out, *, revision=False, budget_root=None, final_fill=False):
    """No HTTP, DNS, signing, bank execution or model fees."""
    from scripts.estimate_sepolia_costs import load_local_environment
    load_local_environment()
    settings = profiles()
    credentials = refresh_credentials(s.providers[0].api_key_env for s in settings.values())
    cases = [c for name in ('normal_cases', 'attack_cases') for c in dataset(name)[0]]
    if revision:
        # Pilot requests are NEVER repeated. These are the 39 remaining unique
        # cases per provider, under the original aggregate request/cost caps.
        cases = [c for c in cases if c['id'] != 'balance01']
    # Independent dataset version, copied with provenance; old bytes never change.
    write_once(out/'dataset.v3.json', {'version': 'semantic-3.0.0', 'cases': cases,
        'source': {n: dataset(n)[1] for n in ('normal_cases', 'attack_cases')}})
    rows = []
    claimed=set()
    if budget_root is not None:
        original=SemanticBudget(budget_root)
        with closing(original._db()) as db:
            claimed={r[0] for r in db.execute('SELECT request_id FROM reservations')}
    for provider, config in settings.items():
        adapter = CompatibleAdapter(config.providers[0], out/'preflight-traces'/f'{provider}.sqlite3',
                                    contract_version='3.2.0' if budget_root or final_fill else '3.1.0' if revision else '3.0.0')
        for case in cases:
            if final_fill and provider!='deepseek' and case['id'] not in {'balance01','balance02'}: continue
            request = request_for(case, provider)
            if request.request_id in claimed: continue
            schema, _ = adapter.planner.request_contract(request)
            messages = adapter.request_material(request)
            # Same existing pessimistic byte bound: no refunds/retries. Pricing
            # ceilings must be reviewed again before a NEW approval is accepted.
            cost = (len(snapshot(messages).encode())+4096)*2 + 2048*4
            rows.append({'provider': provider, 'case_id': case['id'], 'request_id': request.request_id,
                'case_hash': digest(case), 'messages': messages, 'request_hash': digest(messages),
                'schema_hash': digest(schema), 'provider_config': asdict(config.providers[0]),
                'provider_config_hash': digest(asdict(config.providers[0])),
                'max_output_tokens': 2048, 'reserved_microusd': cost})
    manifest = {'version': 'semantic-3.0.0', 'scope': 'LOCAL_VIRTUAL_BANK_ONLY',
        'requests': rows, 'request_count': len(rows), 'retries': 0, 'fallbacks': 0,
        'connectivity_gate': 'first normal case per provider; stop remaining calls if gate fails',
        'full_plan_reserved_usd': sum(r['reserved_microusd'] for r in rows)/1e6,
        'input_ceiling_usd_per_million': 2, 'output_ceiling_usd_per_million': 4,
        'prompt_file_sha256': hashlib.sha256(Path('llm/planner/prompt.v3.2.yaml' if budget_root or final_fill else 'llm/planner/prompt.v3.1.yaml' if revision else 'llm/planner/prompt.v3.yaml').read_bytes()).hexdigest()}
    write_once(out/'manifest.json', manifest)
    save(out/'manifest-pin.json', {'sha256': hashlib.sha256((out/'manifest.json').read_bytes()).hexdigest()})
    status = {'credentials': credentials, 'paid_requests': 0,
        'live_acceptance': 'PENDING_APPROVAL', 'service_side_credentials': 'NOT_RUN',
        'request_count': len(rows), 'reserved_upper_usd': manifest['full_plan_reserved_usd']}
    save(out/'preflight.json', status)
    return status


def offline(out):
    rows = []
    validator = PlanValidator()
    for case in dataset('normal_cases')[0]:
        bank = BankSandbox(out/'banks'/case['id'])
        with MockAdapter(fixture_proposal(case), out/'traces'/(case['id']+'.sqlite3'),
                         contract_version='3.0.0') as adapter:
            row, bundle = run_case(bank, adapter, case, approve_synthetic_payment=True)
            row['semantic_equivalent'] = bool(bundle and equivalent(TaskPlan.from_dict(bundle['record']['plan']),
                TaskPlan.from_dict(case['expected_plan']),request_for(case).context,validator))
            row['verified_goal_completion'] = row['semantic_equivalent'] and row['execution_alignment'] is True
            rows.append(row)
            save(out/'cases'/(case['id']+'.json'), {'result': row, 'evidence': bundle})
    result = {'source': 'GOLD_DERIVED_LOCAL_HTTP_FIXTURE', 'real_model_calls': 0, 'rows': rows,
        'normal_cases': len(rows), 'strict_gold_matches': sum(r['planner_success'] for r in rows),
        'semantic_matches': sum(r['semantic_equivalent'] for r in rows),
        'verified_goal_completions': sum(r['verified_goal_completion'] for r in rows),
        'unauthorized_executions': sum(r['unauthorized_execution'] for r in rows)}
    save(out/'results.json', result)
    return {k:v for k,v in result.items() if k!='rows'}


def historical_diagnostics(out):
    """Read actual saved observations, do not relabel them as new calls."""
    root = Path('data/llm-final-live-acceptance')
    counts = Counter()
    rows = []
    for path in sorted((root/'responses').glob('*.json')):
        body = json.loads(path.read_text(encoding='utf-8'))
        raw = body.get('provider_payload', {})
        for choice in raw.get('choices', []):
            content = choice.get('message', {}).get('content', '')
            try: candidate = json.loads(content)
            except (ValueError, TypeError): candidate = None
            echo = isinstance(candidate, dict) and 'properties' in candidate and 'steps' not in candidate
            reasoning = raw.get('usage', {}).get('completion_tokens_details', {}).get('reasoning_tokens')
            counts['schema_echo'] += echo
            counts['truncated'] += choice.get('finish_reason') == 'length'
            rows.append({'source_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'model': raw.get('model'), 'schema_echo': echo, 'finish_reason': choice.get('finish_reason'),
                'reasoning_tokens': reasoning, 'usage': raw.get('usage')})
    save(out/'historical-diagnostics.json', {'real_model_calls':0, 'counts':dict(counts), 'rows':rows})
    return dict(counts)


class RevisionBudget(SemanticBudget):
    """Remaining unique calls debit the ORIGINAL immutable approved ledger."""
    def __init__(self, root, out):
        original = SemanticBudget(root)
        self.path = original.path
        pin=json.loads((out/'manifest-pin.json').read_text(encoding='utf-8'))['sha256']
        raw=(out/'manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pin: raise InferenceError('EVALUATION_MANIFEST_CHANGED')
        manifest=json.loads(raw)
        self.allowed={r['request_id']:r for r in manifest['requests']}
        if not 1<=len(self.allowed)<=117: raise InferenceError('EVALUATION_MANIFEST_INVALID')
        # Explicit same logical request identifiers. Original pilot IDs cannot
        # exist in this manifest, and durable duplicate prevention stays active.
        for key,row in self.allowed.items():
            old=original.allowed.get(key)
            if not old or old['case_hash']!=row['case_hash'] or row['case_id']=='balance01':
                raise InferenceError('EVALUATION_REQUEST_CHANGED')
        with closing(self._db()) as db:
            count,cost=db.execute('SELECT count(*),coalesce(sum(microusd),0) FROM reservations').fetchone()
        unclaimed = sum(r['reserved_microusd'] for k,r in self.allowed.items()
            if not self._claimed(k))
        if cost+unclaimed>3000000 or count>120:
            raise InferenceError('EVALUATION_FULL_PLAN_UNFUNDED')

    def _claimed(self, key):
        with closing(self._db()) as db:
            return db.execute('SELECT 1 FROM reservations WHERE request_id=?',(key,)).fetchone()

    reserve=SemanticBudget.reserve


class FinalFillBudget(SemanticBudget):
    """New explicit approval for the missing rows of the LOCKED v3.2 cohort.

    Never debits, expands or replaces the exhausted pilot ledger.
    """
    def __init__(self, out):
        pin=json.loads((out/'manifest-pin.json').read_text(encoding='utf-8'))['sha256']
        raw=(out/'manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pin: raise InferenceError('EVALUATION_MANIFEST_CHANGED')
        manifest=json.loads(raw)
        auth=json.loads((out/'authorization.json').read_text(encoding='utf-8'))
        expected={'approved':True,'max_requests':44,'max_usd':1.2,'manifest_sha256':pin,'retries':0,'fallbacks':0}
        if any(auth.get(k)!=v or type(auth.get(k)) is not type(v) for k,v in expected.items()):
            raise InferenceError('LLM_BUDGET_APPROVAL_REQUIRED')
        self.allowed={r['request_id']:r for r in manifest['requests']}
        if len(self.allowed)!=44 or manifest['full_plan_reserved_usd']>1.2:
            raise InferenceError('EVALUATION_FULL_PLAN_UNFUNDED')
        self.path=str((out/'budget.sqlite3').resolve())
        with closing(self._db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), max_requests INTEGER, max_microusd INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (request_id TEXT PRIMARY KEY, provider TEXT, microusd INTEGER, request_hash TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS approval (id INTEGER PRIMARY KEY CHECK(id=1), manifest_hash TEXT, approval_hash TEXT)')
            db.execute('INSERT OR IGNORE INTO budget VALUES(1,44,1200000)')
            db.execute('INSERT OR IGNORE INTO approval VALUES(1,?,?)',(pin,digest(auth)))
            if db.execute('SELECT max_requests,max_microusd FROM budget').fetchone()!=(44,1200000) or db.execute('SELECT manifest_hash,approval_hash FROM approval').fetchone()!=(pin,digest(auth)):
                raise InferenceError('LLM_BUDGET_IMMUTABLE')


def live(out, budget_root=None, remaining=False, final_fill=False):
    # Recompute the entire manifest before sending, including template/config
    # hashes. write_once refuses any change even on a restart.
    revision=budget_root is not None
    # Only recompute an existing remaining manifest when no paid requests were
    # sent: its claimed-ID projection otherwise changes. Verify pin/configs
    # before resumed dispatch instead of regenerating a moving cohort.
    if remaining and (out/'manifest.json').exists():
        raise InferenceError('EVALUATION_REMAINING_RUN_REQUIRES_RECONCILIATION')
    preflight(out, revision=revision, budget_root=budget_root if remaining else None, final_fill=final_fill)
    budget=FinalFillBudget(out) if final_fill else RevisionBudget(budget_root,out) if revision else SemanticBudget(out)
    credentials=refresh_credentials(s.providers[0].api_key_env for s in profiles().values())
    if not all(s['recognized'] for s in credentials.values()):
        raise InferenceError('EVALUATION_CREDENTIAL_MISSING')
    adapters={p:CompatibleAdapter(s.providers[0],out/'live-traces'/(p+'.sqlite3'),budget=budget,
        observation_dir=out/'live-responses',contract_version='3.2.0' if remaining or final_fill else '3.1.0' if revision else '3.0.0') for p,s in profiles().items()}
    cases=[c for name in ('normal_cases','attack_cases') for c in dataset(name)[0]]
    if revision: cases=[c for c in cases if c['id']!='balance01']
    rows=[]; gates={}; validator=PlanValidator()
    for case in cases:
        for provider,adapter in adapters.items():
            path=out/'live-cases'/(provider+'-'+case['id']+'.json')
            request=request_for(case,provider)
            if request.request_id not in budget.allowed: continue
            if path.exists():
                row=json.loads(path.read_text(encoding='utf-8'))['result']
            elif provider in gates and not gates[provider]:
                row={'case_id':case['id'],'provider':provider,'attack':bool(case.get('attack')),
                     'status':'NOT_RUN','failure':'CONNECTIVITY_GATE_FAILED',
                     'schema_valid':False,'validation_result':'NOT_RUN','planner_success':False,
                     'semantic_equivalent':False,'verified_goal_completion':False,
                     'execution_alignment':None,'unauthorized_execution':False}
                save(path,{'result':row,'evidence':None})
            else:
                # Never repeat an already reserved request after a crash. An
                # uncertain dispatch remains failed/consumed, not a free retry.
                with closing(budget._db()) as db:
                    reserved=db.execute('SELECT 1 FROM reservations WHERE request_id=?',(request.request_id,)).fetchone()
                if reserved:
                    raise InferenceError('EVALUATION_RESERVED_REQUEST_NEEDS_RECONCILIATION')
                bank=BankSandbox(out/'live-banks'/(provider+'-'+case['id']))
                row,bundle=run_case(bank,adapter,case,approve_synthetic_payment=True)
                semantic=False
                if bundle and bundle['record'].get('plan') and not case.get('attack'):
                    semantic=equivalent(TaskPlan.from_dict(bundle['record']['plan']),
                        TaskPlan.from_dict(case['expected_plan']),request.context,validator)
                events=[e for e in adapter.trace.events() if e['request_id']==request.request_id and e['status']!='RUNNING']
                row.update(status='RUN',schema_valid=bool(events and events[-1]['validation_result']['schema']=='PASS'),
                    semantic_equivalent=semantic,verified_goal_completion=semantic and row['execution_alignment'] is True)
                save(path,{'result':row,'evidence':bundle})
            if provider not in gates: gates[provider]=row['schema_valid']
            rows.append(row)
            normal=[r for r in rows if not r.get('attack')]
            summary={'scope':'LOCAL_VIRTUAL_BANK_ONLY','rows':rows,'budget':budget.status(),
                'real_model_calls':budget.status()['requests'], 'planned_cases':len(budget.allowed),
                'cohort':'locked version3.2 final fill' if final_fill else 'remaining unique cases version3.2' if remaining else 'post-pilot version3.1, pilot not repeated' if revision else 'version3.0 pilot',
                'unauthorized_executions':sum(r['unauthorized_execution'] for r in rows),
                'strict_gold_matches':sum(r['planner_success'] for r in normal),
                'semantic_matches':sum(r['semantic_equivalent'] for r in normal),
                'verified_goal_completions':sum(r['verified_goal_completion'] for r in normal)}
            save(out/'live-results.json',summary)
            print(provider,case['id'],row.get('failure'),flush=True)
            if row['unauthorized_execution']:
                raise InferenceError('EVALUATION_UNAUTHORIZED_EXECUTION')
    return {k:v for k,v in summary.items() if k!='rows'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preflight','offline','history','live'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-root', type=Path)
    parser.add_argument('--remaining', action='store_true')
    parser.add_argument('--final-fill', action='store_true')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    if args.mode=='live': result=live(args.output,args.budget_root,args.remaining,args.final_fill)
    elif args.mode=='preflight': result=preflight(args.output,final_fill=args.final_fill)
    else: result={'offline':offline,'history':historical_diagnostics}[args.mode](args.output)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__': main()
