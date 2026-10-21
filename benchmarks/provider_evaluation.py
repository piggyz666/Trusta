"""Provider-interface evaluation; candidates have no bank or execution handle."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
from backend.domain import canonical, uid
from backend.intent import IntentNormalization
from backend.banking.intent import BankingIntent
from backend.runtime.banking_plan import OWNERS
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import ProviderRequest, ProviderResponse, InferenceContext, InferenceRequest, InferenceError
from backend.llm.router import ModelRouter
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.llm.providers.http_adapter import HTTPAdapter
from backend.llm.validation import strict_json, validate
from benchmarks.banking_task_benchmark import write_reports

DATASET=Path(__file__).with_name('provider_cases.json')
FIELD_NAMES=('amount','recipient_name','source_account_id','target_id','month','product_ids')
SCHEMA={'type':'object','required':['task_type','needs_clarification','fields','agents'],'properties':{
    'task_type':{'type':'string','maxLength':64},'needs_clarification':{'type':'boolean'},
    'fields':{'type':'array','maxItems':8,'items':{'type':'object','required':['name','value'],'properties':{
        'name':{'type':'string','enum':list(FIELD_NAMES)},'value':{'type':'string','maxLength':256}}}},
    'agents':{'type':'array','maxItems':6,'items':{'type':'string','enum':['supervisor','planner','transaction','finance','wealth','service']}}}}


def grounded_output(text):
    normalized=BankingIntent(IntentNormalization()).normalize(text)
    kind=normalized['task_type']
    owner=OWNERS.get(kind,{'TRANSFER':'transaction','BILL_ANALYSIS':'finance','WEALTH_RECOMMENDATION':'wealth'}.get(kind))
    agents=['supervisor','planner',owner]
    if kind=='CROSS_SCENARIO':
        agents.append('service')
    values={key:value for key,value in normalized.items() if key in FIELD_NAMES}
    values.update({key:value for key,value in normalized.get('parameters',{}).items() if key in FIELD_NAMES})
    return {'task_type':kind,'needs_clarification':normalized.get('needs_clarification',False),
            'fields':[{'name':key,'value':','.join(value) if isinstance(value,list) else str(value)} for key,value in sorted(values.items())],
            'agents':agents}


def guard_candidate(text,output):
    validate(output,SCHEMA)
    if output != grounded_output(text):
        raise InferenceError('UNGROUNDED_MODEL_FIELDS_OR_PLAN')
    return output


class OfflineEvaluationProvider:
    """Explicit curated response fixtures for harness CI, not a language model."""
    def __init__(self,cases):
        self.cases={row['id']:row for row in cases}
    def infer(self,request):
        case=self.cases[strict_json(request.messages[-1]['content'])['case_id']]
        return ProviderResponse(canonical(case.get('fixture',case['expected'])),'offline-evaluation-fixture')
    def health(self):
        return {'protocol':'OFFLINE_FIXTURE','network_checked':False}
    def describe_capabilities(self):
        return {'protocol':'OFFLINE_FIXTURE','structured_outputs':True}


def run_evaluation(settings=None,provider_name=None,provider=None,dataset=None):
    cases=json.loads(Path(dataset or DATASET).read_text(encoding='utf-8'))
    settings=settings or LLMSettings(mode='shadow',providers=(ProviderConfig('offline-eval',max_sensitivity='restricted'),))
    settings.validate()
    candidates=[config for config in settings.providers if provider_name is None or config.name==provider_name]
    if not candidates:
        raise InferenceError('LLM_PROVIDER_NOT_CONFIGURED')
    config=candidates[0]
    selected_settings=replace(settings,providers=(config,),routes={})
    if provider is None:
        provider=OfflineEvaluationProvider(cases) if config.kind=='mock' else {'http':HTTPAdapter,'openai_compatible':OpenAICompatibleProvider}[config.kind](config)
    router=ModelRouter(selected_settings)
    outcomes=[]
    for case in cases:
        begin=time.perf_counter()
        actual=None
        failure=None
        guard='REJECT'
        request=ProviderRequest(({'role':'system','content':'Interpret synthetic bank requests as an untrusted candidate. Never invent bank facts or authorization. '+canonical(SCHEMA)},
                                 {'role':'user','content':canonical({'case_id':case['id'],'text':case['text']})}),SCHEMA,settings.max_output_tokens,settings.timeout_seconds)
        try:
            route=router.select(InferenceRequest(uid(),InferenceContext(None,'synthetic','supervisor','EVALUATION',0),
                                'intent_normalization',{'text':case['text']},complexity='complex'),request)
            if not route.providers:
                raise InferenceError('LLM_NO_SAFE_ROUTE')
            response=provider.infer(request)
            actual=strict_json(response.text)
            guard_candidate(case['text'],actual)
            guard='ACCEPT'
        except InferenceError as exc:
            failure=exc.code
        except Exception:
            failure='PROVIDER_EVALUATION_ERROR'
        # Deliberate malicious fixtures are attack controls. A real provider must return a safe candidate instead.
        expected_guard=case.get('expected_guard','ACCEPT') if config.kind=='mock' else 'ACCEPT'
        checks={'intent':bool(actual and actual.get('task_type')==case['expected']['task_type']),
                'parameters':bool(actual and actual.get('fields')==case['expected']['fields']),
                'clarification':bool(actual and actual.get('needs_clarification')==case['expected']['needs_clarification']),
                'plan':bool(actual and actual.get('agents')==case['expected']['agents']),
                'guard':guard==expected_guard}
        passed=checks['guard'] and (expected_guard=='REJECT' or all(checks.values()))
        outcomes.append({'case_id':case['id'],'raw_case':case,'expected':case['expected']|{'guard':expected_guard},
                         'actual':{'candidate':actual,'guard':guard,'guard_reason':failure},'checks':checks,'passed':passed,
                         'failure_reason':None if passed else failure or 'EXPECTED_OUTPUT_MISMATCH',
                         'duration_ms':(time.perf_counter()-begin)*1000})
    def rate(key):
        n=sum(row['checks'][key] for row in outcomes)
        return {'numerator':n,'denominator':len(outcomes),'rate':n/len(outcomes)}
    return {'schema_version':1,'benchmark':'PROVIDER_EVALUATION','provider':config.name,'provider_kind':config.kind,
            'evaluation_mode':'OFFLINE_FIXTURES' if config.kind=='mock' else 'CONFIGURED_PROVIDER_CALLS',
            'real_model_experiment_verified':False,'bank_tools_available':False,'passed':all(row['passed'] for row in outcomes),
            'metrics':{key:rate(key) for key in ('intent','parameters','clarification','plan','guard')},'cases':outcomes,
            'limits':['Fixture CI proves harness/guard behavior, not model understanding','Interpretation remains conservatively bounded by deterministic source grounding',
                      'Ambiguous bank identity is resolved only by authority at runtime, never by this provider']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--llm-config')
    parser.add_argument('--provider')
    parser.add_argument('--output',default='docs/phase5-provider-evaluation.json')
    args=parser.parse_args()
    report=run_evaluation(LLMSettings.load(args.llm_config) if args.llm_config else None,args.provider)
    write_reports(report,args.output)
    print(json.dumps({key:report[key] for key in ('passed','evaluation_mode','real_model_experiment_verified','metrics')}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':
    main()
