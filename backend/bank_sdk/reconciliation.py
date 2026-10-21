"""Explicit read-only review of an already authorised external transfer.

Terminal Scheduler history stays frozen. A verified late settlement is recorded
as a supplementary reconciliation/receipt, never as a new execution attempt.
"""
import hmac
import json
from backend.domain import Rejected,digest


def reconcile(service,task_id,user_id):
    from backend.runtime.graph_claims import GraphClaims
    with GraphClaims(service).hold('reconcile:'+task_id,recover_metadata=True):
        return _reconcile(service,task_id,user_id)

def _reconcile(service,task_id,user_id):
    with service.lock:
        task=service._owned_task(task_id,user_id)
        provider=service.gateway._adapter.provider
        if task['task_type']!='TRANSFER' or provider.manifest.provider!='revolut' or not hasattr(provider,'observe_submission'):
            raise Rejected('RECONCILIATION_UNAVAILABLE')
        if task['status']=='COMPLETED' or task.get('reconciliation',{}).get('status')=='VERIFIED':return task
        if task['status']!='MANUAL_REVIEW' or len(task['proposed_actions'])!=1:raise Rejected('RECONCILIATION_NOT_REQUIRED')
        action=task['proposed_actions'][0];hashed=digest(action)
        row=service.db.one("SELECT body FROM execution_contracts WHERE json_extract(body,'$.action_id')=? AND json_extract(body,'$.approved_action_hash')=? ORDER BY json_extract(body,'$.issued_at') DESC LIMIT 1",(action['action_id'],hashed))
        if not row:raise Rejected('MISSING_CONTRACT')
        contract=json.loads(row['body']);auth=service.auth.get(contract['authorization_id'])
        operation=provider._operation(key=action['idempotency_key'])
        if not operation:raise Rejected('BANK_SUBMISSION_NOT_CLAIMED')
        observation=json.loads(operation['body'])
        if (not hmac.compare_digest(service.contracts._seal(contract),contract.get('contract_hash',''))
                or contract['idempotency_key']!=action['idempotency_key'] or auth['action_hash']!=hashed
                or auth['user_id']!=user_id or auth['status']!='AUTHORIZED' or operation['hash']!=hashed
                or not contract['issued_at']<=observation['submitted_at']<contract['expires_at']):
            raise Rejected('RECONCILIATION_AUTHORITY_MISMATCH')
        try:
            bank_id=provider.observe_submission(action)
            verification=service.verifier.verify(action,bank_id)
        except Rejected as exc:
            verification={'status':'MANUAL_REVIEW','reason':exc.code,'provider_status':'UNKNOWN'}
            bank_id=None
        result={'status':verification['status'],'verification':verification,'financial_write':False,'receipt':None}
        if verification['status']=='VERIFIED':
            result['receipt']=service.receipts.create(action,contract,{'id':bank_id},verification)
        audit_ref=service.audit.append(task_id,action['action_id'],'BANK_RECONCILIATION_RECORDED','verifier',result)
        # Do not change MANUAL_REVIEW terminal steps or issue a fresh contract.
        return service.blackboard.update(task_id,'runtime',reconciliation=result,audit_refs=task['audit_refs']+[audit_ref])
