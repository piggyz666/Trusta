"""Real local workflow/audit evidence, zero remote bank or model requests."""
import copy,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.phase6_helpers import service
from backend.domain import digest
from backend.llm.subscription_workflow import SubscriptionBudgetDialogue
from backend.runtime.handoff_evidence import verify_bundle
from backend.audit_trust.records import verify_record

def main():
    output=ROOT/'docs/backend-final-hardening'
    with tempfile.TemporaryDirectory(prefix='trusta-hardening-demo-') as directory:
        s=service(directory);d=SubscriptionBudgetDialogue(s)
        goal='分析最近三个月的支出，找出不需要的订阅，列出可以取消的项目，等我确认后取消，再根据节省的预算制定下个月的理财计划。'
        a=d.start('demo',goal,'acct-demo');body=a['context']['subscription_dialogue']['body']
        b=d.select('demo',a['task_id'],a['revision'],'取消演示视频，下个月基础预算一百元')
        pending={'status':b['status'],'budget_started':'Budget:result' in b['graph_state']['outputs'],
                 'subscription_status':s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')['status']}
        s=service(directory)
        b=s.graphs.authorize(b['task_id'],'demo','Cancel',digest(b['graph_state']['actions']['Cancel']['action']),True)
        bundle=s.handoffs.export(b['task_id'],'demo');registry=s.crypto_registry.snapshot()
        keys={r['key_id']:r for r in registry['keys']};grants={r['agent_id']:r for r in registry['agents']}
        assert verify_bundle(bundle,keys,grants)
        proof=s.records.query('demo',receipt_id=b['receipts'][0]['receipt_id'])
        pem,_=s.checkpoints.public_key(proof['checkpoint']);assert verify_record(proof,pem)
        bad=copy.deepcopy(proof);bad['checkpoint']['merkle_root']='0'*64
        assert not verify_record(bad,pem)
        report={'status':'LOCAL_VERIFIED','raw_goal':goal,'analysis_months':body['months'],
            'choices':[{'id':r['id'],'merchant':r['merchant'],'amount':r['amount']} for r in body['choices']],
            'source_task':a['task_id'],'child_task':b['task_id'],'before_authorization':pending,
            'after_restart_and_authorization':{'status':b['status'],'budget':b['graph_state']['outputs']['Budget:result'],'receipts':b['receipts']},
            'handoff_verified':True,'receipt_merkle_verified':True,'tampered_root_rejected':True,
            'checkpoint_hash':digest(proof['checkpoint']),'business_merkle_root':proof['checkpoint']['merkle_root'],
            'checkpoint_origin':'Actual local trusted subscription cancellation audit, not a random root',
            'bank_writes_scope':'Virtual Bank subscription state only; no balance or remote bank writes',
            'bank_calls_external':0,'llm_requests_external':0,'wallet_signatures':0,'chain_broadcasts':0,'external_blockchain_verified':False}
        for name,value in [('subscription-budget-demo.json',report),('workflow-handoff.json',bundle),
                           ('workflow-trusted-registry.json',registry),('workflow-record-proof.json',proof),
                           ('workflow-checkpoint.json',proof['checkpoint'])]:
            (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (output/'workflow-checkpoint-public.pem').write_text(pem,encoding='ascii')
        print(json.dumps({'status':report['status'],'budget':report['after_restart_and_authorization']['budget'],
                          'merkle_verified':True,'external_blockchain_verified':False}))

if __name__=='__main__':main()
