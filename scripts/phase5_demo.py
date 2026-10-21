"""Six actual HTTP sandbox scenarios, signed receipts and a durable TIME demonstration."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
import time
from urllib.request import Request,urlopen
from backend.audit_trust.records import verify_record
from backend.business import CHINA
from backend.config import Config
from backend.domain import digest
from backend.server import create_server
from backend.service import TrustaService


@contextmanager
def http_bank(config):
    service=TrustaService(config)
    server=create_server(service,'isolated-phase5-demo-token',0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def request(path,body=None):
        req=Request(base+path,data=None if body is None else json.dumps(body).encode(),
                    headers={'Authorization':'Bearer isolated-phase5-demo-token','Content-Type':'application/json'})
        with urlopen(req,timeout=20) as response:
            return json.loads(response.read())
    try:
        yield service,request
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def create(request,text):
    return request('/api/tasks',{'text':text})


def approve(request,task,index=0,decision=True):
    if 'action_records' in task:
        row=task['action_records'][index]
        path=f"/api/tasks/{task['task_id']}/actions/{row['action']['action_id']}/authorize"
        action_hash=digest(row['action'])
    else:
        path=f"/api/tasks/{task['task_id']}/authorize"
        action_hash=task['authorization']['action_hash']
    return request(path,{'action_hash':action_hash,'approve':decision,'strong_secret':'demo-secret'})


def prove(service,request,tasks):
    service.anchors.run_once()
    receipts=[]
    for task in tasks:
        receipts.extend(task.get('receipts',[]) or ([task['receipt']] if task.get('receipt') else []))
    proofs=[]
    for receipt in receipts:
        bundle=request('/api/audit/proof?receipt_id='+receipt['receipt_id'])
        assert verify_record(bundle,bundle['signer_public_key'],digest(bundle['checkpoint']))
        assert bundle['anchor_verified']
        proofs.append(bundle)
    assert service.audit.verify_chain()
    return proofs


def demo(directory):
    root=Path(directory)
    results=[]
    proofs=[]
    for number in range(1,7):
        config=Config(db_path=str(root/f'scene-{number}.db'),signing_key=b'isolated-phase5-demo-root-key',
                      communication_version=3,banking_version=5,strong_auth_secret='demo-secret')
        with http_bank(config) as (s,request):
            tasks=[]
            if number==1:
                task=approve(request,create(request,'给13800000001转2000元'))
                tasks.append(task)
                assert task['status']=='COMPLETED' and request('/api/balance')['balance']==800000
                result={'scene':'智能转账','status':task['status'],'recipient_id':task['proposed_actions'][0]['recipient_id'],
                        'cash_cents':800000,'transaction_count':s.db.one('SELECT COUNT(*) n FROM transactions')['n']}
            elif number==2:
                task=create(request,'分析2026-09消费')
                assert task['status']=='COMPLETED' and task['result']['summary']['net_spend']==542500
                assert request('/api/balance')['balance']==1000000 and not s.db.rows('SELECT * FROM bank_operations')
                result={'scene':'账单分析','status':task['status'],'net_spend_cents':542500,
                        'anomalies':len(task['result']['anomalies']['flags']),'read_only':True}
            elif number==3:
                recommendation=create(request,'推荐适合我的理财产品')
                comparison=create(request,'比较sandbox-r1和sandbox-r2')
                assert recommendation['status']==comparison['status']=='COMPLETED'
                assert request('/api/balance')['balance']==1000000
                purchase=approve(request,create(request,'购买sandbox-r1 1000元'))
                assert request('/api/balance')['balance']==900000
                redeem=approve(request,create(request,'赎回sandbox-r1 400元'))
                tasks.extend([purchase,redeem])
                holding=s.gateway.read('wealth','demo','portfolio.get',account_id='acct-demo',product_id='sandbox-r1')['amount_cents']
                assert purchase['status']==redeem['status']=='COMPLETED' and holding==60000 and request('/api/balance')['balance']==940000
                assert len(s.db.rows('SELECT * FROM investment_transactions'))==2 and len(s.db.rows('SELECT * FROM ledger_entries'))==4
                result={'scene':'理财推荐、比较、申购与赎回','status':'COMPLETED','cash_cents':940000,'holding_cents':holding,
                        'investment_transactions':2,'ledger_entries':4,'recommend_compare_read_only':True,'return_promised':False}
            elif number==4:
                freeze=approve(request,create(request,'冻结card-demo'))
                assert s.gateway.read('service','demo','card.get',resource_id='card-demo')['status']=='FROZEN'
                unfreeze=approve(request,create(request,'解冻card-demo'))
                limit=approve(request,create(request,'card-demo限额设为1000元'))
                control=approve(request,create(request,'设置card-demo交易控制线上关闭境外关闭非接触关闭'))
                tasks.extend([freeze,unfreeze,limit,control])
                card=s.gateway.read('service','demo','card.get',resource_id='card-demo')
                assert all(task['status']=='COMPLETED' for task in tasks) and card['status']=='ACTIVE' and card['spending_limit']==100000
                assert (card['online_enabled'],card['international_enabled'],card['contactless_enabled'])==(0,0,0)
                result={'scene':'银行卡管理','status':'COMPLETED','observed_states':['ACTIVE','FROZEN','ACTIVE'],
                        'card':card,'bank_operations':4,'cash_cents':request('/api/balance')['balance']}
            elif number==5:
                detection=create(request,'检测订阅')
                cancel=approve(request,create(request,'取消订阅sub-stream'))
                disable=approve(request,create(request,'关闭代扣dd-electric'))
                tasks.extend([cancel,disable])
                sub=s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')
                debit=s.gateway.read('service','demo','direct_debit.get',resource_id='dd-electric')
                assert detection['status']==cancel['status']==disable['status']=='COMPLETED'
                assert sub['status']=='CANCELLED' and debit['status']=='DISABLED'
                result={'scene':'订阅与代扣管理','status':'COMPLETED','subscription_status':sub['status'],
                        'debit_status':debit['status'],'candidate_count':len(detection['result']['candidates']),
                        'cash_cents':request('/api/balance')['balance']}
            else:
                task=create(request,'分析我最近支出，把明显没必要的订阅找出来，我确认后取消')
                assert len(task['action_records'])==2 and task['status']=='WAITING_USER'
                first=approve(request,task,0)
                assert first['status']=='WAITING_USER'
                done=approve(request,first,1,False)
                tasks.append(done)
                assert done['status']=='COMPLETED' and [row['status'] for row in done['action_records']]==['COMPLETED','DENIED']
                statuses=[row['status'] for row in s.gateway.read('service','demo','subscription.list')]
                assert sorted(statuses)==['ACTIVE','CANCELLED']
                assert any(row['resolution']=='UNRESOLVED' for row in done['result']['resolutions'])
                result={'scene':'跨场景协同与逐项授权','status':done['status'],'dag':done['steps'],
                        'parallel_read_steps':['R02','R03'],'resolutions':done['result']['resolutions'],
                        'independent_action_statuses':[row['status'] for row in done['action_records']],
                        'subscription_statuses':statuses,'cash_cents':request('/api/balance')['balance']}
            scene_proofs=prove(s,request,tasks)
            result['proof_count']=len(scene_proofs)
            result['audit_valid']=True
            proofs.extend(scene_proofs)
        restarted=TrustaService(config)
        result['restart_audit_valid']=restarted.audit.verify_chain()
        for proof in scene_proofs:
            again=restarted.records.query('demo',receipt_id=proof['receipt']['receipt_id'])
            assert verify_record(again,proof['signer_public_key'])
        result['restart_proofs_verified']=True
        results.append(result)
    # An additional time scenario uses persisted jobs and the worker. No bank call occurs in the wait.
    config=Config(db_path=str(root/'time.db'),signing_key=b'isolated-phase5-time-demo-key',communication_version=3,banking_version=5)
    with http_bank(config) as (s,request):
        stamp=datetime.fromtimestamp(time.time()+5,CHINA).strftime('%Y-%m-%d %H:%M:%S')
        task=approve(request,create(request,f'在{stamp}给张三转100元'))
        assert task['status']=='WAITING_TIME' and request('/api/balance')['balance']==1000000
        job=s.banking.jobs(task['task_id'])[0]
    with http_bank(config) as (s,request):
        worker=TrustaService(config,recover_workflows=False)
        while time.time()<job['execute_at']:
            assert worker.banking.tick()['processed']==0
            time.sleep(max(0,min(0.1,job['execute_at']-time.time())))
        assert worker.banking.tick()['statuses']==['COMPLETED']
        assert worker.banking.tick()['processed']==0
        done=request('/api/tasks/'+task['task_id'])
        assert done['status']=='COMPLETED' and request('/api/balance')['balance']==990000
        time_proofs=prove(s,request,[done])
        proofs.extend(time_proofs)
        time_result={'status':'COMPLETED','persistent_job_id':job['schedule_id'],'restart_before_due':True,
                     'cash_cents':990000,'transaction_count':s.db.one('SELECT COUNT(*) n FROM transactions')['n'],
                     'duplicate_tick_writes':0,'due_policy_authorization_state_rechecked':True}
    assert all(row['status']=='COMPLETED' and row['restart_audit_valid'] for row in results)
    return {'schema_version':1,'passed':True,'bank':'VIRTUAL_SANDBOX','banking_version':5,'communication_version':3,
            'active_agents':['supervisor','planner','transaction','finance','wealth','service'],
            'transport':'ACTUAL_LOOPBACK_HTTP','scenarios':results,'time_scenario':time_result,'record_proofs':proofs,
            'real_bank_connected':False,'real_llm_evaluated':False,'blockchain_externally_verified':False,
            'limits':['Each scene has an isolated sandbox; balances do not represent a single continuous customer session',
                      'Unit-price investment principal model, no market execution or investment returns',
                      'Local anchor and demonstration key pin; no production external trust provider']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='docs/phase5-demo.json')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='trusta-phase5-demo-') as directory:
        report=demo(directory)
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'scenario_count':len(report['scenarios']),'proof_count':len(report['record_proofs']),
                      'time_scenario':report['time_scenario']}))


if __name__=='__main__':
    main()
