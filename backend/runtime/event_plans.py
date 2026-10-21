"""Owner-scoped reminders and one-shot events. Events create proposals, never authorization."""
import hashlib
import hmac
import json
import math
import time
from datetime import datetime
from backend.business import CHINA
from backend.domain import Rejected, canonical, digest, uid


class EventPlans:
    TYPES = {'BIRTHDAY_TRANSFER','SPENDING_REVIEW'}

    def __init__(self, service, recover=True):
        self.s = service
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(service)
        self.key = hmac.digest(service.config.signing_key,b'event-plans-v1','sha256')
        with service.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS event_plans(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,execute_at REAL NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(owner,request_id))')
            conn.execute('CREATE TABLE IF NOT EXISTS renewal_reminders(id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL)')
            # Never automatically create another task after an interrupted trigger.
            for row in (conn.execute("SELECT * FROM event_plans WHERE status='RUNNING'").fetchall() if recover else []):
                from backend.runtime.graph_claims import process_alive
                claim=conn.execute('SELECT pid FROM graph_worker_claims WHERE task_id=?',('event:'+row['id'],)).fetchone()
                if claim and process_alive(claim['pid']):continue
                body=self._decode(row)
                body.update(status='MANUAL_REVIEW',reason='INTERRUPTED_TRIGGER_DO_NOT_REPEAT')
                conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',('MANUAL_REVIEW',self._encode(body),row['id']))
                service.audit.append_in(conn,None,None,'EVENT_TRIGGER_INTERRUPTED','runtime',{'event_id':row['id']})

    def _encode(self, value):
        return canonical({'value':value,'seal':hmac.new(self.key,canonical(value).encode(),hashlib.sha256).hexdigest()})

    def _load(self, text):
        try:
            item=json.loads(text)
            expected=hmac.new(self.key,canonical(item['value']).encode(),hashlib.sha256).hexdigest()
            if set(item)!={'value','seal'} or not hmac.compare_digest(item['seal'],expected): raise ValueError()
            return item['value']
        except (KeyError,ValueError,TypeError):
            raise Rejected('EVENT_STATE_INVALID') from None

    def _decode(self, row):
        body=self._load(row['body'])
        if any(body[k]!=row[k] for k in ('id','owner','request_id','execute_at','status')):
            raise Rejected('EVENT_STATE_INVALID')
        return body

    def create(self, owner, request_id, account_id, event_type, execute_at, parameters):
        if (not isinstance(request_id,str) or not 1<=len(request_id)<=100 or not isinstance(event_type,str) or event_type not in self.TYPES
                or type(execute_at) not in (int,float) or not math.isfinite(execute_at)
                or not time.time()<execute_at<=time.time()+366*86400 or not isinstance(parameters,dict)):
            raise Rejected('INVALID_EVENT_PLAN')
        account=self.s.service_agent.gateway.read('service',owner,'account.get',account_id=account_id)
        if account['currency']!='CNY': raise Rejected('EVENT_CURRENCY_UNSUPPORTED')
        if event_type=='BIRTHDAY_TRANSFER':
            if (set(parameters)!={'recipient_name','amount'} or type(parameters['amount']) is not int
                    or not 0<parameters['amount']<=10**12 or not isinstance(parameters['recipient_name'],str)
                    or not 1<=len(parameters['recipient_name'])<=100): raise Rejected('INVALID_EVENT_PLAN')
            # Resolve real authority now; resolve again when the event triggers.
            self.s.transaction.recipient(owner,parameters['recipient_name'])
        elif parameters:
            raise Rejected('INVALID_EVENT_PLAN')
        intent={'account_id':account_id,'event_type':event_type,'execute_at':execute_at,'parameters':parameters}
        with self.s.db.transaction() as conn:
            prior=conn.execute('SELECT * FROM event_plans WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()
            if prior:
                body=self._decode(prior)
                if body['intent']!=intent: raise Rejected('EVENT_IDEMPOTENCY_CONFLICT')
                return body
            body={'id':uid(),'owner':owner,'request_id':request_id,'execute_at':execute_at,'status':'PENDING',
                  'intent':intent,'task_id':None,'authority':'PROPOSAL_ONLY','created_at':time.time()}
            conn.execute('INSERT INTO event_plans VALUES(?,?,?,?,?,?)',(body['id'],owner,request_id,execute_at,'PENDING',self._encode(body)))
            self.s.audit.append_in(conn,None,None,'EVENT_PLAN_CREATED','runtime',{'event_id':body['id'],'intent_hash':digest(intent),'authority':'PROPOSAL_ONLY'})
        return body

    def get(self, event_id, owner):
        row=self.s.db.one('SELECT * FROM event_plans WHERE id=? AND owner=?',(event_id,owner))
        if not row: raise Rejected('EVENT_NOT_FOUND_OR_DENIED')
        return self._decode(row)

    def list(self, owner):
        return [self._decode(row) for row in self.s.db.rows('SELECT * FROM event_plans WHERE owner=? ORDER BY execute_at,id',(owner,))]

    def cancel(self, event_id, owner):
        with self.s.db.transaction() as conn:
            row=conn.execute('SELECT * FROM event_plans WHERE id=? AND owner=?',(event_id,owner)).fetchone()
            if not row: raise Rejected('EVENT_NOT_FOUND_OR_DENIED')
            body=self._decode(row)
            if body['status']=='CANCELLED': return body
            if body['status']!='PENDING': raise Rejected('EVENT_CANNOT_CANCEL')
            body['status']='CANCELLED'
            conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',('CANCELLED',self._encode(body),event_id))
            self.s.audit.append_in(conn,None,None,'EVENT_PLAN_CANCELLED','runtime',{'event_id':event_id})
            return body

    def tick(self, owner):
        results=[]
        for item in self.s.db.rows("SELECT id FROM event_plans WHERE owner=? AND execute_at<=? AND status='PENDING' ORDER BY execute_at,id",(owner,time.time())):
            from contextlib import ExitStack
            with ExitStack() as ownership:
                try:ownership.enter_context(self.claims.hold('event:'+item['id']))
                except Rejected as exc:
                    if exc.code=='GRAPH_WORKER_BUSY':continue
                    raise
                with self.s.db.transaction() as conn:
                    row=conn.execute("SELECT * FROM event_plans WHERE id=? AND owner=? AND execute_at<=? AND status='PENDING'",(item['id'],owner,time.time())).fetchone()
                    if not row: continue
                    body=self._decode(row)
                    body['status']='RUNNING'
                    conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',('RUNNING',self._encode(body),body['id']))
                    self.s.audit.append_in(conn,None,None,'EVENT_TRIGGER_STARTED','runtime',{'event_id':body['id']})
                # The DB claim prevents duplicate triggers. Holding the service lock
                # while parallel Agents read would deadlock their identity checks.
                try:
                    intent=body['intent']
                    if intent['event_type']=='BIRTHDAY_TRANSFER':
                        p=intent['parameters']
                        normalized={'task_type':'TRANSFER','recipient_name':p['recipient_name'],'amount':p['amount'],'currency':'CNY','needs_clarification':False}
                        task=self.s._create_task('生日转账事件：需要逐笔审批',owner,intent['account_id'],normalized)
                    else:
                        normalized={'task_type':'CROSS_SCENARIO','month':datetime.now(CHINA).strftime('%Y-%m'),'needs_clarification':False}
                        task=self.s.banking.create('事件触发支出分析与订阅审查：需要逐项审批',owner,intent['account_id'],normalized)
                    body.update(status='PROPOSED',task_id=task['task_id'],task_status=task['status'])
                except Exception:
                    # A task may already exist. No automatic retry with a new task/action key.
                    body.update(status='MANUAL_REVIEW',reason='EVENT_TRIGGER_UNCERTAIN')
                with self.s.db.transaction() as conn:
                    conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',(body['status'],self._encode(body),body['id']))
                    self.s.audit.append_in(conn,body['task_id'],None,'EVENT_TRIGGER_RESULT','runtime',{'event_id':body['id'],'status':body['status']})
                results.append(body)
        return {'processed':len(results),'events':results}

    def reminders(self, owner, account_id):
        self.s.gateway.read('service',owner,'account.get',account_id=account_id)
        current=[]
        now=time.time()
        for kind in ('subscription','direct_debit'):
            for row in self.s.service_agent.resources(owner,kind):
                if row['account_id']!=account_id or row['status']!='ACTIVE': continue
                deadline=row['next_charge_at']
                if type(deadline) not in (int,float) or not math.isfinite(deadline): raise Rejected('INVALID_RENEWAL_EVIDENCE')
                reminder_id=digest({'owner':owner,'kind':kind,'id':row['id'],'next_charge_at':deadline,'version':row['version']})
                current.append({'id':reminder_id,'owner':owner,'account_id':account_id,'kind':kind,'resource_id':row['id'],
                                'merchant':row['merchant'],'amount':row['amount'],'currency':row['currency'],
                                'next_charge_at':deadline,'remind_at':deadline-3*86400,'verified_at':now,
                                'status':'OVERDUE' if deadline<=now else 'DUE' if deadline-3*86400<=now else 'UPCOMING',
                                'authority':'NOTIFICATION_ONLY','evidence_hash':digest(row)})
        with self.s.db.transaction() as conn:
            active_ids={row['id'] for row in current}
            for row in conn.execute('SELECT * FROM renewal_reminders WHERE owner=?',(owner,)).fetchall():
                previous=self._load(row['body'])
                if previous['id']!=row['id'] or previous['owner']!=owner: raise Rejected('EVENT_STATE_INVALID')
                if previous['account_id']==account_id and row['id'] not in active_ids and previous['status']!='STALE':
                    previous.update(status='STALE',verified_at=now)
                    conn.execute('UPDATE renewal_reminders SET body=? WHERE id=?',(self._encode(previous),row['id']))
            for reminder in current:
                exists=conn.execute('SELECT id FROM renewal_reminders WHERE id=?',(reminder['id'],)).fetchone()
                conn.execute('INSERT INTO renewal_reminders VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                             (reminder['id'],owner,self._encode(reminder)))
                if not exists:
                    self.s.audit.append_in(conn,None,None,'RENEWAL_REMINDER_CREATED','runtime',{'reminder_id':reminder['id'],'evidence_hash':reminder['evidence_hash']})
        return {'reminders':current,'bank_write':False,'delivery':'LOCAL_BACKEND_QUEUE'}
