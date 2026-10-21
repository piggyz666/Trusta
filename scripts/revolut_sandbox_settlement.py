"""Operator-only official Sandbox settlement fixture, outside business Agents.

Advances an existing Core-authorised 0.01 transfer, never creates a payment.
Not registered as an Agent capability, Gateway tool or public HTTP endpoint.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hmac
import json
import os
import sys
import time
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.domain import Rejected,canonical,digest
from backend.bank_sdk.revolut import identifier
from backend.bank_sdk.revolut_auth import SANDBOX,credential_lock
from backend.bank_sdk.contracts import BankError
from scripts.revolut_server import build


def prior_rejection(service,action,bank_id,target,report):
    """Bind a known 403 to the historical claim and its audit payload hash.

    The first live report predates HTTP-status reporting; its original payload
    is reconstructed only for that exact legacy format, without rewriting it.
    """
    try:
        claim=json.loads(target.read_text(encoding='utf-8'))
        if (claim['task_id']!=action['task_id'] or claim['bank_transaction_id']!=bank_id
                or claim['approved_action_hash']!=digest(action) or claim['status']!='CLAIMED'
                or not service.audit.verify_chain() or report['error']!='BANK_SCOPE_DENIED'
                or report['http_status']!=403 or report['observed_state']!='SIMULATION_REJECTED'):
            raise ValueError()
        keys=('task_id','bank_transaction_id','amount_minor_units','currency','mode',
              'simulation_post_calls','payment_post_calls','production_bank_write_calls','observed_state','error')
        payload={key:report[key] for key in keys}
        if (payload['task_id']!=action['task_id'] or payload['bank_transaction_id']!=bank_id
                or payload['amount_minor_units']!=action['amount'] or payload['currency']!=action['currency']
                or payload['simulation_post_calls']!=1 or payload['payment_post_calls']!=0
                or payload['production_bank_write_calls']!=0):raise ValueError()
        if (claim['mode']=='OFFICIAL_SANDBOX_SIMULATED_SETTLEMENT'
                and report.get('previous_report_observed_state')=='SIMULATION_RESPONSE_UNCERTAIN'):
            payload.update(mode=claim['mode'],observed_state='SIMULATION_RESPONSE_UNCERTAIN')
        else:
            if payload['mode']!=claim['mode']:raise ValueError()
            payload['http_status']=403
        events=[json.loads(row['body']) for row in service.db.rows('SELECT body FROM audit_events ORDER BY seq')]
        matches=[event for event in events if event['task_id']==action['task_id']
            and event['action_id']==action['action_id'] and event['actor']=='sandbox_test_operator'
            and event['event_type']=='SANDBOX_SIMULATION_OBSERVED' and event['payload_hash']==digest(payload)
            and event['timestamp']>=claim['claimed_at']]
        if len(matches)!=1:raise ValueError()
        return matches[0]
    except (KeyError,TypeError,ValueError,OSError):
        raise Rejected('SIMULATION_PRIOR_REJECTION_NOT_PROVEN') from None


def operator_token(provider,after):
    """Load only the separately consented, still-valid operator token.

    No refresh, business-auth replacement, or management endpoint is exposed.
    Missing bank scope is recorded as unknown; simulator access must be tested.
    """
    try:
        target=Path(provider.auth.key_path).resolve().parent/'oauth-simulation-tokens.local.json'
        with credential_lock(target):row=json.loads(target.read_text(encoding='utf-8'))
        if (row.get('client_id')!=provider.auth.client_id or row.get('issuer')!='trusta.localhost'
                or row.get('sandbox_only') is not True or row.get('operator_simulation_only') is not True
                or row.get('scope_requested')!='READ,WRITE' or row.get('token_type','').lower()!='bearer'):
            raise ValueError()
        if 'scope' in row and (not isinstance(row['scope'],str)
                or set(row['scope'].replace(',',' ').split())!={'READ','WRITE'}):raise ValueError()
        token=row['access_token'];issued=datetime.fromisoformat(row['obtained_at'].replace('Z','+00:00'))
        ttl=row['expires_in'];now=time.time()
        if (not isinstance(token,str) or not token or len(token)>8192 or any(c.isspace() for c in token)
                or issued.tzinfo is None or type(ttl) is not int or not 0<ttl<=86400
                or not after<issued.timestamp()<=now+5 or issued.timestamp()+ttl<=now+30):raise ValueError()
        return token,issued.isoformat(),row.get('scope')
    except (KeyError,TypeError,ValueError,OSError,AttributeError):
        raise Rejected('SIMULATION_FRESH_OPERATOR_CREDENTIAL_REQUIRED') from None


def save_report(path,report):
    """A subsequent read must not erase an earlier bank mutation observation."""
    path=Path(path)
    saved=report
    if path.exists() and report.get('simulation_post_calls',0)==0:
        prior=json.loads(path.read_text(encoding='utf-8'))
        if prior.get('simulation_post_calls',0)>0:
            prior['latest_read_only_observation']=report;saved=prior
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def advance(service,task_id,claim_directory,*,operator_retry=False,rejection_report=None,check_only=False):
    task=service._owned_task(task_id,'demo');p=service.gateway._adapter.provider
    if (p.manifest.provider!='revolut' or p.manifest.environment!='sandbox'
            or p.transport.base_url!=SANDBOX or task['task_type']!='TRANSFER'
            or task['status'] not in {'MANUAL_REVIEW','COMPLETED'} or len(task['proposed_actions'])!=1):
        raise Rejected('SANDBOX_SIMULATION_DENIED')
    a=task['proposed_actions'][0];hashed=digest(a)
    if a['amount']!=1 or a['currency'] not in {'EUR','GBP'}:raise Rejected('SANDBOX_SIMULATION_DENIED')
    op=p._operation(key=a['idempotency_key'])
    if not op or not op['bank_id'] or op['hash']!=hashed:raise Rejected('BANK_SUBMISSION_REQUIRED')
    saved=json.loads(op['body'])
    contract_row=service.db.one("SELECT body FROM execution_contracts WHERE json_extract(body,'$.action_id')=? AND json_extract(body,'$.approved_action_hash')=? ORDER BY json_extract(body,'$.issued_at') DESC LIMIT 1",(a['action_id'],hashed))
    if not contract_row:raise Rejected('MISSING_CONTRACT')
    contract=json.loads(contract_row['body']);auth=service.auth.get(contract['authorization_id'])
    if (not hmac.compare_digest(service.contracts._seal(contract),contract.get('contract_hash',''))
            or contract['idempotency_key']!=a['idempotency_key'] or auth['status']!='AUTHORIZED'
            or auth['user_id']!='demo' or auth['action_hash']!=hashed
            or not contract['issued_at']<=saved['submitted_at']<contract['expires_at']):
        raise Rejected('SIMULATION_AUTHORITY_MISMATCH')
    bank_id=identifier(op['bank_id']);row=p._status(bank_id);e=row['evidence']
    if (row['amount']!=1 or row['currency']!=a['currency'] or row['recipient_id']!=a['recipient_id']
            or e['request_id']!=a['idempotency_key'] or e['reference']!=saved['reference']
            or e['bank_type']!='transfer' or e['fee']!=0
            or not saved['submitted_at']-30<=e['created_at']<=saved['deadline']+5):
        raise Rejected('SIMULATION_BANK_RECORD_MISMATCH')
    result={'task_id':task_id,'bank_transaction_id':bank_id,'amount_minor_units':1,'currency':a['currency'],
            'mode':'OFFICIAL_SANDBOX_SETTLEMENT_CONTROL','simulation_post_calls':0,'payment_post_calls':0,
            'production_bank_write_calls':0,'observed_state':row['status']}
    if row['status']=='SETTLED':return result
    if row['status']!='PENDING':raise Rejected('SIMULATION_PENDING_REQUIRED')
    # A permanent exclusive claim prevents retries after a lost simulator response.
    # Completion is a test-control operation, not an Agent financial capability.
    target=Path(claim_directory)/('simulation-'+bank_id+'.json')
    claim={'task_id':task_id,'bank_transaction_id':bank_id,'approved_action_hash':hashed,
           'status':'CLAIMED','mode':result['mode'],'claimed_at':time.time()}
    if operator_retry:
        if Path(claim_directory).resolve()!=Path(service.db.path).resolve().parent:
            raise Rejected('SIMULATION_CLAIM_DIRECTORY_MISMATCH')
        event=prior_rejection(service,a,bank_id,target,rejection_report)
        token,issued,scope=operator_token(p,event['timestamp'])
        result.update(credential_purpose='OPERATOR_ONLY_READ_WRITE_TRIAL',
                      prior_rejection_audit_ref=event['audit_event_id'],bank_reported_scope=scope)
        claim.update(prior_rejection_audit_ref=event['audit_event_id'],credential_obtained_at=issued)
        # Preserve the original rejection claim. One exclusive trial claim
        # prevents an uncertain response or a later grant from enabling retries.
        target=Path(claim_directory)/('simulation-'+bank_id+'-operator.json')
    else:
        token=p.auth.access_token()
    if check_only:
        result['observed_state']='SIMULATION_PREFLIGHT_READY';return result
    try:
        with target.open('x',encoding='utf-8') as stream:
            stream.write(canonical(claim));stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        result['observed_state']='SIMULATION_ALREADY_CLAIMED_READ_ONLY_REVIEW_REQUIRED';return result
    service.audit.append(task_id,a['action_id'],'SANDBOX_SIMULATION_CLAIMED','sandbox_test_operator',claim)
    try:
        response=p.transport.send('POST','/api/1.0/sandbox/transactions/'+bank_id+'/complete',b'',
            {'Authorization':'Bearer '+token,'Accept':'application/json'})
        if identifier(response['id'])!=bank_id or response['state']!='completed':raise ValueError()
        result['observed_state']='SIMULATOR_REPORTED_COMPLETED'
    except Exception as exc:
        status=exc.http_status if isinstance(exc,BankError) else None
        result['observed_state']=('SIMULATION_REJECTED' if status is not None
            and 400<=status<500 and status!=408 else 'SIMULATION_RESPONSE_UNCERTAIN')
        result['error']=exc.code if isinstance(exc,Rejected) else 'SIMULATION_RESPONSE_UNCERTAIN'
        if status is not None:result['http_status']=status
    result['simulation_post_calls']=1
    service.audit.append(task_id,a['action_id'],'SANDBOX_SIMULATION_OBSERVED','sandbox_test_operator',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--complete-task',required=True)
    parser.add_argument('--config',default=str(ROOT/'config/bank.revolut-transfer.local.toml'))
    parser.add_argument('--ui-port',type=int,default=8002)
    parser.add_argument('--operator-retry',action='store_true',help='One explicit trial after audited 403 and fresh, separately consented READ,WRITE credentials. Never resends payment.')
    parser.add_argument('--check-only',action='store_true',help='Read-only preflight; no simulation claim or POST.')
    args=parser.parse_args()
    s,ui_token=build(args.config)
    claim_directory=Path(s.db.path).parent
    try:
        prior=None
        if args.operator_retry:
            prior=json.loads((ROOT/'docs/revolut-sandbox-settlement-validation.json').read_text(encoding='utf-8'))
        report=advance(s,identifier(args.complete_task),claim_directory,
                       operator_retry=args.operator_retry,rejection_report=prior,check_only=args.check_only)
        # Reconciliation runs in the original UI service's lock; no duplicate
        # receipt writer, financial execution, fresh contract or authorization.
        req=Request(f'http://127.0.0.1:{args.ui_port}/api/tasks/{args.complete_task}/reconcile',data=b'{}',
            headers={'Authorization':'Bearer '+ui_token,'Content-Type':'application/json'})
        with urlopen(req,timeout=30) as response:task=json.loads(response.read())
        recon=task.get('reconciliation',{})
        receipt=task.get('receipt') or recon.get('receipt')
        report.update(task_status=task['status'],verification_status=(recon.get('verification') or task.get('verification') or {}).get('status'),
            receipt_present=bool(receipt),receipt_id=receipt['receipt_id'] if receipt else None,
            audit_chain_valid=s.audit.verify_chain(),verified_at=datetime.now(timezone.utc).isoformat())
    except Rejected as exc:
        report={'status':'NOT_COMPLETED','reason':exc.code,'production_bank_write_calls':0}
    report_name=('revolut-sandbox-settlement-preflight.json' if args.check_only else
                 'revolut-sandbox-operator-settlement-validation.json' if args.operator_retry else
                 'revolut-sandbox-settlement-validation.json')
    save_report(ROOT/'docs'/report_name,report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
