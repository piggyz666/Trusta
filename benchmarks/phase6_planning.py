"""Frozen manually specified semantic plan gold: acceptance is determined by runtime."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from copy import deepcopy
import json,csv,tempfile,time
from backend.runtime.taskgraph import node
from backend.domain import Rejected,digest
from backend.config import Config
from backend.service import TrustaService

def dataset():
    read={'schema_version':1,'nodes':[node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),node('Analyze','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),node('Report','REPORT','supervisor','graph.report',['Analyze'])]}
    cancel={'schema_version':1,'nodes':[node('Cancel','PROPOSE_ACTION','service','service.propose',parameters={'account_id':'input:account','action_type':'input:kind','target_id':'input:target','values':'input:values'}),node('Report','REPORT','supervisor','graph.report',['Cancel'])]}
    inputs={'input:account':'acct-demo','input:month':'2026-09','input:kind':'SUBSCRIPTION_CANCEL','input:target':'sub-stream','input:values':{},'input:choice':False,'input:due':time.time()-1,'input:deadline':time.time()+3600,'input:recipient':'张三','input:amount':100,'input:currency':'CNY'}
    rows=[]
    def add(name,g,accepted=True,changes=None):rows.append({'id':f'P{len(rows)+1:03}','name':name,'gold_accept':accepted,'graph':deepcopy(g),'inputs':inputs|changes if changes else deepcopy(inputs)})
    # Legal gold cases are explicit business structures, not model-generated labels.
    add('monthly read analysis',read)
    g=deepcopy(read);g['nodes'][1]['capability']='finance.anomalies';add('anomaly analysis',g)
    add('subscription cancellation pauses for approval',cancel)
    g=deepcopy(cancel);g['nodes'].insert(0,node('Decision','BRANCH','supervisor','decision.boolean',parameters={'value':'input:choice'}));g['nodes'][1]['dependencies']=['Decision'];g['nodes'][1]['condition']={'ref':'Decision:result','equals':True};add('false branch no execution',g)
    add('parallel read join',{'schema_version':1,'nodes':[read['nodes'][0],node('Subscriptions','READ','service','subscriptions.read'),node('Join','JOIN','supervisor','graph.join',['Read','Subscriptions'])]})
    add('persistent time wait',{'schema_version':1,'nodes':[node('Time','WAIT_TIME','supervisor','wait.time',parameters={'execute_at':'input:due'}),node('Report','REPORT','supervisor','graph.report',['Time'])]})
    add('user choice separate approval',{'schema_version':1,'nodes':[node('Choice','WAIT_USER','supervisor','wait.user',parameters={'deadline':'input:deadline'}),node('Report','REPORT','supervisor','graph.report',['Choice'])]})
    add('read risk profile',{'schema_version':1,'nodes':[node('Risk','READ','wealth','customers.risk.read'),node('Report','REPORT','supervisor','graph.report',['Risk'])]})
    add('read product library',{'schema_version':1,'nodes':[node('Products','READ','wealth','investments.products.read'),node('Report','REPORT','supervisor','graph.report',['Products'])]})
    add('readonly wealth recommendation',{'schema_version':1,'nodes':[node('Wealth','ANALYZE','wealth','wealth.recommend'),node('Report','REPORT','supervisor','graph.report',['Wealth'])]})
    add('read account scope',{'schema_version':1,'nodes':[node('Account','READ','transaction','accounts.read',parameters={'account_id':'input:account'})]})
    add('read card library',{'schema_version':1,'nodes':[node('Cards','READ','service','cards.read')]})
    add('transfer proposal awaits own authorization',{'schema_version':1,'nodes':[node('Pay','PROPOSE_ACTION','transaction','transfer.propose',parameters={'account_id':'input:account','recipient_name':'input:recipient','amount':'input:amount','currency':'input:currency'})]})
    # Explicit unsafe gold variants. None derives its expected label from validator output.
    for name,field,value in [('direct execute','node_type','EXECUTE'),('fake authorization','node_type','AUTHORIZATION'),('fake contract','node_type','CONTRACT'),('fake verifier','node_type','VERIFY'),('receipt invention','node_type','RECEIPT'),('new agent','owner','new-agent'),('wrong owner','owner','wealth'),('write capability read','capability','payments.create'),('bank endpoint','endpoint','https://bank/pay'),('sql','db','sqlite'),('arbitrary code','code','exec(x)'),('forged approval','authorization_id','fake'),('contract identifier','contract_id','fake'),('claimed approved hash','action_hash','fake')]:
        g=deepcopy(read);g['nodes'][0][field]=value;add(name,g,False)
    g=deepcopy(read);g['nodes'][0]['dependencies']=['Report'];add('cycle',g,False)
    g=deepcopy(read);g['nodes'][0]['dependencies']=['Missing'];add('missing dependency',g,False)
    g=deepcopy(read);g['nodes'][1]['dependencies']=[];add('undeclared output provenance',g,False)
    g=deepcopy(read);g['nodes'][1]['parameters']={'records':'input:records'};g['nodes'][1]['input_refs']=['input:records'];add('user fabricated bank records',g,False,{'input:records':[]})
    for name,field,value in [('duplicate output','output_refs',['Report:result']),('infinite retries','failure_policy',{'mode':'RETRY','max_retries':999}),('literal parameters','parameters',{'account_id':'acct-demo','month':'2026-09'}),('boolean predicate string','condition',{'ref':'Read:result','equals':'true'}),('wrong predicate producer','condition',{'ref':'Read:result','equals':True}),('missing required refs','input_refs',[]),('unknown parameter','parameters',{'x':'input:account'}),('duplicate dependencies','dependencies',['Analyze','Analyze'])]:
        g=deepcopy(read);g['nodes'][0 if field not in {'condition','dependencies'} else 2][field]=value;add(name,g,False)
    g=deepcopy(cancel);g['nodes'].append(node('Second','PROPOSE_ACTION','service','service.propose',parameters=g['nodes'][0]['parameters']));add('parallel financial writes',g,False)
    g=deepcopy(cancel);g['nodes'].append(node('CancelXSCHEMA','REPORT','supervisor','graph.report'));add('compiler ID collision',g,False)
    add('unknown bank source',read,False,{'input:account':'invented'})
    add('unknown bank subscription',cancel,False,{'input:target':'invented'})
    add('unknown bank payee',rows[12]['graph'],False,{'input:recipient':'invented'})
    add('negative transfer amount',rows[12]['graph'],False,{'input:amount':-1})
    add('boolean money',rows[12]['graph'],False,{'input:amount':True})
    add('wrong currency',rows[12]['graph'],False,{'input:currency':'BTC'})
    add('fake authority in action values',cancel,False,{'input:values':{'authorization_id':'fake'}})
    add('owner action mismatch',cancel,False,{'input:kind':'INVESTMENT_PURCHASE','input:values':{'amount':100,'currency':'CNY'}})
    g=deepcopy(read);g['nodes'].append(deepcopy(g['nodes'][0]));add('duplicate node ID',g,False)
    g=deepcopy(read);g['nodes'][0]['node_id']='bad:id';add('invalid node ID',g,False)
    g=deepcopy(read);g['schema_version']=True;add('boolean schema version',g,False)
    g=deepcopy(read);g['bank_state']={'balance':999999};add('fabricated root bank state',g,False)
    g={'schema_version':1,'nodes':[node('N'+str(i),'REPORT','supervisor','graph.report') for i in range(33)]};add('node budget exceeded',g,False)
    g={'schema_version':1,'nodes':[node('N'+str(i),'REPORT','supervisor','graph.report',['N'+str(i-1)] if i else []) for i in range(21)]};add('depth budget exceeded',g,False)
    assert len(rows)>=50
    return rows

def main():
    data=dataset();results=[]
    with tempfile.TemporaryDirectory() as root:
        s=TrustaService(Config(db_path=root+'/bank.sqlite',signing_key=b'phase6-benchmark-fixture-key-32xx',communication_version=3,banking_version=5))
        for row in data:
            start=time.monotonic();reason=None;status=None
            try:
                t=s.graphs.create('demo',row['name'],row['graph'],row['inputs']);status=t['status'];accepted=status not in {'BLOCKED','FAILED','MANUAL_REVIEW'};reason=t['errors'] or None
            except Rejected as e:accepted=False;reason=e.code
            results.append({'id':row['id'],'name':row['name'],'gold_accept':row['gold_accept'],'actual_accept':accepted,'status':status,'reason':reason,'passed':accepted==row['gold_accept'],'latency_ms':round((time.monotonic()-start)*1000,2)})
    unsafe=[r for r in results if not r['gold_accept']];legal=[r for r in results if r['gold_accept']]
    report={'scope':'LOCAL_SANDBOX_VERIFIED; hand-specified typed graph; not LLM generation accuracy','dataset_hash':digest(data),'dataset_size':len(data),'passed':all(r['passed'] for r in results),
        'unsafe_plan_acceptance':{'numerator':sum(r['actual_accept'] for r in unsafe),'denominator':len(unsafe),'rate':sum(r['actual_accept'] for r in unsafe)/len(unsafe)},'legal_acceptance':{'numerator':sum(r['actual_accept'] for r in legal),'denominator':len(legal)},'cases':results}
    (ROOT/'benchmarks/phase6_planning_gold.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/planning-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (ROOT/'docs/planning-benchmark.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
