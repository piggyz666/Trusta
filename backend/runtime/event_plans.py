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
    TYPES = {'BIRTHDAY_TRANSFER','SPENDING_REVIEW','RECURRING_TRANSFER','WISH_ORDER','WISH_RELEASE'}
    FREQUENCIES = {'MONTHLY','WEEKLY'}

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
        elif event_type=='RECURRING_TRANSFER':
            if (set(parameters)!={'recipient_name','amount','frequency','remaining','series_id'}
                    or type(parameters['amount']) is not int or not 0<parameters['amount']<=10**12
                    or parameters['frequency'] not in self.FREQUENCIES or type(parameters['remaining']) is not int
                    or not 1<=parameters['remaining']<=24 or not isinstance(parameters['recipient_name'],str)
                    or not 1<=len(parameters['recipient_name'])<=100 or not isinstance(parameters['series_id'],str)
                    or not 1<=len(parameters['series_id'])<=64): raise Rejected('INVALID_EVENT_PLAN')
            self.s.transaction.recipient(owner,parameters['recipient_name'])
        elif event_type in {'WISH_ORDER','WISH_RELEASE'}:
            fund=parameters.get('wish_fund_id')
            if not isinstance(fund,str) or not fund.startswith('wish-') or not 6<=len(fund)<=40: raise Rejected('INVALID_EVENT_PLAN')
            if event_type=='WISH_RELEASE':
                if set(parameters)!={'wish_fund_id'}: raise Rejected('INVALID_EVENT_PLAN')
            else:
                items=parameters.get('items')
                if set(parameters)!={'wish_fund_id','items'} or not isinstance(items,list) or not 1<=len(items)<=5:
                    raise Rejected('INVALID_EVENT_PLAN')
                catalogue={m['id']:m for m in self.s.service_agent.resources(owner,'merchant')}
                for item in items:
                    # Prices are fixed from the bank's merchant catalogue at planning time and re-checked at execution.
                    if (not isinstance(item,dict) or set(item)!={'merchant_id','amount'} or item['merchant_id'] not in catalogue
                            or item['amount']!=catalogue[item['merchant_id']]['price']): raise Rejected('INVALID_EVENT_PLAN')
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
                    p=intent['parameters']
                    if intent['event_type'] in {'BIRTHDAY_TRANSFER','RECURRING_TRANSFER'}:
                        normalized={'task_type':'TRANSFER','recipient_name':p['recipient_name'],'amount':p['amount'],'currency':'CNY','needs_clarification':False}
                        label='生日转账事件' if intent['event_type']=='BIRTHDAY_TRANSFER' else '周期转账到期'
                        task=self.s._create_task(label+'：需要逐笔审批',owner,intent['account_id'],normalized)
                        if intent['event_type']=='RECURRING_TRANSFER' and p['remaining']>1:
                            # Next occurrence is a new proposal-only plan; nothing is pre-authorised.
                            nxt=next_occurrence(intent['execute_at'],p['frequency'])
                            self.create(owner,f"{p['series_id']}#{int(nxt)}",intent['account_id'],'RECURRING_TRANSFER',nxt,
                                        p|{'remaining':p['remaining']-1})
                    elif intent['event_type'] in {'WISH_ORDER','WISH_RELEASE'}:
                        fund=self.s.gateway.read('service',owner,'wish_fund.get',resource_id=p['wish_fund_id'])
                        if fund['status']!='ACTIVE':
                            raise Rejected('WISH_FUND_NOT_ACTIVE')
                        if intent['event_type']=='WISH_ORDER':
                            amount=sum(i['amount'] for i in p['items'])
                            normalized={'task_type':'WISH_FUND_MANAGEMENT','operation':'WISH_ORDER','target_id':p['wish_fund_id'],
                                        'parameters':{'amount':amount,'currency':'CNY','items':p['items']},'needs_clarification':False}
                        else:
                            normalized={'task_type':'WISH_FUND_MANAGEMENT','operation':'FUND_RELEASE','target_id':p['wish_fund_id'],
                                        'parameters':{},'needs_clarification':False}
                        task=self.s.banking.create('心愿金计划到期：需要确认',owner,fund['account_id'],normalized)
                    else:
                        normalized={'task_type':'CROSS_SCENARIO','month':datetime.now(CHINA).strftime('%Y-%m'),'needs_clarification':False}
                        task=self.s.banking.create('事件触发支出分析与订阅审查：需要逐项审批',owner,intent['account_id'],normalized)
                    body.update(status='PROPOSED',task_id=task['task_id'],task_status=task['status'])
                except Rejected as exc:
                    if exc.code=='WISH_FUND_NOT_ACTIVE':  # lock was declined or already released: nothing to do
                        body.update(status='SKIPPED',reason=exc.code)
                    else:
                        body.update(status='MANUAL_REVIEW',reason='EVENT_TRIGGER_UNCERTAIN')
                except Exception:
                    # A task may already exist. No automatic retry with a new task/action key.
                    body.update(status='MANUAL_REVIEW',reason='EVENT_TRIGGER_UNCERTAIN')
                with self.s.db.transaction() as conn:
                    conn.execute('UPDATE event_plans SET status=?,body=? WHERE id=?',(body['status'],self._encode(body),body['id']))
                    self.s.audit.append_in(conn,body['task_id'],None,'EVENT_TRIGGER_RESULT','runtime',{'event_id':body['id'],'status':body['status']})
                results.append(body)
        return {'processed':len(results),'events':results}

    def advance_for_demo(self, owner, event_id):
        """Demo-only fast forward (TRUSTA_DEMO_MODE=true): makes one PENDING event due now. Audited; never authorises."""
        import os
        if os.environ.get('TRUSTA_DEMO_MODE','false').lower()!='true':
            raise Rejected('DEMO_MODE_DISABLED')
        with self.s.db.transaction() as conn:
            row=conn.execute("SELECT * FROM event_plans WHERE id=? AND owner=?",(event_id,owner)).fetchone()
            if not row: raise Rejected('EVENT_NOT_FOUND_OR_DENIED')
            body=self._decode(row)
            if body['status']!='PENDING': raise Rejected('EVENT_CANNOT_ADVANCE')
            now=time.time()
            body['execute_at']=now
            conn.execute('UPDATE event_plans SET execute_at=?,body=? WHERE id=?',(now,self._encode(body),event_id))
            self.s.audit.append_in(conn,None,None,'EVENT_DEMO_ADVANCED','runtime',{'event_id':event_id})
        return self.tick(owner)

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


def next_occurrence(stamp, frequency):
    """Same wall-clock time next week / next month (day clamped to month end), Asia/Shanghai."""
    from calendar import monthrange
    from datetime import timedelta
    moment=datetime.fromtimestamp(stamp,CHINA)
    if frequency=='WEEKLY':
        return (moment+timedelta(days=7)).timestamp()
    year,month=(moment.year+1,1) if moment.month==12 else (moment.year,moment.month+1)
    return moment.replace(year=year,month=month,day=min(moment.day,monthrange(year,month)[1])).timestamp()
