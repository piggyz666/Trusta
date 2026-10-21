"""Independent task evaluation against a real isolated SQLite Virtual Bank."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
import time
from datetime import datetime
from backend.business import CHINA
from backend.config import Config
from backend.domain import digest, uid
from backend.service import TrustaService
from backend.banking.actions import ACTION_SPECS

DATASET=Path(__file__).with_name('banking_cases.json')


def approve_all(service,task,deny_index=None):
    if 'action_records' in task:
        for i,entry in enumerate(task['action_records']):
            if entry['status']=='WAITING_USER' and entry['authorization']:
                task=service.banking.authorize(task['task_id'],'demo',digest(entry['action']),i!=deny_index,'benchmark-secret',entry['action']['action_id'])
    elif task.get('authorization') and task['authorization']['status'].startswith('PENDING'):
        task=service.authorize(task['task_id'],'demo',task['authorization']['action_hash'],True,'benchmark-secret')
    return task


def plan_valid(task):
    seen=set()
    for step in task['steps']:
        if step['step_id'] in seen or not set(step['dependencies'])<=seen:
            return False
        seen.add(step['step_id'])
        if step['owner'] not in {'supervisor','planner','transaction','finance','wealth','service','policy','schema','authorization','executor','verifier','receipts','runtime'}:
            return False
    return bool(seen)


def authorization_compliant(task):
    if 'action_records' not in task:
        if not task.get('receipt'):
            return True
        return task['receipt']['approved_action_hash']==digest(task['proposed_actions'][0])==task['authorization']['action_hash'] and task['authorization']['status']=='AUTHORIZED'
    receipts=[row['receipt'] for row in task['action_records'] if row.get('receipt')]
    if len({receipt['authorization_id'] for receipt in receipts})!=len(receipts):
        return False
    return all(row['authorization'] and row['authorization']['status']=='AUTHORIZED' and
               row['receipt']['authorization_id']==row['authorization']['authorization_id'] and
               row['receipt']['approved_action_hash']==row['authorization']['action_hash']==digest(row['action']) and
               row['verification']['status']=='VERIFIED' for row in task['action_records'] if row.get('receipt'))


def run_benchmark(dataset=None):
    cases=json.loads(Path(dataset or DATASET).read_text(encoding='utf-8'))
    outcomes=[]
    started=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='trusta-banking-benchmark-') as directory:
        for case in cases:
            s=TrustaService(Config(db_path=str(Path(directory)/(case['id']+'.db')),signing_key=b'isolated-banking-benchmark-key',
                                  communication_version=3,banking_version=5,strong_auth_secret='benchmark-secret'))
            for text in case.get('prepare',[]):
                assert approve_all(s,s.create_task(text))['status']=='COMPLETED'
            if case.get('setup'):
                with s.db.transaction() as conn:
                    if case['setup']=='ambiguous_payee':
                        conn.execute("INSERT INTO payees VALUES('another-zhang','demo','张三',0)")
                    elif case['setup']=='lost_card':
                        conn.execute("UPDATE cards SET status='LOST' WHERE id='card-demo'")
                    else:
                        raise ValueError('unknown fixture setup')
            used_tools=[]
            original_execute=s.gateway.execute
            def traced_execute(actor,action,contract):
                used_tools.append('transfer.create' if action['action_type']=='TRANSFER' else ACTION_SPECS[action['action_type']]['tool'])
                return original_execute(actor,action,contract)
            s.gateway.execute=traced_execute
            begin=time.perf_counter()
            failure=None
            try:
                if case.get('turns'):
                    conversation=s.conversations.create()
                    revision=0
                    for text in case['turns']:
                        response=s.conversations.send(conversation['conversation_id'],'demo',uid(),revision,text)
                        revision=response['revision']
                    task=response['task']
                else:
                    text=case['text']
                    if case.get('time_trigger'):
                        stamp=datetime.fromtimestamp(time.time()+5,CHINA).strftime('%Y-%m-%d %H:%M:%S')
                        text=text.replace('{execute_at}',stamp)
                    task=s.create_task(text)
                clarification=task['status']=='WAITING_USER' and not task.get('proposed_actions')
                task=approve_all(s,task,case.get('deny_index'))
                if case.get('time_trigger'):
                    job=s.banking.jobs(task['task_id'])[0]
                    if case['time_trigger']=='expired_authorization':
                        auth=s.auth.get(task['action_records'][0]['authorization']['authorization_id'])
                        auth['expires_at']=time.time()-1
                        from backend.domain import canonical
                        with s.db.transaction() as conn:
                            conn.execute('UPDATE authorizations SET body=? WHERE id=?',(canonical(auth),auth['authorization_id']))
                    while time.time()<job['execute_at']:
                        time.sleep(max(0,min(0.1,job['execute_at']-time.time())))
                    s.banking.tick()
                    task=s.get_task(task['task_id'])
                rejected_model_candidate=None
                if case.get('model_candidate'):
                    from benchmarks.provider_evaluation import guard_candidate
                    from backend.llm.contracts import InferenceError
                    try:
                        guard_candidate(case['text'],case['model_candidate'])
                        rejected_model_candidate=False
                    except InferenceError:
                        rejected_model_candidate=True
                normalized=task['context']['normalized']
                fields={key:value for key,value in normalized.items() if key in {'amount','source_account_id','target_id'}}
                fields.update(normalized.get('parameters',{}))
                if task['proposed_actions']:
                    action=task['proposed_actions'][0]
                    if action['action_type']=='TRANSFER':
                        fields.update({key:action[key] for key in ('amount','recipient_id','source_account_id')})
                    else:
                        fields['target_id']=action['target']['id']
                balance=s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo')['balance']
                checks={
                    'intent':task['task_type']==case['type'],
                    'parameters':all(fields.get(key)==value for key,value in case.get('parameters',{}).items()),
                    'clarification':clarification==case.get('clarification',False),
                    'plan':plan_valid(task),
                    'tools':used_tools==([] if case['tool'] is None else case['tool'] if isinstance(case['tool'],list) else [case['tool']]),
                    'authorization':authorization_compliant(task),
                    'state':task['status']==case['status'] and balance==case['balance'],
                    'audit':s.audit.verify_chain(),
                }
                facts={'status':task['status'],'task_type':task['task_type'],'fields':fields,'tools':used_tools,'balance':balance,
                       'action_statuses':[row['status'] for row in task.get('action_records',[])],'dag':task['steps']}
                if rejected_model_candidate is not None:
                    facts['model_candidate_rejected']=rejected_model_candidate
                    checks['state'] &= rejected_model_candidate
                if case.get('time_trigger'):
                    facts['jobs']=s.banking.jobs(task['task_id'])
                    checks['state'] &= facts['jobs'][0]['status']==case['job_status']
                if 'net_spend' in case:
                    facts['net_spend']=task['result']['summary']['net_spend']
                    checks['state'] &= facts['net_spend']==case['net_spend']
                if 'holding' in case:
                    facts['holding']=s.gateway.read('wealth','demo','portfolio.get',account_id='acct-demo',product_id='sandbox-r1')['amount_cents']
                    checks['state'] &= facts['holding']==case['holding']
                for expected_key,column in (('card_status','status'),('limit','spending_limit'),('online_enabled','online_enabled')):
                    if expected_key in case:
                        facts[expected_key]=s.gateway.read('service','demo','card.get',resource_id='card-demo')[column]
                        checks['state'] &= facts[expected_key]==case[expected_key]
                for key,tool,resource in (('subscription_status','subscription.get','sub-stream'),('debit_status','direct_debit.get','dd-electric')):
                    if key in case:
                        facts[key]=s.gateway.read('service','demo',tool,resource_id=resource)['status']
                        checks['state'] &= facts[key]==case[key]
                if 'cancellations' in case:
                    facts['cancellations']=sum(row['status']=='CANCELLED' for row in s.gateway.read('service','demo','subscription.list'))
                    checks['state'] &= facts['cancellations']==case['cancellations']
                if 'split_total' in case:
                    facts['split_total']=sum(row['amount'] for row in task['result']['split_requests'])
                    checks['state'] &= facts['split_total']==case['split_total']
                passed=all(checks.values())
                failure=None if passed else 'FAILED_CHECKS:'+','.join(key for key,value in checks.items() if not value)
            except Exception as exc:
                facts={'exception_type':type(exc).__name__,'code':getattr(exc,'code','BENCHMARK_CASE_FAILED')}
                checks={key:False for key in ('intent','parameters','clarification','plan','tools','authorization','state','audit')}
                passed=False
                failure=facts['code']
            outcomes.append({'case_id':case['id'],'raw_case':case,'expected':case,'actual':facts,'checks':checks,'passed':passed,
                             'failure_reason':failure,'duration_ms':(time.perf_counter()-begin)*1000})
    def rate(check,selected=None):
        rows=outcomes if selected is None else [row for row in outcomes if selected(row)]
        numerator=sum(row['passed'] if check=='passed' else row['checks'][check] for row in rows)
        return {'numerator':numerator,'denominator':len(rows),'rate':numerator/len(rows) if rows else None}
    metrics={
        'case_pass_rate':rate('passed'),
        'task_completion_rate':rate('passed',lambda row:row['expected']['status']=='COMPLETED'),
        'intent_accuracy':rate('intent'),
        'parameter_accuracy':rate('parameters',lambda row:bool(row['expected'].get('parameters'))),
        'clarification_accuracy':rate('clarification'),
        'plan_validity':rate('plan'),'tool_selection_accuracy':rate('tools'),
        'authorization_compliance':rate('authorization'),'final_state_correctness':rate('state'),
        'cross_scenario_completion_rate':rate('passed',lambda row:row['expected']['type']=='CROSS_SCENARIO')}
    return {'schema_version':1,'benchmark':'BANKING_TASK','banking_version':5,'communication_version':3,
            'dataset':str(DATASET.name),'model_mode':'DETERMINISTIC; no model calls','real_bank':False,
            'passed':all(row['passed'] for row in outcomes),'metrics':metrics,'cases':outcomes,'duration_seconds':time.perf_counter()-started,
            'limits':['Synthetic sandbox tasks, not production bank evaluation','Task completion denominator excludes expected clarification/block outcomes',
                      'Model hallucination resistance is separately exercised by provider_evaluation']}


def write_reports(report,output):
    path=Path(output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with path.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['case_id','raw_case','expected','actual','passed','failure_reason','duration_ms'])
        writer.writeheader()
        for row in report['cases']:
            writer.writerow({key:json.dumps(row[key],ensure_ascii=False) if isinstance(row[key],dict) else row[key] for key in writer.fieldnames})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset')
    parser.add_argument('--output',default='docs/phase5-banking-benchmark.json')
    args=parser.parse_args()
    report=run_benchmark(args.dataset)
    write_reports(report,args.output)
    print(json.dumps({'passed':report['passed'],'metrics':report['metrics'],'failures':[(row['case_id'],row['failure_reason']) for row in report['cases'] if not row['passed']]}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':
    main()
