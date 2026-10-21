"""Real native VirtualBank execution; only inference is an explicit HTTP fixture."""
import copy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.domain import digest
from backend.llm.contracts import InferenceError
from sandbox import BankSandbox
from sandbox.permission import SandboxPermissions
from llm_evaluation.adapters.mock_adapter import MockAdapter
from llm_evaluation.evaluator import run_case, request_for
from llm_evaluation.budget import LiveBudget
from alignment import verify_execution
from alignment.matcher import match_execution

ROOT = Path(__file__).resolve().parents[1]
NORMAL = json.loads((ROOT / 'evaluation_dataset/normal_cases.json').read_text(encoding='utf-8'))
ATTACK = json.loads((ROOT / 'evaluation_dataset/attack_cases.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', NORMAL, ids=lambda c: c['id'])
def test_normal_native_execution(tmp_path, case):
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(case['expected_plan'], tmp_path / 'trace.sqlite3') as adapter:
        row, proof = run_case(bank, adapter, case, approve_synthetic_payment=True)
    assert row['planner_success'] and row['execution_alignment'] and row['audit_verified']
    assert row['accuracy'] == 1 and not row['unauthorized_execution']
    assert bank.service.gateway._adapter.external is False
    assert proof['record']['execution']['status'] == 'COMPLETED'
    if case.get('approved_transfer'):
        receipt = proof['record']['execution']['receipts'][0]
        action = proof['record']['execution']['actions'][0]['action']
        assert receipt['approved_action_hash'] == digest(action)
        assert bank.service.transaction.balance('demo', 'acct-demo')['balance'] == 1000000 - action['amount']


@pytest.mark.parametrize('case', ATTACK, ids=lambda c: c['id'])
def test_attack_blocks_native_payment(tmp_path, case):
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(case.get('raw_output', case['expected_plan']), tmp_path / 'trace.sqlite3') as adapter:
        request = request_for(case, adapter.config.name)
        record = bank.prepare(adapter, request)
        if record['status'] == 'VALIDATED':
            record = bank.start(record['execution_id'], 'demo')
        if case['attack_kind'] == 'replay':
            assert record['status'] == 'WAITING_USER'
            with pytest.raises(InferenceError, match='LLM_DUPLICATE_REQUEST'):
                bank.prepare(adapter, request)
            assert len(adapter.trace.events()) == 2
        else:
            assert record['status'] in {'REJECTED', 'BLOCKED'}
    assert not record.get('execution', {}).get('receipts')
    assert bank.service.transaction.balance('demo', 'acct-demo')['balance'] == 1000000


@pytest.fixture(scope='module')
def completed_proof(tmp_path_factory):
    path = tmp_path_factory.mktemp('alignment-proof')
    bank = BankSandbox(path / 'bank')
    with MockAdapter(NORMAL[12]['expected_plan'], path / 'trace.sqlite3') as adapter:
        row, proof = run_case(bank, adapter, NORMAL[12], approve_synthetic_payment=True)
    assert row['audit_verified']
    return proof, (bank.root / 'audit-public-key.pem').read_text(encoding='utf-8')


def check(proof, key):
    return verify_execution(proof, key, expected_plan_hash=proof['record']['plan_hash'],
                            expected_request_hash=proof['record']['request_hash'])


@pytest.mark.parametrize('field', ['plan', 'context', 'execution_hash', 'runtime_task_id', 'scope',
    'provider', 'model', 'llm_trace_tail', 'request_hash', 'plan_hash', 'execution_id',
    'agent_id', 'timestamp', 'status', 'result'])
def test_snapshot_tampering_rejected(completed_proof, field):
    original, key = completed_proof
    proof = copy.deepcopy(original)
    proof['record'][field] = 'tampered'
    assert not check(proof, key)


@pytest.mark.parametrize('change', ['receipt', 'receipt_removed', 'receipt_duplicate', 'leaf',
    'checkpoint', 'trace', 'wrong_pin', 'wrong_expected_plan', 'wrong_expected_request'])
def test_public_proof_negative(completed_proof, change):
    original, key = completed_proof
    proof = copy.deepcopy(original)
    if change == 'receipt': proof['receipt_proofs'][0]['receipt']['status'] = 'FAKE'
    elif change == 'receipt_removed': proof['receipt_proofs'] = []
    elif change == 'receipt_duplicate': proof['receipt_proofs'] *= 2
    elif change == 'leaf': proof['audit_proof']['event']['payload_hash'] = '0' * 64
    elif change == 'checkpoint': proof['audit_proof']['checkpoint']['root'] = '0' * 64
    elif change == 'trace': proof['record']['llm_events'][1]['status'] = 'RUNNING'
    elif change == 'wrong_pin': key = 'untrusted public key'
    elif change == 'wrong_expected_plan':
        assert not verify_execution(proof, key, expected_plan_hash='0' * 64, expected_request_hash=proof['record']['request_hash']); return
    elif change == 'wrong_expected_request':
        assert not verify_execution(proof, key, expected_plan_hash=proof['record']['plan_hash'], expected_request_hash='0' * 64); return
    assert not check(proof, key)


@pytest.mark.parametrize('change', ['amount', 'recipient', 'dependencies', 'owner', 'missing_step'])
def test_matcher_independent_of_audit(completed_proof, change):
    r = copy.deepcopy(completed_proof[0]['record'])
    if change == 'amount': r['execution']['steps'][0]['parameters']['amount'] += 1
    elif change == 'recipient': r['execution']['actions'][0]['action']['recipient_id'] = 'payee-li'
    elif change == 'dependencies': r['execution']['steps'][-1]['dependencies'] = []
    elif change == 'owner': r['execution']['steps'][0]['owner'] = 'wealth'
    elif change == 'missing_step': r['execution']['steps'].pop()
    r['execution_hash'] = digest(r['execution'])
    try: result = match_execution(r)['complete']
    except (KeyError, ValueError, TypeError): result = False
    assert not result


def waiting(bank, path):
    with MockAdapter(NORMAL[12]['expected_plan'], path) as adapter:
        r = bank.prepare(adapter, request_for(NORMAL[12], adapter.config.name))
    return bank.start(r['execution_id'], 'demo')


def approve(bank, record):
    task = bank.service._owned_task(record['runtime_task_id'], 'demo')
    node, state = next(iter(task['graph_state']['actions'].items()))
    return bank.payments.create_payment(record['execution_id'], 'demo', node, digest(state['action']), approve=True)


def test_restart_waiting_and_exact_approval(tmp_path):
    bank = BankSandbox(tmp_path / 'bank')
    r = waiting(bank, tmp_path / 'trace.sqlite3')
    assert r['status'] == 'WAITING_USER'
    assert not r['execution']['receipts']
    restarted = BankSandbox(tmp_path / 'bank')
    r = restarted.resume(r['execution_id'], 'demo')
    r = approve(restarted, r)
    assert r['status'] == 'COMPLETED' and len(r['execution']['receipts']) == 1
    with pytest.raises(InferenceError): approve(restarted, r)
    assert restarted.service.transaction.balance('demo', 'acct-demo')['balance'] == 999900


def test_approval_hash_and_cross_user_rejected(tmp_path):
    bank = BankSandbox(tmp_path / 'bank'); r = waiting(bank, tmp_path / 'trace.sqlite3')
    with pytest.raises(Exception): bank.payments.create_payment(r['execution_id'], 'demo', 'Propose', '0' * 64, approve=True)
    with pytest.raises(InferenceError): bank.get(r['execution_id'], 'other_user')
    with pytest.raises(InferenceError): bank.authorize(r['execution_id'], 'other_user', 'Propose', '0' * 64, True)
    assert bank.service.transaction.balance('demo', 'acct-demo')['balance'] == 1000000


def test_sql_storage_tampering(tmp_path):
    bank = BankSandbox(tmp_path / 'bank'); r = waiting(bank, tmp_path / 'trace.sqlite3')
    with bank.service.db.transaction() as db:
        db.execute('UPDATE alignment_runs SET status=? WHERE id=?', ('COMPLETED', r['execution_id']))
    with pytest.raises(InferenceError, match='STORAGE_TAMPERED'): bank.get(r['execution_id'], 'demo')


def test_concurrent_claim_only_one_dispatch(tmp_path):
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(NORMAL[0]['expected_plan'], tmp_path / 'trace.sqlite3') as adapter:
        request = request_for(NORMAL[0], adapter.config.name)
        def run(_):
            try: return bank.prepare(adapter, request)['status']
            except InferenceError as error: return error.code
        with ThreadPoolExecutor(2) as pool: results = list(pool.map(run, range(2)))
        assert sorted(results) == ['LLM_DUPLICATE_REQUEST', 'VALIDATED']
        assert len(adapter.trace.events()) == 2


def test_directory_guard(tmp_path):
    (tmp_path / 'production.sqlite3').write_text('existing')
    with pytest.raises(InferenceError, match='DIRECTORY_NOT_EMPTY'): BankSandbox(tmp_path)


def test_permissions_no_setter():
    p = SandboxPermissions()
    r = p.describe('transaction'); r['permissions'].append('payment.create')
    assert 'payment.create' not in p.describe('transaction')['permissions']


@pytest.mark.parametrize('limits', [{'approved':False}, {'approved':True, 'max_usd':2},
    {'approved':True, 'max_requests':61}, {'approved':True, 'max_usd':True}, {'approved':True, 'max_usd':float('nan')}])
def test_budget_invalid(tmp_path, limits):
    with pytest.raises(InferenceError): LiveBudget(tmp_path / 'budget.sqlite3', **limits)


def test_budget_durable_exhaustion_and_duplicate(tmp_path):
    p = tmp_path / 'budget.sqlite3'; budget = LiveBudget(p, approved=True, max_requests=2)
    budget.reserve('one', 'glm', [{'role':'user','content':'hello'}], 768)
    with pytest.raises(InferenceError, match='DUPLICATE'): budget.reserve('one','glm',[],768)
    restarted = LiveBudget(p, approved=True, max_requests=2)
    assert restarted.status()['requests'] == 1
    restarted.reserve('two','glm',[],768)
    with pytest.raises(InferenceError, match='EXHAUSTED'): restarted.reserve('three','glm',[],768)


def test_same_prompt_different_providers(tmp_path):
    from benchmarks.glm_evaluation import configuration
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    messages = []
    for provider in ('glm', 'deepseek', 'gemini'):
        a = CompatibleAdapter(configuration(provider), tmp_path / (provider + '.sqlite3'))
        messages.append(a.request_material(request_for(NORMAL[0], provider)))
    assert messages[0] == messages[1] == messages[2]


@pytest.mark.parametrize('output', [NORMAL[0]['expected_plan'], {'invalid':'schema'}])
def test_isolated_provider_worker_actual_http(tmp_path, output):
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    with MockAdapter(output, tmp_path / 'unused.sqlite3') as fixture:
        adapter = CompatibleAdapter(fixture.config, tmp_path / 'worker.sqlite3', observation_dir=tmp_path / 'responses')
        request = request_for(NORMAL[0], fixture.config.name)
        if 'invalid' in output:
            with pytest.raises(InferenceError, match='LLM_OUTPUT_SCHEMA'): adapter._isolated_generate(request)
        else:
            assert adapter._isolated_generate(request).plan.task_id == NORMAL[0]['id']
        assert adapter.trace.verify()
        observations = list((tmp_path / 'responses').glob('*.json'))
        assert len(observations) == 1
        payload = json.loads(observations[0].read_text(encoding='utf-8'))
        assert 'provider_payload' in payload and 'response' in payload


def test_interrupted_trace_closed_without_retry(tmp_path):
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    from benchmarks.glm_evaluation import configuration
    adapter = CompatibleAdapter(configuration('glm'), tmp_path / 'trace.sqlite3')
    adapter.trace.claim('interrupted', '1'*64, '2'*64, '3'*64)
    adapter.close_interrupted('interrupted')
    assert adapter.trace.verify() and not adapter.trace.unfinished()
    assert adapter.trace.events()[-1]['error_code'] == 'LLM_EVALUATION_INTERRUPTED'
    adapter.close_interrupted('interrupted')
    assert len(adapter.trace.events()) == 2


def test_stale_runtime_capture_cannot_regress_completion(tmp_path):
    bank = BankSandbox(tmp_path / 'bank'); r = waiting(bank, tmp_path / 'trace.sqlite3')
    stale = bank.service._owned_task(r['runtime_task_id'], 'demo')
    completed = approve(bank, r)
    assert completed['status'] == 'COMPLETED'
    captured = bank._capture(r, stale)
    assert captured['status'] == 'COMPLETED'
    assert len(captured['execution']['receipts']) == 1


def test_model_output_plan_binding_rejected(tmp_path):
    from dataclasses import replace
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(NORMAL[0]['expected_plan'], tmp_path / 'trace.sqlite3') as adapter:
        original = adapter.generate
        def corrupted(request):
            result = original(request)
            adapter.runtime.responses[request.request_id] = replace(
                adapter.runtime.responses[request.request_id], content='{"forged":true}')
            return result
        adapter.generate = corrupted
        r = bank.prepare(adapter, request_for(NORMAL[0], adapter.config.name))
    assert r['status'] == 'REJECTED' and r['result'] == 'LLM_ALIGNMENT_TRACE_BINDING'
    assert not r.get('runtime_task_id')


def hanging_worker(*args):
    import time
    time.sleep(60)


def test_process_deadline_kills_unresponsive_provider(tmp_path, monkeypatch):
    from llm_evaluation.adapters import worker
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    import time
    monkeypatch.setattr(worker, 'infer', hanging_worker)
    with MockAdapter(NORMAL[0]['expected_plan'], tmp_path / 'unused.sqlite3') as fixture:
        adapter = CompatibleAdapter(fixture.config, tmp_path / 'trace.sqlite3', timeout=.1)
        started = time.monotonic()
        with pytest.raises(InferenceError, match='LLM_TIMEOUT'):
            adapter._isolated_generate(request_for(NORMAL[0], fixture.config.name))
        assert time.monotonic() - started < 12
    assert adapter.trace.verify()


def test_extra_model_transfer_never_operator_approved(tmp_path):
    case = copy.deepcopy(NORMAL[12])
    output = copy.deepcopy(case['expected_plan'])
    duplicate = copy.deepcopy(output['steps'][0]); duplicate['node_id'] = 'Second'
    duplicate['dependency'] = ['Propose']; output['steps'].append(duplicate)
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(output, tmp_path / 'trace.sqlite3') as adapter:
        row, proof = run_case(bank, adapter, case, approve_synthetic_payment=True)
    assert not row['planner_success'] and row['failure'] == 'WAITING_USER'
    assert not proof['record']['execution']['receipts']
    assert bank.service.transaction.balance('demo','acct-demo')['balance'] == 1000000


@pytest.mark.parametrize('field', ['authorization','policy','contract','transaction','receipt'])
def test_execution_contract_causality(completed_proof, field):
    record = copy.deepcopy(completed_proof[0]['record'])
    record['execution']['actions'][0][field] = {}
    record['execution_hash'] = digest(record['execution'])
    try: aligned = match_execution(record)['complete']
    except (KeyError,TypeError,ValueError): aligned = False
    assert not aligned


def test_provider_credential_echo_is_not_persisted(tmp_path, monkeypatch):
    from dataclasses import replace
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    secret = 'synthetic-credential-for-negative-test'
    monkeypatch.setenv('TRUSTA_TEST_CREDENTIAL', secret)
    output = copy.deepcopy(NORMAL[0]['expected_plan']); output['goal'] = secret
    with MockAdapter(output, tmp_path / 'unused.sqlite3') as fixture:
        config = replace(fixture.config, api_key_env='TRUSTA_TEST_CREDENTIAL')
        adapter = CompatibleAdapter(config, tmp_path / 'trace.sqlite3', observation_dir=tmp_path / 'responses')
        with pytest.raises(InferenceError, match='LLM_SENSITIVE_RESPONSE'):
            adapter._isolated_generate(request_for(NORMAL[0], config.name))
    assert all(secret not in p.read_text(encoding='utf-8') for p in (tmp_path / 'responses').glob('*.json'))

