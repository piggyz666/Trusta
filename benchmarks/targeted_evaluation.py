"""One-shot, hash-pinned, cost-bounded REAL inference; never issues bank approval."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import getproxies
from backend.config import Config
from backend.domain import canonical, digest
from backend.llm.configuration import LLMSettings
from backend.llm.contracts import InferenceError
from backend.llm.gateway import LLMGateway
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.service import TrustaService
from benchmarks.hardening_holdout import configuration
from benchmarks.targeted_score import score, summarize

ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/'data/targeted-llm-budget.json'
LOCK=threading.Lock()


def charge_reserve(provider,request,initial_request=True):
    # UTF-8 bytes are deliberately conservative for input token estimation.
    prices={'deepseek':(.3,1.2),'gemini':(.25,1.5)}[provider]
    estimate=(len(canonical(request.messages).encode())+len(canonical(request.output_schema).encode())+1024)*prices[0]/1e6+request.max_output_tokens*prices[1]/1e6
    with LOCK:
        state=json.loads(LEDGER.read_text()) if LEDGER.exists() else {'requests':0,'reserved_or_estimated_usd':0,'attempts':[],'max_usd':5,'max_requests':800}
        protected=state.get('protected_initial_requests',0)
        if (state['requests']>=800 or state['reserved_or_estimated_usd']+estimate>5 or
                not initial_request and 800-state['requests']<=protected): raise InferenceError('LLM_BUDGET_EXHAUSTED')
        if initial_request and protected:state['protected_initial_requests']=protected-1
        ticket=state['requests'];state['requests']+=1;state['reserved_or_estimated_usd']+=estimate
        state['attempts'].append({'provider':provider,'reserved':estimate,'state':'RESERVED'})
        LEDGER.write_text(json.dumps(state,indent=2),encoding='utf-8')
    return ticket,estimate,prices


def charge_usage(ticket,reserve,prices,usage):
    measured=usage and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens'))
    cost=(usage['input_tokens']*prices[0]+usage['output_tokens']*prices[1])/1e6 if measured else reserve
    with LOCK:
        state=json.loads(LEDGER.read_text());state['reserved_or_estimated_usd']+=cost-reserve
        state['attempts'][ticket].update(state='MEASURED_ESTIMATE' if measured else 'RESERVATION_RETAINED',cost=cost,usage=usage)
        LEDGER.write_text(json.dumps(state,indent=2),encoding='utf-8')
    return cost


def restore_pre():
    # Only the two proposal modules are overlaid from immutable pre-change
    # sources. Frozen execution code must retain its pre-review hashes.
    from backend.llm import compact_graph, graph_proposal
    frozen=json.loads((ROOT/'data/targeted-evaluation-freeze.json').read_text())
    for module in (compact_graph,graph_proposal):
        path=ROOT/'data/targeted-pre-source'/(module.__name__.split('.')[-1]+'.py')
        original='backend/llm/'+path.name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frozen['pre_sources'][original]: raise ValueError('Pre source changed')
        exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'),module.__dict__)


def evaluate(case,provider,mode):
    captured=[];cfg=configuration(provider)
    model='deepseek-flash' if provider=='deepseek' else 'gemini-3.1-flash-lite'
    cfg=replace(cfg,model=model,endpoint='https://api.deepseek.com/chat/completions' if provider=='deepseek' else 'https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent')
    base=OpenAICompatibleProvider if provider=='deepseek' else GeminiProvider
    class Capturing(base):
        def parse(self,payload):
            self.last_payload=payload
            return super().parse(payload)
        def infer(self,request):
            self.last_payload=None
            ticket,reserve,prices=charge_reserve(provider,request,initial_request=not captured)
            try:
                response=super().infer(request)
            except Exception as exc:
                cost=charge_usage(ticket,reserve,prices,None)
                chain=[];current=exc
                while current is not None and len(chain)<4:
                    chain.append({'type':type(current).__name__,'winerror':getattr(current,'winerror',None)})
                    current=current.__context__
                captured.append({'raw_output':None,'raw_provider_response':self.last_payload,'exception_types':chain,
                    'error_code':getattr(exc,'code','PROVIDER_ERROR'),'usage':None,'cost_usd_estimate':cost,'schema_hash':digest(request.output_schema)})
                raise
            cost=charge_usage(ticket,reserve,prices,response.usage)
            captured.append({'raw_output':response.text,'usage':response.usage,'model':response.model,'configured_model':model,
                             'prompt':request.messages,'schema_hash':digest(request.output_schema),'cost_usd_estimate':cost})
            return response
    start=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='trusta-targeted-') as directory:
        s=TrustaService(Config(db_path=directory+'/bank.db',banking_version=5,communication_version=3,signing_key=os.urandom(32),safety_profile='competition'))
        options=dict(mode='assisted',version='targeted-v1',timeout_seconds=60,max_output_tokens=4096,max_request_chars=40000,max_fallbacks=0,graph_format='compact_v2',providers=(cfg,))
        if mode!='pre': options['graph_repair_attempts']=1
        s.llm=LLMGateway(LLMSettings(**options),s.db,s.audit,{cfg.name:Capturing(cfg)})
        s.llm.identity_guard=s.crypto_registry.require_active
        task=s.graphs.propose('demo',case['goal'],case['inputs'])
        writes=len(s.db.rows('SELECT * FROM transactions'))+len(s.db.rows('SELECT * FROM bank_operations'))
        if writes: raise AssertionError('Unauthorized bank write')
        return {'id':case['id'],'group':case['group'],'category':case['category'],'provider':provider,'configured_model':model,
                'system_status':task['status'],'errors':task['errors'],'installed_graph':bool(task['steps']),
                'normalized_candidate':task.get('graph_state',{}).get('graph'),'system_final':task.get('result'),
                'scores':score(case,task,captured,s.registry),'unauthorized_bank_writes':writes,
                'latency_ms':round((time.perf_counter()-start)*1000,3),'captures':captured,
                'cost_usd_estimate':sum(c['cost_usd_estimate'] for c in captured),
                'calls':[json.loads(r['body']) for r in s.db.rows('SELECT body FROM llm_calls')]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--suite',choices=['development','locked','final'],required=True)
    parser.add_argument('--mode',choices=['pre','post'],required=True);parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();Config.local()
    proxy=getproxies().get('https')
    if proxy: os.environ['TRUSTA_HOLDOUT_PROXY']=proxy
    freeze=json.loads((ROOT/'data/targeted-evaluation-freeze.json').read_text())
    path=ROOT/'benchmarks'/('targeted_'+args.suite+'_20261009.json')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=freeze['datasets'][args.suite]: raise ValueError('Dataset changed')
    if hashlib.sha256((ROOT/'benchmarks/targeted_score.py').read_bytes()).hexdigest()!=freeze['scorer_hash']: raise ValueError('Scorer changed')
    if args.mode=='post' and args.suite in {'locked','final'}:
        source_freeze=json.loads((ROOT/'data/targeted-post-source-freeze.json').read_text())
        if any(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=h for f,h in source_freeze.items()):raise ValueError('Frozen post source changed')
    output=ROOT/'data'/('targeted-'+args.suite+'-'+args.mode+'.json')
    claim=output.with_suffix('.claimed')
    # Even a failed final run is retained; do not overwrite or repeat it.
    with claim.open('x',encoding='utf-8') as stream: stream.write(canonical({'dataset_hash':freeze['datasets'][args.suite],'scorer_hash':freeze['scorer_hash'],'started_unix':time.time()}))
    if args.mode=='pre': restore_pre()
    cases=json.loads(path.read_text(encoding='utf-8'))['cases'];rows=[]
    if args.mode=='post' and args.suite in {'locked','final'}:
        with LOCK:
            ledger=json.loads(LEDGER.read_text())
            ledger['protected_initial_requests']=len(cases)*2+(240 if args.suite=='locked' else 0)
            LEDGER.write_text(json.dumps(ledger,indent=2),encoding='utf-8')
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(evaluate,c,p,args.mode):(c['id'],p) for c in cases for p in ('deepseek','gemini')}
        for f in as_completed(futures):
            try: row=f.result()
            except Exception as exc:
                cid,p=futures[f];row={'id':cid,'provider':p,'runner_error':type(exc).__name__}
            rows.append(row)
            report={'suite':args.suite,'mode':args.mode,'dataset_hash':freeze['datasets'][args.suite],'scorer_hash':freeze['scorer_hash'],
                    'real_inference':True,'complete':len(rows)==len(futures),'rows':sorted(rows,key=lambda r:(r['provider'],r['id'])),
                    'metrics':{p:summarize([r for r in rows if r['provider']==p and 'scores' in r]) for p in ('deepseek','gemini')},
                    'runner_errors':[r for r in rows if 'runner_error' in r]}
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'completed':len(rows),'total':len(futures),'id':row['id'],'provider':row['provider'],'status':row.get('system_status',row.get('runner_error'))}),flush=True)
    print(json.dumps(report['metrics']),flush=True)

if __name__=='__main__': main()
