"""Protocol contracts and controlled execution; fixture results are not live scores."""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import pytest
import yaml
from backend.llm.contracts import InferenceError
from llm.contracts import LLMModelConfig, ProtocolProviderConfig
from llm.gateway import LLMGateway
from llm.configuration import load_settings
from llm.router import ModelRouter, ModelRoute, RoutingContext
from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
from llm_evaluation.evaluator import run_case
from llm_evaluation.run import dataset
from llm_evaluation.budget import LiveBudget
from llm_evaluation.convergence import describe
from sandbox import BankSandbox

MESSAGES = ({'role':'system','content':'Return JSON.'},{'role':'user','content':'Synthetic data'})
SCHEMA = {'type':'object','required':['amount'],'additionalProperties':False,
          'properties':{'amount':{'type':'integer','minimum':1,'maximum':9}}}


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.requests.append((self.path,body))
            payload = self.server.payload
            if self.path == '/capacity':
                budget = body.get('max_tokens',body.get('max_completion_tokens',0))
                if budget < 1024:
                    payload = {'choices':[{'message':{'content':''},'finish_reason':'length'}],
                               'usage':{'completion_tokens':budget,
                                        'completion_tokens_details':{'reasoning_tokens':budget}}}
            if self.path == '/unavailable':
                self.send_response(503)
                self.send_header('Content-Length','0')
                self.end_headers()
                return
            if self.path == '/slow':
                time.sleep(.25)
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass
    svc = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    svc.daemon_threads = True
    svc.payload = {'choices':[{'message':{'content':'{"amount":3}'},'finish_reason':'stop'}],
                   'usage':{'prompt_tokens':11,'completion_tokens':7,
                            'completion_tokens_details':{'reasoning_tokens':2}}}
    svc.requests = []
    t = threading.Thread(target=svc.serve_forever,daemon=True)
    t.start()
    yield svc
    svc.shutdown(); t.join(3); svc.server_close()


def config(server, **kwargs):
    return LLMModelConfig(ProtocolProviderConfig(name='fixture', kind='openai_compatible', model='fixture',
        endpoint=f'http://127.0.0.1:{server.server_port}/ok', **kwargs), 1, 2048)


@pytest.mark.parametrize('kind,token', [('openai','max_tokens'),('openai','max_completion_tokens'),
                                     ('openai_compatible','max_tokens'),('openai_compatible','max_completion_tokens')])
def test_declared_token_parameter_overrides_only_wire_field(server,kind,token):
    c = config(server)
    c = replace(c,provider=replace(c.provider,kind=kind,token_parameter=token))
    r = LLMGateway(c).generate(MESSAGES,SCHEMA)
    body = server.requests[0][1]
    assert body[token] == 2048
    assert ('max_tokens' in body)+('max_completion_tokens' in body) == 1
    assert body['messages'] == list(MESSAGES)
    assert r.usage == {'input_tokens':11,'output_tokens':7}
    assert r.diagnostics == {'completion_tokens':7,'reasoning_tokens':2,'non_reasoning_tokens':5}


@pytest.mark.parametrize('value',[None,True,[],{},'arbitrary','max_output_tokens'])
def test_invalid_wire_parameter_rejected(server,value):
    with pytest.raises(InferenceError, match='LLM_INVALID_CONFIG'):
        config(server,token_parameter=value).validate()
    assert not server.requests


def test_native_token_override_rejected(server):
    c = config(server)
    with pytest.raises(InferenceError, match='LLM_INVALID_CONFIG'):
        replace(c,provider=replace(c.provider,kind='gemini',token_parameter='max_tokens')).validate()


def test_output_capacity_fix_does_not_repair_or_retry_truncation(server):
    c = config(server)
    c = replace(c,provider=replace(c.provider,endpoint=f'http://127.0.0.1:{server.server_port}/capacity'))
    with pytest.raises(InferenceError, match='LLM_INCOMPLETE_OUTPUT') as failure:
        fallback=replace(config(server),provider=replace(config(server).provider,name='fallback'))
        LLMGateway(replace(c,max_output_tokens=768),[fallback]).generate(MESSAGES,SCHEMA)
    assert failure.value.retryable is False
    assert len(server.requests) == 1


def test_capacity_profile_and_reasoning_metadata(server):
    c = config(server)
    c = replace(c,provider=replace(c.provider,endpoint=f'http://127.0.0.1:{server.server_port}/capacity'))
    with pytest.raises(InferenceError) as failed:
        LLMGateway(replace(c,max_output_tokens=768)).generate(MESSAGES,SCHEMA)
    assert failed.value.code == 'LLM_INCOMPLETE_OUTPUT'
    assert failed.value.diagnostics['reasoning_tokens'] == 768
    assert failed.value.diagnostics['non_reasoning_tokens'] == 0
    assert len(server.requests) == 1
    assert json.loads(LLMGateway(c).generate(MESSAGES,SCHEMA).content) == {'amount':3}
    assert len(server.requests) == 2  # second independent, explicitly configured call


@pytest.mark.parametrize('tokens',[0,4097,True,1.5])
def test_output_budget_is_hard_bounded(server,tokens):
    with pytest.raises(InferenceError):
        LLMGateway(replace(config(server),max_output_tokens=tokens)).generate(MESSAGES,SCHEMA)
    assert not server.requests


@pytest.mark.parametrize('env',['','ABSENT_FINAL_CONVERGENCE_KEY'])
@pytest.mark.parametrize('domain',['external','private'])
def test_external_credentials_required_before_network(server,monkeypatch,env,domain):
    monkeypatch.delenv('ABSENT_FINAL_CONVERGENCE_KEY',raising=False)
    c = config(server,locality=domain,api_key_env=env)
    with pytest.raises(InferenceError,match='LLM_PROVIDER_CREDENTIALS'):
        LLMGateway(c).generate(MESSAGES,SCHEMA)
    assert not server.requests


def test_yaml_switch_preserves_runtime_and_native_contract(server,tmp_path,monkeypatch):
    monkeypatch.setenv('LOCAL_FINAL_KEY','public-fixture')
    for kind in ('openai','openai_compatible','gemini'):
        p = tmp_path/(kind+'.yaml')
        entry = dict(provider=kind,model='same-contract',endpoint=f'http://127.0.0.1:{server.server_port}/ok',
            api_key='${LOCAL_FINAL_KEY}',locality='local',gateway={'mode':'shadow','max_output_tokens':2048})
        if kind != 'gemini':
            entry['token_parameter']='max_completion_tokens'
            entry['structured_outputs']=True
        p.write_text(yaml.safe_dump(entry),encoding='utf-8')
        if kind == 'gemini':
            server.payload={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"amount":3}'}]}}],
                            'usageMetadata':{'promptTokenCount':11,'candidatesTokenCount':5,'thoughtsTokenCount':2}}
        else:
            server.payload={'choices':[{'finish_reason':'stop','message':{'content':'{"amount":3}'}}]}
        settings=load_settings(p)
        router=ModelRouter.from_config(p)
        selected=router.select(RoutingContext('bank_query'),MESSAGES,SCHEMA)
        r=LLMGateway.from_config(p).generate(MESSAGES,SCHEMA)
        assert json.loads(r.content) == {'amount':3}
        assert selected.configs[0].max_output_tokens == settings.max_output_tokens == 2048
        wire=server.requests[-1][1]
        if kind != 'gemini':
            assert wire['response_format']['json_schema']['schema'] == SCHEMA
            assert wire['messages'] == list(MESSAGES)
        else:
            assert wire['generationConfig']['maxOutputTokens'] == 2048


def test_disabled_example_and_policy_budget_fail_pre_dispatch(server,tmp_path):
    gateway=LLMGateway.from_config('config/llm-convergence.example.yaml')
    with pytest.raises(InferenceError,match='LLM_DISABLED'):
        gateway.generate(MESSAGES,SCHEMA)
    router=ModelRouter([ModelRoute(config(server))])
    for context in (RoutingContext('bank',sensitivity='restricted'),
                    RoutingContext('bank',max_cost_usd=0),
                    RoutingContext('bank',purpose='plan_proposal',security_level='enhanced')):
        with pytest.raises(InferenceError,match='LLM_NO_ELIGIBLE_PROVIDER'):
            router.select(context,MESSAGES,SCHEMA)
    assert not server.requests


def test_schema_echo_is_not_normalized_and_cannot_fallback(server):
    server.payload['choices'][0]['message']['content']=json.dumps(SCHEMA)
    fallback=replace(config(server),provider=replace(config(server).provider,name='fallback'))
    with pytest.raises(InferenceError,match='LLM_OUTPUT_SCHEMA'):
        LLMGateway(config(server),[fallback]).generate(MESSAGES,SCHEMA)
    assert len(server.requests) == 1


@pytest.mark.parametrize('payload',[[{}],{'choices':[]},{'choices':[{},{}]}])
def test_ambiguous_protocol_envelope_fails_closed(server,payload):
    server.payload=payload
    with pytest.raises(InferenceError,match='LLM_PROVIDER_PROTOCOL'):
        LLMGateway(config(server)).generate(MESSAGES,SCHEMA)


def test_native_tools_are_rejected_without_execution(server):
    server.payload['choices'][0]['message']['tool_calls']=[{'function':{'name':'bank.transfer'}}]
    with pytest.raises(InferenceError,match='LLM_TOOL_CALL_FORBIDDEN'):
        LLMGateway(config(server)).generate(MESSAGES,SCHEMA)


def test_schema_budget_counts_wire_copy_without_reopening_old_budget(tmp_path):
    a=LiveBudget(tmp_path/'a.sqlite3',approved=True)
    b=LiveBudget(tmp_path/'b.sqlite3',approved=True)
    small=a.reserve('r','p',MESSAGES,4096)
    extra=b.reserve('r','p',MESSAGES,4096,schema=SCHEMA)
    assert extra > small
    assert a.status()['requests'] == b.status()['requests'] == 1
    assert a.status()['reserved_usd'] <= 1
    with pytest.raises(InferenceError,match='LLM_BUDGET_IMMUTABLE'):
        LiveBudget(tmp_path/'a.sqlite3',approved=True,max_requests=59)


@pytest.mark.parametrize('kind',['openai','openai_compatible','gemini'])
@pytest.mark.parametrize('case_id',['balance01','transfer02'])
def test_frozen_business_same_execution_across_protocols(server,tmp_path,monkeypatch,kind,case_id):
    monkeypatch.setenv('LOCAL_FINAL_KEY','public-fixture')
    case=next(c for c in dataset('normal_cases')[0] if c['id']==case_id)
    text=json.dumps(case['expected_plan'])
    server.payload=({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':text}]}}]} if kind=='gemini'
                    else {'choices':[{'finish_reason':'stop','message':{'content':text}}]})
    path=tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(dict(provider=kind,model='explicit-protocol-fixture',
        endpoint=f'http://127.0.0.1:{server.server_port}/ok',api_key='${LOCAL_FINAL_KEY}',
        locality='local',gateway={'mode':'shadow','max_output_tokens':2048})),encoding='utf-8')
    cfg=load_settings(path)
    adapter=CompatibleAdapter(cfg.providers[0],tmp_path/'trace.sqlite3')
    # The unchanged runtime uses the enterprise configuration router directly.
    adapter.runtime.router=ModelRouter.from_config(path)
    bank=BankSandbox(tmp_path/'bank')
    row,proof=run_case(bank,adapter,case,approve_synthetic_payment=True)
    assert row['planner_success'] and row['audit_verified'] and row['execution_alignment']
    assert proof['record']['llm_response']['content'] == text
    assert adapter.trace.verify()
    assert bank.service.gateway._adapter.external is False


def test_diagnosis_keeps_truncation_and_schema_echo_separate():
    truncated={'choices':[{'finish_reason':'length','message':{'content':''}}],
        'usage':{'completion_tokens':768,'completion_tokens_details':{'reasoning_tokens':768}}}
    d=describe(truncated,'deepseek')
    assert d['provider_reached'] and not d['complete_response']
    assert d['diagnostics']['reasoning_tokens'] == 768
    from schemas import task_plan_schema
    echo={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(task_plan_schema())}}]}
    assert describe(echo,'glm')['schema_echo']
    assert describe(None,'gemini')['provider_reached'] is None


def test_equivalent_purpose_order_allows_only_transport_fallback(server):
    primary=config(server,purposes=('plan_proposal','finance_explanation'))
    primary=replace(primary,provider=replace(primary.provider,endpoint=f'http://127.0.0.1:{server.server_port}/unavailable'))
    fallback=replace(primary,provider=replace(primary.provider,name='fallback',
        endpoint=f'http://127.0.0.1:{server.server_port}/ok',priority=101,
        purposes=tuple(reversed(primary.provider.purposes))))
    router=ModelRouter([ModelRoute(primary),ModelRoute(fallback)],max_fallbacks=1)
    selected=router.select(RoutingContext('bank_query'),MESSAGES,SCHEMA)
    assert len(selected.configs) == 2
    r=LLMGateway(primary,[fallback]).generate(MESSAGES,SCHEMA)
    assert r.provider == 'fallback' and len(server.requests) == 2


@pytest.mark.parametrize('purposes', [('plan_proposal',),
                                    ('plan_proposal','finance_explanation','wealth_explanation')])
def test_purpose_subset_or_superset_still_rejected(server,purposes):
    primary=config(server,purposes=('plan_proposal','finance_explanation'))
    fallback=replace(primary,provider=replace(primary.provider,name='fallback',purposes=purposes,priority=101))
    with pytest.raises(InferenceError,match='LLM_FALLBACK_POLICY_MISMATCH'):
        LLMGateway(primary,[fallback])
    selected=ModelRouter([ModelRoute(primary),ModelRoute(fallback)],max_fallbacks=1).select(
        RoutingContext('bank_query'),MESSAGES,SCHEMA)
    assert len(selected.configs) == 1
    assert ('fallback','FALLBACK_TRUST_DOMAIN') in selected.rejected
    assert not server.requests


@pytest.mark.parametrize('field',['content','model'])
def test_local_provider_credential_echo_rejected_at_common_boundary(server,monkeypatch,field):
    secret='synthetic-local-auth-credential'
    monkeypatch.setenv('LOCAL_FINAL_KEY',secret)
    if field=='content':
        server.payload['choices'][0]['message']['content']=json.dumps({'answer':secret})
    else:
        server.payload['model']=secret
    primary=config(server,api_key_env='LOCAL_FINAL_KEY')
    fallback=replace(primary,provider=replace(primary.provider,name='fallback'))
    with pytest.raises(InferenceError,match='LLM_SENSITIVE_RESPONSE') as failed:
        LLMGateway(primary,[fallback]).generate(MESSAGES,None)
    assert secret not in str(failed.value)
    assert len(server.requests)==1


def test_tainted_model_metadata_redacted_without_weakening_content_guard(server,monkeypatch):
    secret='synthetic-local-auth-credential'
    monkeypatch.setenv('LOCAL_FINAL_KEY',secret)
    server.payload['model']='provider-'+secret+'-version'
    primary=config(server,api_key_env='LOCAL_FINAL_KEY')
    r=LLMGateway(primary).generate(MESSAGES,SCHEMA)
    assert secret not in json.dumps(r.__dict__)
    assert r.model == primary.provider.model
    assert r.diagnostics['reported_model_redacted'] == 1
    from llm.bridge import BackendProviderBridge
    from backend.llm.contracts import ProviderRequest
    native=BackendProviderBridge(primary.provider).infer(ProviderRequest(MESSAGES,SCHEMA,2048,1))
    assert native.model is None  # never present a redacted config name as a provider report
    server.payload['choices'][0]['message']['content']=json.dumps({'answer':'prefix-'+secret+'-suffix'})
    with pytest.raises(InferenceError,match='LLM_SENSITIVE_RESPONSE'):
        LLMGateway(primary).generate(MESSAGES,None)
