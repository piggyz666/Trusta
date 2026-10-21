"""Persistent collection, with participant-owned ordinary transfer tasks."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import time
import threading
import hmac
import hashlib
from contextlib import contextmanager
from functools import wraps
from backend.domain import Rejected, uid, digest, canonical


def bill_claim(method):
    @wraps(method)
    def guarded(self, bill_id, *args, **kwargs):
        with self.s.lock, self._claim(bill_id):
            return method(self, bill_id, *args, **kwargs)
    return guarded

@dataclass(frozen=True)
class SplitBill:
    id: str
    organizer: str
    account_id: str
    total_amount: int
    expires_at: float
    status: str = 'DRAFT'

@dataclass(frozen=True)
class SplitParticipant:
    user_id: str
    amount: int
    status: str = 'PENDING'

@dataclass(frozen=True)
class PaymentRequest:
    id: str
    bill_id: str
    user_id: str
    amount: int

@dataclass(frozen=True)
class PaymentAction:
    request_id: str
    task_id: str
    action_id: str
    action_hash: str

def allocate(total, users, method='EQUAL', values=None):
    if type(total) is not int or not 0<total<=10**12 or not 2<=len(users)<=20 or len(set(users))!=len(users) or any(not isinstance(u,str) or not u for u in users):
        raise Rejected('INVALID_SPLIT')
    if method=='EQUAL':
        base,remainder=divmod(total,len(users));shares=[base+int(i<remainder) for i in range(len(users))]
    elif method=='EXACT_AMOUNT':
        if not isinstance(values,list) or len(values)!=len(users) or any(type(v) is not int or v<0 for v in values):
            raise Rejected('INVALID_SPLIT_AMOUNTS')
        shares=values[:]
    elif method=='PERCENTAGE':
        try:
            if not isinstance(values,list) or len(values)!=len(users) or any(not isinstance(v,str) or len(v)>16 for v in values): raise ValueError()
            percents=[Decimal(v) for v in values]
            if any(not v.is_finite() or v<0 or v>100 or v!=v.quantize(Decimal('0.0001')) for v in percents) or sum(percents)!=100: raise ValueError()
            raw=[Decimal(total)*v/100 for v in percents];shares=[int(v) for v in raw]
            order=sorted(range(len(users)),key=lambda i:(-(raw[i]-shares[i]),i))
            for i in order[:total-sum(shares)]: shares[i]+=1
        except (ValueError,InvalidOperation): raise Rejected('INVALID_SPLIT_PERCENTAGES') from None
    else: raise Rejected('INVALID_SPLIT_METHOD')
    if sum(shares)!=total: raise Rejected('SPLIT_TOTAL_MISMATCH')
    return dict(zip(users,shares))

class SplitCollection:
    def __init__(self, service):
        self.s=service
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(service)
        self._held=threading.local()
        self._state_key=hmac.digest(service.config.signing_key,b'trusta-split-state-v1','sha256')
        with service.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS split_bills(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS split_payment_requests(id TEXT PRIMARY KEY,bill_id TEXT NOT NULL,user_id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(bill_id,user_id))')

    @contextmanager
    def _claim(self, bill_id):
        # Reentrant only in this controller/thread, never another Service.
        held: set[str]=getattr(self._held,'bills',set())
        if bill_id in held:
            yield
            return
        with self.claims.hold('split:'+bill_id,recover_metadata=True):
            self._held.bills=held|{bill_id}
            try: yield
            finally: self._held.bills=held

    def _save(self, bill, event, actor):
        bill['_seal']=self._seal(bill)
        with self.s.db.transaction() as conn:
            conn.execute('UPDATE split_bills SET body=? WHERE id=?',(canonical(bill),bill['id']))
            self.s.audit.append_in(conn,None,None,event,actor,{'bill_id':bill['id'],'state_hash':digest(bill)})
        return bill

    def _seal(self,bill):
        return hmac.new(self._state_key,canonical({k:v for k,v in bill.items() if k!='_seal'}).encode(),hashlib.sha256).hexdigest()

    def _load(self, bill_id, user_id):
        row=self.s.db.one('SELECT body FROM split_bills WHERE id=?',(bill_id,))
        if not row: raise Rejected('SPLIT_NOT_FOUND')
        bill=json.loads(row['body'])
        if '_seal' in bill:
            if not isinstance(bill['_seal'],str) or not hmac.compare_digest(bill['_seal'],self._seal(bill)):
                raise Rejected('SPLIT_STATE_INVALID')
        else:
            # Upgrade historical unsealed state only when its exact digest is
            # committed by the intact audit chain, never by trusting plaintext.
            committed=digest({'bill_id':bill_id,'state_hash':digest(bill)})
            if not self.s.audit.verify_chain():raise Rejected('SPLIT_STATE_INVALID')
            for event in self.s.db.rows('SELECT body FROM audit_events ORDER BY seq DESC'):
                item=json.loads(event['body'])
                if item.get('payload_hash')==committed and item.get('event_type','').startswith('SPLIT_'):
                    break
            else:raise Rejected('SPLIT_STATE_INVALID')
        if user_id!=bill['organizer'] and user_id not in bill['participants']: raise Rejected('SPLIT_ACCESS_DENIED')
        return bill

    def check_action(self, action):
        task=self.s.blackboard.get(action['task_id'])
        if not task['user_goal'].startswith('AA '): return None
        parts=task['user_goal'].split()
        if len(parts)!=4 or parts[2]!='participant': raise Rejected('SPLIT_PAYMENT_BINDING_REQUIRED')
        bill=self._load(parts[1],action['customer_id']);p=bill['participants'].get(action['customer_id'])
        route=self.s.db.one('SELECT account_id FROM split_payee_routes WHERE payee_id=?',(action['recipient_id'],))
        if (not p or p['task_id']!=task['task_id'] or p['status']!='REQUESTED'
                or bill['status'] not in {'REQUESTED','PARTIALLY_PAID'} or time.time()>=bill['expires_at']
                or action['amount']!=p['amount'] or not route or route['account_id']!=bill['account_id']):
            raise Rejected('SPLIT_PAYMENT_NOT_ALLOWED')
        return bill['expires_at']

    def create(self, organizer, account_id, total_amount, participants, method='EQUAL', values=None, expires_at=None):
        with self.s.lock:
            if getattr(self.s.gateway._adapter,'external',False): raise Rejected('SPLIT_EXTERNAL_COLLECTION_NOT_CONFIGURED')
            account=self.s.gateway.read('transaction',organizer,'account.get',account_id=account_id)
            if account['currency']!='CNY': raise Rejected('SPLIT_CURRENCY_UNSUPPORTED')
            expiry=expires_at if expires_at is not None else time.time()+86400
            if type(expiry) not in {int,float} or not time.time()<expiry<=time.time()+30*86400: raise Rejected('INVALID_SPLIT_EXPIRY')
            shares=allocate(total_amount,participants,method,values)
            if organizer in participants: raise Rejected('SPLIT_ORGANIZER_CANNOT_BE_PAYER')
            for user in shares:
                if not self.s.db.one('SELECT id FROM customers WHERE id=?',(user,)): raise Rejected('SPLIT_UNKNOWN_PARTICIPANT')
                route=self.s.db.one('SELECT payee_id FROM split_payee_routes r JOIN payees p ON p.id=r.payee_id WHERE r.account_id=? AND p.customer_id=?',(account_id,user))
                if not route: raise Rejected('SPLIT_RECEIVING_ROUTE_REQUIRED')
            bill={'id':uid(),'organizer':organizer,'account_id':account_id,'total_amount':total_amount,'currency':'CNY',
                  'method':method,'expires_at':expiry,'status':'DRAFT','participants':{u:{'user_id':u,'amount':v,'status':'PENDING','request_id':uid(),'task_id':None,'receipt_id':None} for u,v in shares.items()}}
            bill['_seal']=self._seal(bill)
            with self.s.db.transaction() as conn:
                conn.execute('INSERT INTO split_bills VALUES(?,?)',(bill['id'],canonical(bill)))
                for user,p in bill['participants'].items():
                    conn.execute('INSERT INTO split_payment_requests VALUES(?,?,?,?)',(p['request_id'],bill['id'],user,canonical({'id':p['request_id'],'bill_id':bill['id'],'user_id':user,'amount':p['amount']})))
                self.s.audit.append_in(conn,None,None,'SPLIT_DRAFT_CREATED',organizer,{'bill_id':bill['id'],'state_hash':digest(bill)})
            return bill

    @bill_claim
    def publish(self, bill_id, organizer):
        with self.s.lock:
            bill=self._load(bill_id,organizer)
            if bill['organizer']!=organizer or bill['status']!='DRAFT' or time.time()>=bill['expires_at']: raise Rejected('SPLIT_CANNOT_PUBLISH')
            bill['status']='REQUESTED'
            for p in bill['participants'].values(): p['status']='REQUESTED'
            return self._save(bill,'SPLIT_REQUESTED',organizer)

    @bill_claim
    def get(self, bill_id, user_id):
        with self.s.lock:
            bill=self._load(bill_id,user_id)
            # Recovery only observes persisted trusted tasks; never approves or retries a write.
            for p in bill['participants'].values():
                if not p['task_id']:
                    if p['status']=='PAID':raise Rejected('SPLIT_COLLECTION_EVIDENCE_MISMATCH')
                    continue
                task=self.s.get_task(p['task_id'],p['user_id'])
                if p['status']=='PAID' and (task['status']!='COMPLETED' or not task.get('receipt')
                        or task.get('verification',{}).get('status')!='VERIFIED'):
                    raise Rejected('SPLIT_COLLECTION_EVIDENCE_MISMATCH')
                if task['status']=='COMPLETED' and task.get('verification',{}).get('status')=='VERIFIED' and task.get('receipt'):
                    tx=task['execution']['transaction']
                    entries=self.s.db.rows("SELECT * FROM ledger_entries WHERE transaction_id=? AND side='CREDIT'",(tx['id'],))
                    if len(entries)!=1 or entries[0]['account_id']!=bill['account_id'] or entries[0]['amount']!=p['amount']: raise Rejected('SPLIT_COLLECTION_EVIDENCE_MISMATCH')
                    p.update(status='PAID',receipt_id=task['receipt']['receipt_id'])
            if bill['status'] not in {'DRAFT','CANCELLED','EXPIRED'}:
                required=[p for p in bill['participants'].values() if p['amount']>0]
                if all(p['status']=='PAID' for p in required): bill['status']='COMPLETED'
                elif time.time()>=bill['expires_at']:
                    bill['status']='EXPIRED'
                    for p in bill['participants'].values():
                        if p['status'] in {'PENDING','REQUESTED'}:
                            if p['task_id']: self.s.cancel_draft(p['task_id'],p['user_id'])
                            p['status']='EXPIRED'
                elif any(p['status']=='PAID' for p in required): bill['status']='PARTIALLY_PAID'
            self._save(bill,'SPLIT_OBSERVED',user_id)
            if user_id!=bill['organizer']:
                bill=dict(bill);bill['participants']={user_id:bill['participants'][user_id]}
            return bill

    @bill_claim
    def prepare_payment(self, bill_id, participant, source_account_id):
        with self.s.lock:
            self.get(bill_id,participant);bill=self._load(bill_id,participant)
            p=bill['participants'].get(participant)
            if bill['status'] not in {'REQUESTED','PARTIALLY_PAID'} or not p or p['status']!='REQUESTED' or time.time()>=bill['expires_at']: raise Rejected('SPLIT_PAYMENT_NOT_ALLOWED')
            if p['amount']==0: raise Rejected('SPLIT_ZERO_SHARE_NO_PAYMENT_REQUIRED')
            if p['task_id']: return self.s.get_task(p['task_id'],participant)
            self.s.gateway.read('transaction',participant,'account.get',account_id=source_account_id)
            route=self.s.db.one('SELECT p.* FROM split_payee_routes r JOIN payees p ON p.id=r.payee_id WHERE r.account_id=? AND p.customer_id=?',(bill['account_id'],participant))
            if not route: raise Rejected('SPLIT_RECEIVING_ROUTE_REQUIRED')
            normalized={'task_type':'TRANSFER','needs_clarification':False,'recipient_name':route['name'],'amount':p['amount'],'currency':'CNY'}
            task=self.s.create_normalized_task(f"AA {bill_id} participant {participant}",participant,source_account_id,normalized)
            p['task_id']=task['task_id'];self._save(bill,'SPLIT_PAYMENT_PREPARED',participant)
            return task

    @bill_claim
    def pay(self, bill_id, participant, action_hash, approve, strong_secret=''):
        with self.s.lock:
            self.get(bill_id,participant);bill=self._load(bill_id,participant);p=bill['participants'].get(participant)
            if not p or p['status']!='REQUESTED' or bill['status'] not in {'REQUESTED','PARTIALLY_PAID'} or time.time()>=bill['expires_at'] or not p['task_id']: raise Rejected('SPLIT_PAYMENT_NOT_ALLOWED')
            task=self.s.authorize(p['task_id'],participant,action_hash,approve,strong_secret)
            if not approve: p['status']='DECLINED';self._save(bill,'SPLIT_PARTICIPANT_DECLINED',participant)
            self.get(bill_id,participant)
            return task

    @bill_claim
    def decline(self, bill_id, participant):
        with self.s.lock:
            self.get(bill_id,participant);bill=self._load(bill_id,participant);p=bill['participants'].get(participant)
            if not p or p['status']!='REQUESTED' or bill['status'] not in {'REQUESTED','PARTIALLY_PAID'}: raise Rejected('SPLIT_CANNOT_DECLINE')
            if p['task_id'] and not self.s.cancel_draft(p['task_id'],participant): raise Rejected('SPLIT_PAYMENT_UNCERTAIN')
            p['status']='DECLINED';return self._save(bill,'SPLIT_PARTICIPANT_DECLINED',participant)

    @bill_claim
    def cancel(self, bill_id, organizer):
        with self.s.lock:
            self.get(bill_id,organizer);bill=self._load(bill_id,organizer)
            if organizer!=bill['organizer'] or bill['status'] not in {'DRAFT','REQUESTED','PARTIALLY_PAID'}: raise Rejected('SPLIT_CANNOT_CANCEL')
            for p in bill['participants'].values():
                if p['status'] in {'REQUESTED','PENDING'}:
                    if p['task_id'] and not self.s.cancel_draft(p['task_id'],p['user_id']): raise Rejected('SPLIT_PAYMENT_UNCERTAIN')
                    p['status']='CANCELLED'
            bill['status']='CANCELLED';return self._save(bill,'SPLIT_CANCELLED',organizer)
