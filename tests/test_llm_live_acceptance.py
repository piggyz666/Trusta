"""Acceptance bookkeeping only; synthetic unit fixtures are never live scores."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.llm.contracts import InferenceError
from llm.hashing import digest, snapshot
from llm_evaluation.run import dataset
from llm_evaluation.final_live import AcceptanceBudget, observation_metrics, ratio, summarize_rows, attack_case
from llm_evaluation.adapters.mock_adapter import MockAdapter
from benchmarks.glm_evaluation import configuration
from sandbox import BankSandbox


@pytest.fixture
def frozen(tmp_path):
    messages = ({'role': 'user', 'content': 'synthetic fixture'},)
    cost = (len(snapshot(messages).encode()) + 4096) * 2 + 2048 * 4
    manifest = {'scope': 'LOCAL_VIRTUAL_BANK_ONLY', 'providers': {p: {} for p in ('deepseek','glm','gemini')},
        'datasets': {n: dataset(n)[1] for n in ('normal_cases','attack_cases')},
        'budget': {'full_plan_reservation_usd': cost*120/1000000},
        'requests': [{'request_id': 'deepseek:case'+str(i), 'provider': 'deepseek',
                      'prompt_hash': digest(messages), 'reserved_microusd': cost} for i in range(120)]}
    body = json.dumps(manifest).encode()
    (tmp_path / 'manifest.json').write_bytes(body)
    pin = hashlib.sha256(body).hexdigest()
    (tmp_path / 'manifest-pin.json').write_text(json.dumps({'sha256':pin}))
    auth = {'manifest_sha256':pin,'max_requests':120,'max_usd':3,'approved':True,'retries':0,'fallbacks':0}
    (tmp_path / 'authorization.json').write_text(json.dumps(auth))
    return tmp_path, messages, auth


def test_reservation_new_ledger_exact_body_and_no_duplicate(frozen):
    path, messages, _ = frozen
    budget = AcceptanceBudget(path)
    budget.reserve('deepseek:case0','deepseek',messages,2048)
    with pytest.raises(InferenceError, match='LLM_BUDGET_DUPLICATE'):
        budget.reserve('deepseek:case0','deepseek',messages,2048)
    assert budget.status()['requests'] == 1
    assert AcceptanceBudget(path).status()['requests'] == 1


@pytest.mark.parametrize('field,value', [('max_requests',121),('max_usd',4),('approved',False),
                                        ('retries',1),('fallbacks',1),('approved',1)])
def test_authorization_cannot_expand(frozen,field,value):
    path, _, auth = frozen
    auth[field] = value
    (path/'authorization.json').write_text(json.dumps(auth))
    with pytest.raises(InferenceError, match='LLM_BUDGET_APPROVAL_REQUIRED'):
        AcceptanceBudget(path)


@pytest.mark.parametrize('change', ['provider','tokens','messages','schema','id'])
def test_dispatch_rejects_changed_request(frozen,change):
    path, messages, _ = frozen
    values = dict(request_id='deepseek:case0',provider='deepseek',messages=messages,output_tokens=2048,schema=None)
    values[{'provider':'provider','tokens':'output_tokens','messages':'messages','schema':'schema','id':'request_id'}[change]] = {
        'provider':'gemini','tokens':4096,'messages':({'role':'user','content':'different'},),'schema':{},'id':'unknown'}[change]
    with pytest.raises(InferenceError, match='EVALUATION_REQUEST_CHANGED'):
        AcceptanceBudget(path).reserve(**values)


def test_manifest_tamper_rejected_before_budget_creation(frozen):
    path, _, _ = frozen
    with (path/'manifest.json').open('ab') as f:
        f.write(b' ')
    with pytest.raises(InferenceError, match='EVALUATION_MANIFEST_CHANGED'):
        AcceptanceBudget(path)
    assert not (path/'budget.sqlite3').exists()


def test_concurrent_unique_reservations_durable(frozen):
    path, messages, _ = frozen
    budget = AcceptanceBudget(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: budget.reserve('deepseek:case'+str(i),'deepseek',messages,2048), range(12)))
    assert AcceptanceBudget(path).status()['requests'] == 12


def test_protocol_success_not_schema_or_task_success():
    cfg = configuration('glm')
    payload = {'choices':[{'message':{'content':'{"not_a_plan":true}'},'finish_reason':'stop'}]}
    row = observation_metrics({'provider_payload':payload},cfg)
    assert row['provider_compatible'] is True
    assert row['schema_valid'] is False


def test_missing_response_never_passes_metrics():
    row = observation_metrics({},configuration('gemini'))
    assert row['provider_compatible'] is None
    assert row['schema_valid'] is None
    assert ratio([True,False,None],3) == {'numerator':1,'denominator':3,'not_evaluable':1,'rate':1/3}


def test_empty_results_keep_full_denominator():
    metrics = summarize_rows([])
    assert metrics['provider_compatibility']['denominator'] == 40
    assert metrics['verified_task_completion']['denominator'] == 20
    assert metrics['execution_alignment']['rate'] is None


def test_attack_valid_proposal_never_controller_approved(tmp_path):
    case = dataset('attack_cases')[0][-1]
    bank = BankSandbox(tmp_path/'bank')
    with MockAdapter(case['expected_plan'],tmp_path/'trace.sqlite3') as adapter:
        row, bundle = attack_case(bank,adapter,case)
    assert row['terminal_status'] == 'WAITING_USER'
    assert row['planner_success'] is None
    assert row['unauthorized_execution'] is False
    assert not bundle['record']['execution']['receipts']
