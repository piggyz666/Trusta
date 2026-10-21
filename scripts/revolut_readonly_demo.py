"""Live official sandbox reads through the existing TRUSTA business runtime.

Never calls an LLM or bank write endpoint. Detailed bank data and runtime keys
stay beside the existing private credentials; exported results are aggregate only.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.audit_trust.checkpoints import verify_checkpoint
from backend.bank_sdk.contracts import BankRequest
from backend.bank_sdk.revolut_auth import TOKEN_PATH
from backend.config import Config
from backend.domain import Rejected
from backend.runtime.taskgraph import node
from backend.service import TrustaService
from backend.security.local_files import protect_secret,write_secret_atomic


def run(profile,month,currency,output):
    profile=Path(profile).resolve()
    settings=tomllib.loads(profile.read_text(encoding='utf-8'))
    token=Path(settings['token_path'])
    if not token.is_absolute(): token=profile.parent/token
    private=token.resolve().parent/'readonly-service'
    private.mkdir(exist_ok=True)
    protect_secret(private)
    key=private/'signing.key'
    if not key.exists():
        write_secret_atomic(key,secrets.token_bytes(32),create_only=True)
    protect_secret(key)
    if key.stat().st_size!=32:raise Rejected('INVALID_ROOT_SIGNING_KEY')
    s=TrustaService(Config(db_path=str(private/'service.sqlite3'),bank_config_path=str(profile),
        signing_key=key.read_bytes(),banking_version=5,communication_version=3),recover_workflows=False)
    provider=s.gateway._adapter.provider
    calls=Counter()
    original_send=provider.transport.send
    def bounded_send(method,path,body,headers):
        if method=='POST' and path==TOKEN_PATH: kind='OAUTH_REFRESH_POST'
        elif method=='GET' and path=='/api/1.0/accounts': kind='ACCOUNTS_GET'
        elif method=='GET' and path.startswith('/api/1.0/transactions?'): kind='TRANSACTIONS_GET'
        else: raise Rejected('DEMO_BANK_ENDPOINT_DENIED')
        calls[kind]+=1
        return original_send(method,path,body,headers)
    provider.transport.send=bounded_send
    owner=settings['customer_id']
    # Service-side discovery, not an Agent privilege: business reads below use Gateway.
    accounts=provider.request(BankRequest('accounts.read',owner)).data
    selected=next((a for a in accounts if a['currency']==currency and not a['frozen']),None)
    if selected is None: raise Rejected('DEMO_ACCOUNT_UNAVAILABLE')
    counts=lambda:{t:s.db.one('SELECT COUNT(*) AS n FROM '+t)['n'] for t in
        ('accounts','transfers','transactions','ledger_entries','actions','authorizations','execution_contracts','receipts')}
    before=counts()
    event_start=s.db.one('SELECT COALESCE(MAX(seq),0) AS n FROM audit_events')['n']
    balance=s.gateway.read('verifier',owner,'account.get_balance',account_id=selected['id'])
    bill=s.create_task('分析'+month+'消费',user_id=owner,source_account_id=selected['id'])
    if bill['status']!='COMPLETED': raise Rejected('DEMO_BILL_FAILED')
    graph={'schema_version':1,'nodes':[
        node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),
        node('Sum','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),
        node('Report','REPORT','supervisor','graph.report',['Sum'])]}
    task=s.graphs.create(owner,'Official sandbox monthly read-only graph',graph,
                        {'input:account':selected['id'],'input:month':month})
    if task['status']!='COMPLETED' or task['proposed_actions'] or task['receipts']:
        raise Rejected('DEMO_GRAPH_FAILED')
    summary=bill['result']['summary']
    if summary!=task['result']['Sum:result']: raise Rejected('DEMO_SUMMARY_MISMATCH')
    after=counts()
    if before!=after or any(after.values()): raise Rejected('DEMO_FINANCIAL_STATE_CHANGED')
    denied={}
    for capability in ('payments.create','investments.purchase','cards.freeze'):
        count=sum(calls.values())
        try: provider.request(BankRequest(capability,owner))
        except Rejected as exc: denied[capability]=exc.code
        else: raise Rejected('DEMO_WRITE_NOT_DENIED')
        if sum(calls.values())!=count: raise Rejected('DEMO_DENIAL_CONTACTED_BANK')
    audit_valid=s.audit.verify_chain()
    checkpoints=[]
    while True:
        cp=s.checkpoints.generate_checkpoint()
        if cp is None: break
        pem,status=s.checkpoints.public_key(cp)
        if not s.checkpoints.verify_checkpoint(cp) or not verify_checkpoint(cp,pem):
            raise Rejected('DEMO_CHECKPOINT_INVALID')
        checkpoints.append({'checkpoint':cp,'public_key':pem,'key_status':status})
    if not audit_valid or not s.checkpoints.verify_storage(): raise Rejected('DEMO_AUDIT_INVALID')
    kinds=Counter(json.loads(row['body'])['event_type'] for row in
                  s.db.rows('SELECT body FROM audit_events WHERE seq>?',(event_start,)))
    accepted=kinds['MESSAGE_ACCEPTED']
    if accepted==0: raise Rejected('DEMO_SIGNED_MESSAGES_MISSING')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    proof=output.with_name(output.stem+'-checkpoints.json')
    proof.write_text(json.dumps(checkpoints,indent=2),encoding='utf-8')
    report={'schema_version':1,'verified_at':datetime.now(timezone.utc).isoformat(),
        'provider':'revolut','environment':'sandbox','status':'SUCCEEDED',
        'scope':'READ_ONLY_BUSINESS_INTEGRATION','scope_returned':None,
        'account_count':len(accounts),'balance_read_verified':type(balance['balance']) is int and balance['currency']==currency,
        'month':month,'month_basis':'CREATED_AT_ASIA_SHANGHAI','summary':summary,
        'category_labels':[r['category'] for r in bill['result']['categories']],
        'absolute_cny_anomaly_threshold_applied':False,
        'bill_analysis_status':bill['status'],'taskgraph_status':task['status'],
        'summary_agreement':True,'requests':dict(calls),'refresh_count':provider.auth.refresh_count,
        'bank_business_write_calls':0,'llm_calls':0,'raw_credentials_exported':False,
        'signed_messages_accepted':accepted,'sdk_read_audit_events':kinds['BANK_SDK_READ'],
        'audit_chain_valid':audit_valid,'checkpoints_verified':len(checkpoints),
        'checkpoint_trust_source':'LOCAL_REGISTRY_PINNED_KEY','blockchain_submission':False,
        'financial_state_rows_before':before,'financial_state_rows_after':after,
        'unsupported_capabilities_rejected':denied,'frontend_default':'VIRTUAL_BANK_UNCHANGED'}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config/bank.revolut.local.toml'))
    parser.add_argument('--month',default=datetime.now().strftime('%Y-%m'))
    parser.add_argument('--currency',choices=['GBP','EUR','USD','AUD'],default='GBP')
    parser.add_argument('--output',default=str(ROOT/'docs/revolut-readonly-validation.json'))
    args=parser.parse_args()
    try: run(args.config,args.month,args.currency,args.output)
    except Rejected as exc:
        print(json.dumps({'status':'FAILED','error_code':exc.code}));sys.exit(1)
    except Exception:
        # Never dump a transport exception, credentials, raw response or private path.
        print(json.dumps({'status':'FAILED','error_code':'DEMO_LOCAL_SETUP_ERROR'}));sys.exit(1)
