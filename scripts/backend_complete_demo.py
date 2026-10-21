"""Six-scenario + genuine crypto backend demo. Isolated DB, no live money/API calls."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from backend.config import Config
from backend.domain import digest
from backend.service import TrustaService
from backend.security.banking_controls import hotp
from backend.audit_trust.records import verify_record

ROOT=Path(__file__).resolve().parents[1]


def run():
    assets=ROOT/'data'/'zkp-dev-v1'
    with tempfile.TemporaryDirectory() as directory:
        s=TrustaService(Config(db_path=str(Path(directory)/'demo.db'),signing_key=b'isolated-full-backend-demo-root',
                              communication_version=3,banking_version=5,safety_profile='competition',did_enabled=True,
                              zkp_enabled=True,zkp_artifact_dir=str(assets),
                              zkp_manifest_sha256=hashlib.sha256((assets/'manifest.json').read_bytes()).hexdigest()))
        counter=int(time.time()//30)
        with patch('backend.security.banking_controls.time.time',return_value=counter*30):
            seed=base64.b32decode(s.safety.begin_enrollment('demo')['secret'])
            s.safety.confirm_enrollment('demo',hotp(seed,counter))

        def approve(task):
            nonlocal counter
            records=task.get('action_records') or [{'action':task['proposed_actions'][0],'authorization':task['authorization'],'status':'WAITING_USER'}]
            for entry in records:
                if entry['status']!='WAITING_USER': continue
                if entry['authorization']['auth_method']=='totp':
                    # Slow proof generation or a minute boundary must not move
                    # the synthetic authorization clock behind proposal creation.
                    counter=max(counter+1,int(max(time.time(),entry['action']['created_at'])//30)+1)
                    with patch('backend.security.banking_controls.time.time',return_value=counter*30):
                        task=s.authorize(task['task_id'],'demo',digest(entry['action']),True,hotp(seed,counter))
                else:task=s.authorize(task['task_id'],'demo',digest(entry['action']),True)
            assert task['status']=='COMPLETED',task['errors']
            return task

        def summary(task):
            return {'task_id':task['task_id'],'task_type':task['task_type'],'status':task['status'],
                    'receipt_count':len(task.get('receipts',[])) if task.get('action_records') else int(bool(task.get('receipt')))}

        pending=s.create_task('给张三转 600 元')
        did=s.did.export('transaction');assert s.did.verify(did)
        proof=s.privacy.prove(pending['task_id'],'demo',digest(pending['proposed_actions'][0]))
        privacy=s.privacy.verify('demo',proof)
        assert s.get_task(pending['task_id'])['status']=='WAITING_USER'
        transfer=approve(pending)
        elevated=s.create_task('给张三转 500 元')
        assert elevated['authorization']['auth_method']=='totp'
        transfer2=approve(elevated)
        annual=s.create_task('分析2026年度账单报告');assert annual['status']=='COMPLETED'
        recommendation=s.create_task('推荐适合我的理财产品');assert recommendation['status']=='COMPLETED'
        compare=s.create_task('比较sandbox-r1和sandbox-r2');assert compare['status']=='COMPLETED'
        buy=approve(s.create_task('购买sandbox-r1 100元'))
        redeem=approve(s.create_task('赎回sandbox-r1 100元'))
        application=approve(s.create_task('申请虚拟借记卡 acct-demo'))
        freeze=approve(s.create_task('冻结card-demo'))
        loss=approve(s.create_task('挂失card-demo'))
        reminders=s.events.reminders('demo','acct-demo')
        subscription=approve(s.create_task('取消订阅sub-stream'))
        debit=approve(s.create_task('关闭代扣dd-electric'))
        after=s.events.reminders('demo','acct-demo')
        due=time.time()+5
        event=s.events.create('demo','birthday-demo','acct-demo','BIRTHDAY_TRANSFER',due,{'recipient_name':'张三','amount':100})
        with patch('backend.runtime.event_plans.time.time',return_value=due+1):s.events.tick('demo')
        event=s.events.get(event['id'],'demo')
        proposal=s.get_task(event['task_id']);assert proposal['status']=='WAITING_USER'
        birthday=approve(proposal)
        cross=s.create_task('分析2026-09消费，找出可能的订阅，我逐项确认后取消')
        if cross['status']=='WAITING_USER':cross=approve(cross)
        assert cross['status']=='COMPLETED'
        assert s.audit.verify_chain()
        # Trust the locally provisioned audit key independently of proof-supplied data.
        audit_public=s.crypto_registry.resolve('audit-checkpoint')['public_key']
        s.anchors.run_once(max_events=4096)
        record=s.records.query('demo',receipt_id=transfer['receipt']['receipt_id'])
        merkle_verified=verify_record(record,audit_public,digest(record['checkpoint']))
        local_anchor=s.anchors.verify_anchor(record['checkpoint']['checkpoint_id'])
        assert merkle_verified and local_anchor
        return {'mode':'ISOLATED_VIRTUAL_BANK','external_bank_calls':0,'external_llm_calls':0,'production_funds':False,
                'scenarios':[
                    {'name':'智能转账','cases':[summary(transfer),summary(transfer2)],'daily_limit_upgrade':'TOTP'},
                    {'name':'账单分析','cases':[summary(annual)],'months':len(annual['result']['months']),'coverage':annual['result']['coverage']},
                    {'name':'理财操作','cases':[summary(t) for t in (recommendation,compare,buy,redeem)],'provider':'LOCAL_SANDBOX'},
                    {'name':'卡片管理','cases':[summary(t) for t in (application,freeze,loss)],'application_status':'SUBMITTED','lost_state':'TERMINAL'},
                    {'name':'订阅/代扣','cases':[summary(t) for t in (subscription,debit)],'reminders_before':len(reminders['reminders']),'reminders_after':len(after['reminders']),'notification':'LOCAL_QUEUE_ONLY'},
                    {'name':'跨场景联动','cases':[summary(birthday),summary(cross)],'event_requires_new_confirmation':True}],
                'crypto':{'did':{'method':'did:key','binding_verified':True},'zkp':privacy,
                          'proof_public_signal_count':len(proof['public_signals']),'setup':s.privacy.status(),
                          'merkle_record_independently_verified':merkle_verified,
                          'local_anchor_verified':local_anchor,'local_anchor_is_blockchain':False,
                          'external_sepolia_verified':False},'audit_chain_verified':True}


if __name__=='__main__':
    report=run()
    (ROOT/'docs'/'backend-complete-demo.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'six_scenarios':len(report['scenarios']),'real_zkp_verified':report['crypto']['zkp']['verified'],
                      'audit_chain_verified':report['audit_chain_verified'],'external_bank_calls':0}))
