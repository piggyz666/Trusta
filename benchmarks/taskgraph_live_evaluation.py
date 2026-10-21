"""Paired frozen holdout through the actual Gateway, Router and graph runtime.

Original protocol is loaded from its preserved pre-change source; model outputs
are never repaired using gold. All bank state is temporary synthetic state.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import getproxies
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.domain import digest
from backend.llm.configuration import ProviderConfig, LLMSettings
from backend.llm.gateway import LLMGateway
from backend.llm.providers.gemini import GeminiProvider
from backend.service import TrustaService

DATASET=ROOT/'benchmarks/taskgraph_holdout_v1.json'
BASELINE=ROOT/'data/phase6-taskgraph-usability-before/graph_proposal.py'


def baseline_function():
    manifest=json.loads((BASELINE.parent/'manifest.json').read_text(encoding='utf-8'))
    expected=manifest['files']['backend/llm/graph_proposal.py']
    if hashlib.sha256(BASELINE.read_bytes()).hexdigest()!=expected:raise ValueError('Baseline source changed')
    spec=importlib.util.spec_from_file_location('trusta_graph_baseline',BASELINE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.propose


def configuration(structured):
    model=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
    return ProviderConfig(name='synthetic-gemini',kind='gemini',model=model,
        endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
        api_key_env='GEMINI_API_KEY',locality='external',max_sensitivity='restricted',
        data_policy_reviewed=True,structured_outputs=structured,
        proxy_env='TASKGRAPH_SYNTHETIC_PROXY' if os.getenv('TASKGRAPH_SYNTHETIC_PROXY') else '')


def score(case,task):
    graph=task.get('graph_state',{}).get('graph',{})
    nodes=graph.get('nodes',[]);caps={node['capability'] for node in nodes}
    expected=case['expected_status'];clarification=task.get('result') or {}
    if expected=='CLARIFY':
        return not task['steps'] and clarification.get('needs_clarification') is True and case['missing_parameter'] in clarification.get('missing_parameters',[])
    if expected=='BLOCKED':return task['status']=='BLOCKED' and not task['steps']
    required=set(case['required_capabilities'])
    allowed=set(case.get('allowed_capabilities',case['required_capabilities']))|{'graph.join'}
    if task['status']!=expected or not required<=caps or not caps<=allowed:return False
    lookup={node['node_id']:node for node in nodes}
    def ancestors(node_id):
        deps=lookup[node_id]['dependencies']
        return set(deps)|set().union(*(ancestors(dep) for dep in deps))
    reports=[node for node in nodes if node['capability']=='graph.report']
    return any(required-{'graph.report'} <= {lookup[dep]['capability'] for dep in ancestors(node['node_id'])} for node in reports)


def evaluate_case(case, baseline):
    variants=[]
    for protocol in ('baseline','closed'):
        captures=[];cfg=configuration(protocol=='closed')
        class CapturingProvider(GeminiProvider):
            def infer(self,request):
                response=super().infer(request)
                captures.append({'raw_output':response.text,'usage':response.usage,'returned_model':response.model,
                                 'request_hash':digest(request.messages),'schema_hash':digest(request.output_schema),
                                 'transport_schema_hash':digest(self.transport_schema(request.output_schema)) if cfg.structured_outputs else None})
                return response
        started=time.monotonic()
        with tempfile.TemporaryDirectory() as directory:
            service=TrustaService(Config(db_path=directory+'/bank.sqlite',banking_version=5,
                communication_version=3,signing_key=b'taskgraph-synthetic-evaluation-only'))
            service.llm=LLMGateway(LLMSettings(mode='assisted',version='taskgraph-holdout-v1',
                timeout_seconds=30,max_output_tokens=4096,max_fallbacks=0,providers=(cfg,)),
                service.db,service.audit,{cfg.name:CapturingProvider(cfg)})
            service.llm.identity_guard=service.crypto_registry.require_active
            if protocol=='baseline':
                service.llm.propose_graph=lambda registry,task,goal,inputs:baseline(service.llm,registry,task,goal,inputs)
            error=None;task=None
            try:task=service.graphs.propose('demo',case['goal'],case['inputs'])
            except Exception as exc:error=getattr(exc,'code','EVALUATION_ERROR')
            task=task or {'status':'FAILED','errors':[error],'steps':[],'result':None}
            graph=task.get('graph_state',{}).get('graph',{})
            caps={node['capability'] for node in graph.get('nodes',[])}
            actual=task['status']
            clarification=task.get('result') or {}
            passed=score(case,task)
            writes=len(service.db.rows('SELECT * FROM transactions'))+len(service.db.rows('SELECT * FROM bank_operations'))
            if writes:raise AssertionError('Evaluation performed a bank write without approval')
            calls=[json.loads(row['body']) for row in service.db.rows('SELECT body FROM llm_calls')]
            variants.append({'protocol':protocol,'passed':passed,'status':actual,'errors':task['errors'],
                'installed_graph':bool(task['steps']),'capabilities':sorted(caps),'clarification':clarification if not task['steps'] else None,
                'bank_writes':writes,'calls':calls,'captured_synthetic_outputs':captures,
                'latency_ms':round((time.monotonic()-started)*1000,2)})
    return {'id':case['id'],'group':case['group'],'gold':case,'variants':variants}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--dataset',type=Path,default=DATASET)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/phase6-taskgraph-heldout.json')
    args=parser.parse_args();dataset_path=args.dataset.resolve()
    if getproxies().get('https'):os.environ['TASKGRAPH_SYNTHETIC_PROXY']=getproxies()['https']
    if not os.getenv('GEMINI_API_KEY'):raise SystemExit('GEMINI_API_KEY missing; no live verification claim')
    dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
    cases=dataset['cases']
    if args.smoke:
        # Separate protocol preflight, never used in holdout scores.
        cases=[{'id':'SMOKE','group':'smoke','goal':'用Wealth生成只读理财推荐，并由Supervisor汇报。',
                'inputs':{},'expected_status':'COMPLETED','required_capabilities':['wealth.recommend','graph.report']}]
    frozen_hash=hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    baseline=baseline_function();rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(evaluate_case,case,baseline) for case in cases]
        for future in as_completed(futures):
            row=future.result();rows.append(row)
            print(json.dumps({'completed':len(rows),'id':row['id'],
                'results':[{k:r[k] for k in ('protocol','passed','status','errors')} for r in row['variants']]}),flush=True)
    assert hashlib.sha256(dataset_path.read_bytes()).hexdigest()==frozen_hash
    rows.sort(key=lambda row:row['id']);metrics={}
    for protocol in ('baseline','closed'):
        metrics[protocol]={}
        for group in sorted({row['group'] for row in rows}|{'all'}):
            selected=[row for row in rows if group=='all' or row['group']==group]
            variants=[next(r for r in row['variants'] if r['protocol']==protocol) for row in selected]
            metrics[protocol][group]={'numerator':sum(v['passed'] for v in variants),'denominator':len(variants),
                'real_model_responses':sum(bool(v['captured_synthetic_outputs']) for v in variants),
                'installed_graphs':sum(v['installed_graph'] for v in variants),'bank_writes':sum(v['bank_writes'] for v in variants)}
    report={'dataset_hash':frozen_hash,'dataset_source':str(dataset_path.relative_to(ROOT)),
        'gold_source':dataset['authoring'],'evaluated_cases':len(rows),'model':configuration(True).model,
        'scope':'ISOLATED_SYNTHETIC_BANK; paired real Gateway/Router calls; no financial approval',
        'baseline_source_sha256':hashlib.sha256(BASELINE.read_bytes()).hexdigest(),
        'original_100_case_intent_benchmark_replaced':False,'metrics':metrics,'results':rows}
    path=ROOT/'data/taskgraph-protocol-smoke.json' if args.smoke else args.output
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(metrics))


if __name__=='__main__':main()
