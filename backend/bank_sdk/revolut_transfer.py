"""Opt-in, same-currency Sandbox transfers behind the unchanged Core chain."""
from datetime import datetime,timezone
from decimal import Decimal
import json
import math
import re
import time
from urllib.parse import urlencode

from backend.bank_sdk.contracts import BankCapabilityManifest,BankError,BankResponse,BankVerificationResult,UncertainBankWrite,ExternalBankSafeMode
from backend.bank_sdk.revolut import RevolutReadOnlyAdapter,identifier,minor_units,timestamp
from backend.domain import canonical,digest


def route(value):
    try:
        a,b=value.split(':')
        return identifier(a),identifier(b)
    except (ValueError,AttributeError): raise BankError('BANK_INVALID_PAYEE') from None


class RevolutTransferAdapter(RevolutReadOnlyAdapter):
    CORRIDORS={'GBP':'GB','EUR':'DE'}  # Explicit tested/demo corridors only; no FX.
    def __init__(self,db,transport,auth,customer_id,safe_mode=None,**kwargs):
        super().__init__(db,transport,auth,customer_id,**kwargs)
        self.safe_mode=safe_mode or ExternalBankSafeMode()
        if (self.safe_mode.environment!='sandbox' or self.safe_mode.scheduled_enabled
                or type(self.safe_mode.enabled) is not bool or type(self.safe_mode.maximum_amount) is not int
                or not 1<=self.safe_mode.maximum_amount<=100): raise BankError('INVALID_BANK_PROVIDER_CONFIGURATION')
        for value in self.safe_mode.source_accounts: identifier(value)
        for value in self.safe_mode.payees: route(value)
        if self.safe_mode.enabled and (not self.safe_mode.source_accounts or not self.safe_mode.payees):
            raise BankError('INVALID_BANK_PROVIDER_CONFIGURATION')
        self.manifest=BankCapabilityManifest('revolut','sandbox',self.manifest.capabilities|
            {'payees.read','payments.create','payments.status'},self.manifest.currencies,True,5)
        with db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_bank_operations(key TEXT PRIMARY KEY, hash TEXT NOT NULL, provider TEXT NOT NULL, environment TEXT NOT NULL, status TEXT NOT NULL, bank_id TEXT, body TEXT NOT NULL)')

    def _get(self,path,deadline=None):
        allowed=path=='/api/1.0/accounts' or path.startswith('/api/1.0/transactions?') or path.startswith('/api/1.0/counterparties?')
        resource=re.fullmatch(r'/api/1.0/(?:transaction|counterparty)/[0-9a-f-]{36}',path)
        if not (allowed or resource): raise BankError('BANK_ENDPOINT_DENIED')
        self.rate_limiter.acquire(deadline)
        headers=self.auth.headers('GET',path)
        try: return self.transport.send('GET',path,b'',headers)
        except BankError as exc:
            if exc.http_status!=401: raise
            headers=self.auth.headers('GET',path,failed_token=headers['Authorization'][7:])
            self.rate_limiter.acquire(deadline)
            return self.transport.send('GET',path,b'',headers)

    def _payees(self):
        output=[]
        for value in sorted(self.safe_mode.payees):
            cp,account=route(value)
            row=self._get('/api/1.0/counterparty/'+cp)
            try:
                if identifier(row['id'])!=cp or row['state']!='created': raise ValueError()
                matches=[a for a in row['accounts'] if identifier(a['id'])==account]
                currency=matches[0]['currency'] if len(matches)==1 else None
                if currency not in self.CORRIDORS or matches[0].get('bank_country')!=self.CORRIDORS[currency]: raise ValueError()
                name=row['name']
                if not isinstance(name,str) or not name or len(name)>200: raise ValueError()
                # A route-qualified display name resolves multiple bank accounts
                # deterministically without changing the frozen action schema.
                output.append({'id':value,'name':name+' ['+account+']','customer_id':self.customer_id,
                    'currency':currency,'is_new':1,'bank_name':name,'counterparty_id':cp,'receiver_account_id':account})
            except (KeyError,TypeError,ValueError): raise BankError('BANK_INVALID_PAYEE') from None
        return output

    def _operation(self,key=None,bank_id=None):
        row=self.db.one('SELECT * FROM external_bank_operations WHERE '+('key=?' if key else 'bank_id=?'),(key or bank_id,))
        if row and (row['provider']!='revolut' or row['environment']!='sandbox'): raise BankError('BANK_IDEMPOTENCY_CONFLICT')
        return row

    def _pay(self,req):
        a=req.action
        if (not isinstance(a,dict) or a.get('action_type')!='TRANSFER' or a.get('customer_id')!=self.customer_id
                or a.get('currency') not in self.CORRIDORS or req.idempotency is None
                or req.idempotency.action_hash!=digest(a) or req.idempotency.key!=a.get('idempotency_key')
                or type(req.deadline) not in {int,float} or not math.isfinite(req.deadline)):
            raise BankError('BANK_INVALID_ACTION')
        self.safe_mode.check(a,req.parameters.get('scheduled',False))
        key=identifier(req.idempotency.key)
        prior=self._operation(key=key)
        if prior:
            if prior['hash']!=digest(a): raise BankError('BANK_IDEMPOTENCY_CONFLICT')
            if prior['bank_id']: return self._status(prior['bank_id'])
            raise UncertainBankWrite()  # Permanent claim: never resend an unknown POST.
        source=self._account(a['source_account_id'])
        if (source['frozen'] or source['currency']!=a['currency'] or source['balance']!=a['preconditions']['balance']
                or source['version']!=a['preconditions']['account_version']): raise BankError('BANK_PRECONDITION_CHANGED')
        if not any(p['id']==a['recipient_id'] and p['currency']==a['currency'] for p in self._payees()): raise BankError('BANK_INVALID_PAYEE')
        cp,destination=route(a['recipient_id']);reference='TRUSTA-'+key.replace('-','')[:18]
        payload={'request_id':key,'account_id':identifier(a['source_account_id']),
                 'receiver':{'counterparty_id':cp,'account_id':destination},'currency':a['currency'],'reference':reference}
        # Encode an exact JSON number, never pass money through binary float.
        body=(canonical(payload)[:-1]+',"amount":'+format(Decimal(a['amount'])/100,'.2f')+'}').encode()
        self.rate_limiter.acquire(req.deadline)
        headers=self.auth.headers('POST','/api/1.0/pay',body)
        limit=min(req.deadline,a['expires_at'])
        if time.time()>=limit: raise BankError('BANK_DEADLINE_EXPIRED')
        record={'action':a,'reference':reference,'submitted_at':time.time(),'deadline':limit}
        with self.db.transaction() as conn:
            if conn.execute('SELECT key FROM external_bank_operations WHERE key=?',(key,)).fetchone(): raise UncertainBankWrite()
            conn.execute('INSERT INTO external_bank_operations VALUES(?,?,?,?,?,?,?)',
                (key,digest(a),'revolut','sandbox','UNKNOWN',None,canonical(record)))
        self.audit.append(a['task_id'],a['action_id'],'BANK_PAYMENT_SUBMIT_CLAIMED','bank_adapter',
                          {'request_id':key,'action_hash':digest(a),'provider':'revolut','environment':'sandbox'})
        if time.time()>=limit:
            with self.db.transaction() as conn: conn.execute("UPDATE external_bank_operations SET status='FAILED' WHERE key=?",(key,))
            raise BankError('BANK_DEADLINE_EXPIRED')
        try: result=self.transport.send('POST','/api/1.0/pay',body,headers)
        except BankError as exc:
            if exc.http_status is not None and 400<=exc.http_status<500:
                with self.db.transaction() as conn: conn.execute("UPDATE external_bank_operations SET status='FAILED' WHERE key=?",(key,))
                raise
            raise UncertainBankWrite() from None
        except Exception: raise UncertainBankWrite() from None
        try:
            bank_id=identifier(result['id'])
            if result['state'] not in {'created','pending','completed','failed','declined','reverted'}: raise ValueError()
            with self.db.transaction() as conn: conn.execute("UPDATE external_bank_operations SET bank_id=?,status='CREATED' WHERE key=?",(bank_id,key))
        except Exception: raise UncertainBankWrite() from None
        self.audit.append(a['task_id'],a['action_id'],'BANK_PAYMENT_RESPONSE_OBSERVED','bank_adapter',
                          {'bank_id':bank_id,'response_hash':digest({'id':bank_id,'state':result['state']})})
        # POST success does not confer settled/verified authority.
        return {'id':bank_id,'status':'CREATED','provider':'revolut','amount':a['amount'],'currency':a['currency']}

    def _status(self,bank_id):
        bank_id=identifier(bank_id);prior=self._operation(bank_id=bank_id)
        if not prior: raise BankError('BANK_RESOURCE_NOT_FOUND')
        record=json.loads(prior['body']);a=record['action']
        raw=self._get('/api/1.0/transaction/'+bank_id)
        try:
            if identifier(raw['id'])!=bank_id: raise ValueError()
            own=[leg for leg in raw['legs'] if identifier(leg['account_id'])==a['source_account_id']]
            if len(own)!=1: raise ValueError()
            leg=own[0];cp=leg.get('counterparty',{})
            value={'id':bank_id,'status':{'created':'CREATED','pending':'PENDING','completed':'SETTLED',
                'failed':'FAILED','declined':'FAILED','reverted':'REVERSED'}.get(raw['state'],'UNKNOWN'),
                'source_account_id':a['source_account_id'],'amount':-minor_units(leg['amount'],leg['currency']),
                'currency':leg['currency'],'action_hash':prior['hash'],'provider':'revolut',
                'evidence':{'bank_type':raw['type'],'request_id':raw.get('request_id'),'reference':raw.get('reference'),
                    'counterparty_id':cp.get('id'),'receiver_account_id':cp.get('account_id'),
                    'created_at':timestamp(raw['created_at']),
                    'completed_at':timestamp(raw['completed_at']) if raw.get('completed_at') else None,
                    'fee':minor_units(leg.get('fee',0),leg['currency']),
                    'leg_balance':minor_units(leg['balance'],leg['currency']) if 'balance' in leg else None}}
            value['recipient_id']=str(cp.get('id',''))+':'+str(cp.get('account_id',''))
        except (KeyError,TypeError,ValueError): raise BankError('BANK_INVALID_RESPONSE') from None
        with self.db.transaction() as conn:
            latest=conn.execute('SELECT status FROM external_bank_operations WHERE key=?',(prior['key'],)).fetchone()
            old=latest['status'];new=value['status']
            allowed={'SETTLED':{'SETTLED','REVERSED'},'FAILED':{'FAILED'},'REVERSED':{'REVERSED'}}
            if old in allowed and new not in allowed[old]:
                self.audit.append_in(conn,a['task_id'],a['action_id'],'BANK_STATUS_REGRESSION_REJECTED','bank_adapter',
                    {'prior_status':old,'observed_status':new,'financial_write':False})
                regression=True
            else:
                conn.execute('UPDATE external_bank_operations SET status=? WHERE key=?',(new,prior['key']))
                regression=False
        if regression:raise BankError('BANK_STATUS_REGRESSION')
        self.audit.append(a['task_id'],a['action_id'],'BANK_PAYMENT_STATUS_READ','bank_adapter',{'result_hash':digest(value)})
        return value

    def verify(self,action,transaction_id):
        try:
            row=self._status(transaction_id);prior=self._operation(bank_id=transaction_id)
            record=json.loads(prior['body']);e=row['evidence'];now=time.time()
            account=self._account(action['source_account_id'])
            expected=action['expected_postconditions']['after_balance']
            matches=(row['status']=='SETTLED' and prior['hash']==digest(action) and row['amount']==action['amount']
                and row['currency']==action['currency']==account['currency'] and row['recipient_id']==action['recipient_id']
                and e['bank_type']=='transfer' and e['request_id']==action['idempotency_key']
                and e['reference']==record['reference'] and e['fee']==0
                and e['completed_at'] is not None and record['submitted_at']-30<=e['created_at']<=e['completed_at']<=now+5
                and e['created_at']<=record['deadline']+5 and account['balance']==expected
                and (e['leg_balance'] is None or e['leg_balance']==expected))
            result=BankVerificationResult('VERIFIED' if matches else 'MANUAL_REVIEW',
                'MATCH' if matches else 'EXTERNAL_NOT_SETTLED_OR_MISMATCH',row['status'],digest({'transaction':row,'account':account}),
                e['completed_at'] if matches else None)
        except Exception:
            result=BankVerificationResult('MANUAL_REVIEW','EXTERNAL_STATUS_UNCERTAIN','UNKNOWN',digest({'transaction_id':transaction_id}))
        self.audit.append(action.get('task_id'),action.get('action_id'),'BANK_PAYMENT_VERIFIED','bank_adapter',
            {'status':result.status,'reason':result.reason,'evidence_hash':result.evidence_hash})
        return result

    def observe_submission(self,action):
        """Read-only recovery after a lost POST response; never resend /pay."""
        prior=self._operation(key=action['idempotency_key'])
        if not prior or prior['hash']!=digest(action): raise BankError('BANK_RESOURCE_NOT_FOUND')
        if prior['bank_id']: return prior['bank_id']
        record=json.loads(prior['body'])
        iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
        params={'request_id':prior['key'],'account':action['source_account_id'],
                'from':iso(record['submitted_at']-30),'to':iso(time.time()+1),'count':2}
        rows=self._get('/api/1.0/transactions?'+urlencode(params))
        if not isinstance(rows,list) or len(rows)!=1: raise BankError('BANK_STATUS_NOT_UNIQUE')
        raw=rows[0]
        if not isinstance(raw,dict) or raw.get('request_id')!=prior['key'] or raw.get('type')!='transfer':
            raise BankError('BANK_STATUS_NOT_UNIQUE')
        bank_id=identifier(raw['id'])
        with self.db.transaction() as conn:
            conn.execute("UPDATE external_bank_operations SET bank_id=? WHERE key=? AND bank_id IS NULL",(bank_id,prior['key']))
        self.audit.append(action['task_id'],action['action_id'],'BANK_PAYMENT_ID_RECOVERED','bank_adapter',
                          {'request_id':prior['key'],'bank_id':bank_id,'financial_write':False})
        return bank_id

    def request(self,req):
        if req.customer_id!=self.customer_id: raise BankError('BANK_CAPABILITY_OR_OWNER_DENIED')
        if req.capability in {'payees.read','payments.status','payments.create'}:
            p={k:v for k,v in req.parameters.items() if k!='_tool'}
            if req.capability=='payments.create':
                if set(p)-{'scheduled'}: raise BankError('BANK_INVALID_ACTION')
                return BankResponse(self._pay(req),'revolut','sandbox','CREATED')
            if req.action is not None or req.idempotency is not None: raise BankError('BANK_INVALID_READ_PARAMETERS')
            if req.capability=='payments.status':
                if set(p)!={'transaction_id'}: raise BankError('BANK_INVALID_READ_PARAMETERS')
                result=self._status(p['transaction_id'])
            else:
                if set(p)!={'name','recipient_id'}: raise BankError('BANK_INVALID_READ_PARAMETERS')
                rows=self._payees();result=[r for r in rows if (p['name'] is None or r['name']==p['name'])
                    and (p['recipient_id'] is None or r['id']==p['recipient_id'])]
            self.audit.append(None,None,'BANK_SDK_READ','bank_adapter',{'provider':'revolut','environment':'sandbox',
                'capability':req.capability,'request_hash':digest(p),'result_hash':digest(result)})
            return BankResponse(result,'revolut','sandbox')
        return super().request(req)
