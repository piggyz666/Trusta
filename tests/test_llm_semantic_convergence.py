"""New v3 contract: offline inference fixtures, actual bank/runtime/audit."""
from copy import deepcopy
import json
import pytest
from backend.llm.contracts import InferenceError
from llm.planner.semantic import catalog, compile_proposal
from llm_evaluation.run import dataset
from llm_evaluation.evaluator import request_for, run_case
from llm_evaluation.adapters.mock_adapter import MockAdapter
from runtime.task_graph import TaskPlan
from validator import PlanValidator
from evaluation.llm.semantic import equivalent
from sandbox import BankSandbox
from llm.contracts import ProtocolProviderConfig, LLMModelConfig
from llm.providers import create_provider


def proposal(case):
    specs = PlanValidator().specifications()
    calls = []
    positions = {n['node_id']: i for i,n in enumerate(case['expected_plan']['steps'])}
    for n in case['expected_plan']['steps']:
        calls.append({'agent': n['agent'], 'action': specs[(n['agent'], n['action'])].permission_action,
            'parameters': {k: v if v.startswith('input:') else 'call:'+str(positions[v[:-7]])
                           for k,v in n['parameters'].items()}})
    return {'version':3, 'calls':calls, 'clarification':None}


NORMAL = dataset('normal_cases')[0]


@pytest.mark.parametrize('case', NORMAL, ids=lambda c:c['id'])
def test_semantic_bank_e2e(case, tmp_path):
    bank = BankSandbox(tmp_path / 'bank')
    with MockAdapter(proposal(case), tmp_path/'trace.sqlite3', contract_version='3.0.0') as adapter:
        row, bundle = run_case(bank, adapter, case, approve_synthetic_payment=True)
    assert row['validation_result'] == 'PASS'
    actual = TaskPlan.from_dict(bundle['record']['plan'])
    assert equivalent(actual, TaskPlan.from_dict(case['expected_plan']), request_for(case).context, PlanValidator())
    assert row['audit_verified'] and row['execution_alignment']
    assert not row['unauthorized_execution']
    assert bundle['scope'] == 'LOCAL_VIRTUAL_BANK_ONLY'


@pytest.mark.parametrize('mutation,code', [
    ('literal','LLM_OUTPUT_SCHEMA'), ('unknown_action','LLM_OUTPUT_SCHEMA'),
    ('extra_authority','LLM_OUTPUT_SCHEMA'), ('forward','LLM_PLAN_FORWARD_REFERENCE'),
    ('missing','LLM_PLAN_MISSING_INPUT'), ('duplicate','LLM_PLAN_REDUNDANT_CALL'),
    ('conflict','LLM_PLAN_CLARIFICATION_CONFLICT'), ('schema_echo','LLM_OUTPUT_SCHEMA')])
def test_proposal_rejection(mutation, code):
    c = NORMAL[0]; p = proposal(c)
    if mutation=='literal': p['calls'][0]['parameters']['account_id']=123
    if mutation=='unknown_action': p['calls'][0]['action']='transfer.execute'
    if mutation=='extra_authority': p['calls'][0]['approved']=True
    if mutation=='forward': p['calls'][0]['parameters']['account_id']='call:0'
    if mutation=='missing': p['calls'][0]['parameters']['account_id']='input:other'
    if mutation=='duplicate': p['calls']*=2
    if mutation=='conflict': p['clarification']='MISSING_INPUT'
    if mutation=='schema_echo': p={'type':'object','properties':{}}
    with pytest.raises(InferenceError) as error:
        compile_proposal(p,request_for(c),PlanValidator())
    assert error.value.code == code


def test_catalog_has_one_alias_per_capability():
    validator=PlanValidator(); context=request_for(NORMAL[0]).context
    assert len(catalog(validator,context))==1


def test_equivalence_ignores_ids_and_independent_order():
    c=deepcopy(NORMAL[0]); c['grants'].append(['wealth','wealth.recommend'])
    p=c['expected_plan']; p['steps'].append({'node_id':'Wealth','agent':'wealth','action':'wealth.recommend','parameters':{},'dependency':[]})
    q=deepcopy(p); q['steps'].reverse(); q['steps'][1]['node_id']='Other'
    assert equivalent(TaskPlan.from_dict(p),TaskPlan.from_dict(q),request_for(c).context,PlanValidator())
    q['steps'].pop()
    assert not equivalent(TaskPlan.from_dict(p),TaskPlan.from_dict(q),request_for(c).context,PlanValidator())


@pytest.mark.parametrize('authentication', ['bearer','api-key'])
def test_declared_auth_transport(authentication, monkeypatch):
    from backend.llm.providers.http_adapter import HTTPAdapter
    observed={}
    def send(self,outbound,request):
        from backend.llm.contracts import ProviderResponse
        observed.update(headers=dict(outbound.header_items()),body=json.loads(outbound.data))
        return ProviderResponse('{}','fixture',{})
    monkeypatch.setattr(HTTPAdapter,'_send',send)
    monkeypatch.setenv('TEST_MODEL_KEY','fixture-credential')
    config=ProtocolProviderConfig('profile',kind='openai_compatible',model='fixture',
        endpoint='http://127.0.0.1:99/',api_key_env='TEST_MODEL_KEY',authentication=authentication,
        token_parameter='max_completion_tokens',thinking_mode='disabled')
    create_provider('openai_compatible').generate([{'role':'user','content':'test'}],None,(),LLMModelConfig(config))
    assert observed['body']['thinking']=={'type':'disabled'}
    assert observed['body']['max_completion_tokens']==512 and 'max_tokens' not in observed['body']
    headers={k.lower():v for k,v in observed['headers'].items()}
    assert ('authorization' in headers)==(authentication=='bearer')
    assert ('api-key' in headers)==(authentication=='api-key')


def test_authentication_rejects_arbitrary_header():
    with pytest.raises(InferenceError):
        ProtocolProviderConfig('bad',authentication='Cookie').validate()


def test_user_credentials_refresh_without_overwrite(monkeypatch):
    from llm import credentials
    monkeypatch.delenv('TEST_MODEL_KEY',raising=False)
    monkeypatch.setattr(credentials,'_windows_user_value',lambda name:'offline-secret')
    assert credentials.refresh_credentials(['TEST_MODEL_KEY'])['TEST_MODEL_KEY']=={'recognized':True,'source':'windows_user'}
    monkeypatch.setenv('TEST_MODEL_KEY','process-secret')
    assert credentials.refresh_credentials(['TEST_MODEL_KEY'])['TEST_MODEL_KEY']['source']=='process'
    import os
    assert os.getenv('TEST_MODEL_KEY')=='process-secret'


def test_credentials_invalid_reference_rejected():
    from llm.credentials import refresh_credentials
    with pytest.raises(InferenceError):
        refresh_credentials(['../../Environment'])


@pytest.mark.parametrize('provider',['deepseek','kimi','mimo'])
def test_profiles_share_capacity_and_policy(provider):
    from llm.configuration import load_settings
    settings=load_settings('config/llm.'+provider+'.semantic-v3.yaml')
    assert settings.max_output_tokens==2048 and settings.max_fallbacks==0
    assert settings.providers[0].thinking_mode=='disabled'
    assert settings.providers[0].max_sensitivity=='restricted'


def test_revision_uses_shared_ledger_and_duplicate_guard(tmp_path):
    from llm_evaluation.semantic_convergence import RevisionBudget
    from llm_evaluation.budget import LiveBudget
    from llm.hashing import digest
    messages=[{'role':'user','content':'synthetic'}]
    path=tmp_path/'budget.sqlite3'
    LiveBudget(path,approved=True)
    budget=object.__new__(RevisionBudget)
    budget.path=str(path)
    budget.allowed={'r':{'provider':'test','request_hash':digest(messages)}}
    budget.reserve('r','test',messages,2048)
    with pytest.raises(InferenceError) as failure:
        budget.reserve('r','test',messages,2048)
    assert failure.value.code=='LLM_BUDGET_DUPLICATE'
    with pytest.raises(InferenceError): budget.reserve('other','test',messages,2048)
    assert budget.status()['requests']==1


@pytest.mark.parametrize('version',['3.1.0','3.2.0'])
def test_version31_input_shape_never_discloses_values(tmp_path, version):
    from llm.planner.semantic import input_descriptions
    c=NORMAL[0]
    descriptions=input_descriptions(request_for(c).context)
    assert descriptions['input:account']['field_name']=='account_id'
    assert c['inputs']['input:account'] not in json.dumps(descriptions)
    with MockAdapter(proposal(c),tmp_path/'trace.sqlite3',contract_version=version) as adapter:
        validated=adapter.generate(request_for(c,adapter.config.name))
    assert validated.plan.steps[0].parameters['account_id']=='input:account'


def test_contextual_gateway_uses_shared_contract_and_native_runtime(tmp_path):
    from tests.phase6_helpers import service
    from backend.llm.configuration import LLMSettings, ProviderConfig
    from backend.llm.gateway import LLMGateway
    from backend.llm.contracts import ProviderResponse
    s=service(str(tmp_path))
    class ExplicitOfflineProvider:
        def infer(self,request):
            assert 'parameters (OBJECT' in request.messages[0]['content']
            return ProviderResponse(json.dumps(proposal(NORMAL[0])), 'offline-fixture', {})
    settings=LLMSettings(mode='assisted',graph_format='semantic_v3',max_fallbacks=0,
        providers=(ProviderConfig('fixture',max_sensitivity='restricted'),))
    gateway=LLMGateway(settings,s.db,s.audit,providers={'fixture':ExplicitOfflineProvider()})
    tid=s.supervisor.receive('demo','balance',{'task_type':'GENERAL_GRAPH'})
    task=s._owned_task(tid,'demo')
    result=gateway.propose_graph(s.registry,task,'balance',{'input:account':'acct-demo'})
    assert result.status=='SUCCEEDED'
    executed=s.graphs.create('demo','balance',result.output,{'input:account':'acct-demo'})
    assert executed['status']=='COMPLETED'
    assert s.db.rows('SELECT * FROM transfers')==[]


@pytest.mark.parametrize('mutation',['amount_literal','execution_action','wrong_input'])
def test_native_semantic_adapter_rejects_authority_and_unbound_input(mutation):
    from backend.llm.semantic_adapter import normalize
    from backend.security.registry import CapabilityRegistry
    from backend.domain import Rejected
    case=NORMAL[0]; candidate=proposal(case)
    if mutation=='amount_literal': candidate['calls'][0]['parameters']['account_id']=100
    if mutation=='execution_action': candidate['calls'][0]['action']='transfer.execute'
    if mutation=='wrong_input': candidate['calls'][0]['parameters']['account_id']='input:other'
    with pytest.raises(Rejected):
        normalize(candidate,CapabilityRegistry(banking_version=5),case['inputs'],case['id'],case['text'])


def test_native_scoped_input_descriptions_preserve_clause_binding():
    from llm.planner.semantic import input_descriptions
    from validator import PlanningContext
    context=PlanningContext('scope',{'input:s0_account_id':'private-account','input:s1_amount':100},
        [('transaction','account.get_balance')])
    d=input_descriptions(context)
    assert d['input:s0_account_id']['field_name']=='account_id'
    assert d['input:s1_amount']['field_name']=='amount' and d['input:s1_amount']['clause_index']==1
    assert 'private-account' not in json.dumps(d)


def test_read_after_transfer_is_fresh_and_verified(tmp_path):
    transfer=deepcopy(next(c for c in NORMAL if c['id']=='transfer02'))
    transfer['id']='read_after_transfer'; transfer['expected_plan']['task_id']=transfer['id']
    transfer['grants'].append(['transaction','account.get_balance'])
    steps=transfer['expected_plan']['steps']
    # Keep the trusted original transfer Gold, plus explicit before/after goals.
    before={'node_id':'Before','agent':'transaction','action':'account.get_balance',
        'parameters':{'account_id':'input:account'},'dependency':[]}
    after=deepcopy(before); after['node_id']='After'; after['dependency']=[steps[-1]['node_id']]
    steps[0]['dependency']=['Before']
    transfer['expected_plan']['steps']=[before]+steps+[after]
    transfer['text']='Check balance, prepare the requested transfer, then check balance again'
    bank=BankSandbox(tmp_path/'bank')
    with MockAdapter(proposal(transfer),tmp_path/'trace.sqlite3',contract_version='3.2.0') as adapter:
        row,bundle=run_case(bank,adapter,transfer,approve_synthetic_payment=True)
    assert row['planner_success'] and row['execution_alignment']
    assert len(bundle['record']['execution']['receipts'])==1


def test_intrinsic_wealth_reads_are_equivalent_without_rewriting_gold():
    c=next(c for c in NORMAL if c['id']=='products02')
    p=TaskPlan.from_dict(c['expected_plan'])
    q=TaskPlan.from_dict({'task_id':c['id'],'goal':c['text'],'steps':[
        {'node_id':'Consult','agent':'wealth','action':'wealth.recommend','parameters':{},'dependency':[]}]})
    assert equivalent(p,q,request_for(c).context,PlanValidator())
    # A stand-alone requested profile query has no delegated consumer: keep it.
    separate=TaskPlan.from_dict({'task_id':c['id'],'goal':c['text'],'steps':[
        {'node_id':'Profile','agent':'wealth','action':'customer.get_risk_profile','parameters':{},'dependency':[]}]+q.as_dict()['steps']})
    assert not equivalent(separate,q,request_for(c).context,PlanValidator())


def test_native_grounded_gate_accepts_only_intrinsic_read_contraction(tmp_path):
    from tests.phase6_helpers import service
    from backend.llm.configuration import LLMSettings, ProviderConfig
    from backend.llm.gateway import LLMGateway
    from backend.llm.contracts import ProviderResponse
    from backend.llm.grounded_request import GroundedRequestPlanner
    s=service(str(tmp_path))
    class ExplicitOfflineProvider:
        def infer(self,request):
            output={'version':3,'clarification':None,'calls':[
                {'agent':'wealth','action':action,'parameters':{}} for action in
                ('customer.get_risk_profile','wealth.list_products','wealth.recommend')]}
            return ProviderResponse(json.dumps(output),'offline-fixture',{})
    settings=LLMSettings(mode='assisted',graph_format='semantic_v3',max_fallbacks=0,
        providers=(ProviderConfig('fixture',max_sensitivity='restricted'),))
    s.llm=LLMGateway(settings,s.db,s.audit,providers={'fixture':ExplicitOfflineProvider()})
    task=GroundedRequestPlanner(s).propose('demo','推荐适合我的理财产品','acct-demo')
    assert task['status']=='COMPLETED'
    assert s.db.rows('SELECT * FROM transfers')==[]


def test_result_reference_spelling_cannot_hide_duplicate_calls():
    c=next(c for c in NORMAL if c['id']=='analysis01')
    p=proposal(c); duplicate=deepcopy(p['calls'][1]);duplicate['parameters']['records']='call:00'
    p['calls'].append(duplicate)
    with pytest.raises(InferenceError) as failure:
        compile_proposal(p,request_for(c),PlanValidator())
    assert failure.value.code=='LLM_PLAN_REDUNDANT_CALL'
