"""Controlled Phase5 business attacks; actual gateway, bank, registry and TIME worker."""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
from backend.business import CHINA
from backend.config import Config
from backend.domain import Rejected,canonical,digest
from backend.service import TrustaService
from benchmarks.banking_task_benchmark import write_reports

TABLES=('accounts','cards','subscriptions','direct_debits','portfolio','bank_operations','investment_transactions','transactions','ledger_entries')


def bank_state(s):
    return digest({name:[dict(row) for row in s.db.rows(f'SELECT * FROM {name} ORDER BY rowid')] for name in TABLES})


def ready(s,text):
    task=s.create_task(text)
    entry=task['action_records'][0]
    auth=s.auth.decide(entry['authorization']['authorization_id'],'demo',entry['action'],True,'benchmark-secret')
    contract=s.contracts.issue(entry['action'],entry['policy']['policy_decision_id'],auth['authorization_id'])
    return task,entry['action'],contract


def run_benchmark(trials=3):
    if type(trials) is not int or not 1<=trials<=20:
        raise ValueError('invalid trials')
    outcomes=[]
    with tempfile.TemporaryDirectory(prefix='trusta-business-security-') as directory:
        for trial in range(trials):
            cfg=Config(db_path=str(Path(directory)/f'{trial}.db'),signing_key=b'isolated-business-security-root',
                       communication_version=3,banking_version=5,strong_auth_secret='benchmark-secret')
            s=TrustaService(cfg)
            _,card,card_contract=ready(s,'冻结card-demo')
            _,purchase,purchase_contract=ready(s,'购买sandbox-r1 1000元')
            _,subscription,sub_contract=ready(s,'取消订阅sub-stream')
            def reject(name,operation,codes):
                started=time.perf_counter()
                before=bank_state(s)
                code=None
                try:
                    operation()
                except Rejected as exc:
                    code=exc.code
                except Exception:
                    code='UNEXPECTED_EXCEPTION'
                changed=bank_state(s)!=before
                outcomes.append({'case_id':f'{name}-{trial}','raw_case':{'attack':name,'trial':trial},
                    'expected':{'codes':sorted(codes),'unauthorized_bank_changes':0},
                    'actual':{'code':code,'unauthorized_bank_changes':int(changed)},'passed':code in codes and not changed,
                    'failure_reason':None if code in codes and not changed else 'BOUNDARY_OR_STATE_FAILURE','duration_ms':(time.perf_counter()-started)*1000})
            for actor in s.registry.agents.active():
                reject('agent-direct-card-'+actor,lambda actor=actor:s.gateway.execute(actor,card,card_contract),{'CAPABILITY_VIOLATION'})
            reject('wealth-direct-purchase',lambda:s.gateway.execute('wealth',purchase,purchase_contract),{'CAPABILITY_VIOLATION'})
            reject('missing-card-contract',lambda:s.gateway.execute('executor',card,{}),{'MISSING_CONTRACT'})
            changed=copy.deepcopy(subscription)
            changed['target']['id']='sub-cloud'
            reject('subscription-target-swap',lambda:s.gateway.execute('executor',changed,sub_contract),{'REJECTED_SCHEMA','ACTION_HASH_MISMATCH'})
            invented=copy.deepcopy(purchase)
            invented['parameters']['risk_level']=5
            reject('model-invented-risk',lambda:s.gateway.execute('executor',invented,purchase_contract),{'REJECTED_SCHEMA'})
            cross=s.create_task('分析2026-09消费，找出可能的订阅，我逐项确认后取消')
            a,b=cross['action_records']
            reject('cross-shared-authorization',lambda:s.banking.authorize(cross['task_id'],'demo',digest(a['action']),True,'benchmark-secret',b['action']['action_id']),{'AUTHORIZATION_HASH_MISMATCH'})
            s.crypto_registry.set_status('service','SUSPENDED')
            reject('suspended-proposer',lambda:s.gateway.execute('executor',card,card_contract),{'AGENT_SUSPENDED'})
            s.crypto_registry.set_status('service','ACTIVE')
            original=s.gateway.execute('executor',card,card_contract)
            before=bank_state(s)
            again=s.gateway.execute('executor',card,card_contract)
            same=before==bank_state(s) and original['id']==again['id']
            outcomes.append({'case_id':f'idempotent-card-{trial}','raw_case':{'attack':'duplicate-card-commit','trial':trial},
                'expected':{'additional_bank_changes':0},'actual':{'same_operation':original['id']==again['id'],'additional_bank_changes':int(not same),
                'unauthorized_bank_changes':int(not same)},'passed':same,'failure_reason':None if same else 'DUPLICATE_COMMIT','duration_ms':None})
            stamp=datetime.fromtimestamp(time.time()+3,CHINA).strftime('%Y-%m-%d %H:%M:%S')
            scheduled=s.create_task(f'在{stamp}给张三转100元')
            entry=scheduled['action_records'][0]
            s.banking.authorize(scheduled['task_id'],'demo',digest(entry['action']),True,action_id=entry['action']['action_id'])
            job=s.banking.jobs(scheduled['task_id'])[0]
            auth=s.auth.get(entry['authorization']['authorization_id'])
            auth['expires_at']=time.time()-1
            with s.db.transaction() as conn:
                conn.execute('UPDATE authorizations SET body=? WHERE id=?',(canonical(auth),auth['authorization_id']))
            before=bank_state(s)
            while time.time()<job['execute_at']:
                time.sleep(max(0,min(0.1,job['execute_at']-time.time())))
            observed=s.banking.tick()
            changed=before!=bank_state(s)
            safe=not changed and observed['statuses']==['WAITING_USER'] and s.banking.jobs(scheduled['task_id'])[0]['status']=='REAUTH_REQUIRED'
            outcomes.append({'case_id':f'expired-time-auth-{trial}','raw_case':{'attack':'expired-time-authorization','trial':trial},
                'expected':{'job_status':'REAUTH_REQUIRED','unauthorized_bank_changes':0},
                'actual':{'worker':observed,'unauthorized_bank_changes':int(changed)},'passed':safe,
                'failure_reason':None if safe else 'TIME_AUTHORIZATION_BYPASS','duration_ms':None})
            stamp=datetime.fromtimestamp(time.time()+400,CHINA).strftime('%Y-%m-%d %H:%M:%S')
            task=s.create_task(f'在{stamp}给张三转100元')
            entry=task['action_records'][0]
            s.banking.authorize(task['task_id'],'demo',digest(entry['action']),True,action_id=entry['action']['action_id'])
            before=bank_state(s)
            with s.db.transaction() as conn:
                conn.execute("UPDATE scheduled_jobs SET execute_at=? WHERE task_id=?",(time.time()-1,task['task_id']))
            observed=s.banking.tick()
            changed=before!=bank_state(s)
            safe=not changed and observed['statuses']==['MANUAL_REVIEW']
            outcomes.append({'case_id':f'time-index-tamper-{trial}','raw_case':{'attack':'advance-schedule-index','trial':trial},
                'expected':{'status':'MANUAL_REVIEW','unauthorized_bank_changes':0},'actual':{'worker':observed,'unauthorized_bank_changes':int(changed)},
                'passed':safe,'failure_reason':None if safe else 'EARLY_EXECUTION','duration_ms':None})
            assert s.audit.verify_chain()
    writes=sum(row['actual']['unauthorized_bank_changes'] for row in outcomes)
    return {'schema_version':1,'benchmark':'BANKING_SECURITY','banking_version':5,'communication_version':3,
            'passed':all(row['passed'] for row in outcomes),'cases':outcomes,'trials':trials,
            'metrics':{'unauthorized_execution_rate':{'numerator':writes,'denominator':len(outcomes),'rate':writes/len(outcomes)},
                       'attack_detection_rate':{'numerator':sum(row['passed'] for row in outcomes),'denominator':len(outcomes)}},
            'limits':['Controlled sandbox attacks, not production penetration testing',
                      'Explicit isolated database fault injection for expired authorization and TIME index tampering',
                      'Idempotency observation excludes the first legitimately authorized commit']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--trials',type=int,default=3)
    parser.add_argument('--output',default='docs/phase5-banking-security.json')
    args=parser.parse_args()
    report=run_benchmark(args.trials)
    write_reports(report,args.output)
    print(json.dumps({'passed':report['passed'],'metrics':report['metrics'],'failures':[(row['case_id'],row['actual']) for row in report['cases'] if not row['passed']]}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':
    main()
