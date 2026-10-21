"""Reference Public API adapter; official sandbox verification is a separate observation."""
import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from backend.domain import canonical, digest
from backend.bank_sdk.contracts import BankCapabilityManifest, BankResponse, BankError, BankVerificationResult, BankWebhookEvent, UncertainBankWrite, ExternalBankSafeMode

class ReferenceBankAdapter:
    def __init__(self, db, transport, auth, customer_id, safe_mode=None):
        self.db,self.transport,self.auth,self.customer_id = db,transport,auth,customer_id
        self.safe_mode = safe_mode or ExternalBankSafeMode()
        self.manifest = BankCapabilityManifest('starling',self.safe_mode.environment,
            frozenset({'accounts.read','balance.read','transactions.read','payees.read','payments.create','payments.status'}),frozenset({'GBP'}),True,5,1000)
        from backend.bank_sdk.rate_limit import BankRateLimiter
        self.rate_limiter=BankRateLimiter(self.manifest.rate_limit_per_second)
        expected={'sandbox':'https://api-sandbox.starlingbank.com','production':'https://api.starlingbank.com'}
        if transport.base_url != expected.get(self.safe_mode.environment) and not transport.base_url.startswith('http://127.0.0.1:'):
            raise BankError('BANK_ENVIRONMENT_MISMATCH')
        with db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_bank_operations(key TEXT PRIMARY KEY, hash TEXT NOT NULL, provider TEXT NOT NULL, environment TEXT NOT NULL, status TEXT NOT NULL, bank_id TEXT, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS bank_webhook_events(id TEXT PRIMARY KEY, hash TEXT NOT NULL)')

    @staticmethod
    def identifier(value):
        try: return str(uuid.UUID(value))
        except (ValueError,TypeError,AttributeError): raise BankError('BANK_INVALID_IDENTIFIER') from None

    def _send(self, method, path, value=None, deadline=None):
        body=canonical(value).encode() if value is not None else b''
        # Only protocol fixtures are exempt from real-provider pacing.
        if not self.transport.base_url.startswith('http://127.0.0.1:'): self.rate_limiter.acquire(deadline)
        return self.transport.send(method,path,body,self.auth.headers(method,path,body))

    def _accounts(self):
        return self._send('GET','/api/v2/accounts')['accounts']

    def _account(self, account_id):
        account_id=self.identifier(account_id)
        row=next((r for r in self._accounts() if r['accountUid']==account_id),None)
        if row is None: raise BankError('ACCOUNT_ACCESS_DENIED')
        return row

    def _balance(self, account_id):
        row=self._send('GET',f'/api/v2/accounts/{self.identifier(account_id)}/balance')
        amount=row['effectiveBalance']
        if type(amount['minorUnits']) is not int or amount['minorUnits']<0 or amount['currency'] not in self.manifest.currencies:
            raise BankError('BANK_INVALID_RESPONSE')
        return {'balance':amount['minorUnits'],'version':int(digest(amount)[:12],16),'currency':amount['currency']}

    def _payees(self):
        result=[]
        for payee in self._send('GET','/api/v2/payees')['payees']:
            for account in payee.get('accounts',[]):
                if account.get('defaultAccount') and account.get('countryCode')=='GB' and account.get('payeeChannelType','BANK_ACCOUNT')=='BANK_ACCOUNT':
                    result.append({'id':account['payeeAccountUid'],'payee_id':payee['payeeUid'],'name':payee['payeeName'],'customer_id':self.customer_id,'is_new':True})
        return result

    def _feed(self, account, start, end):
        path=f"/api/v2/feed/account/{self.identifier(account['accountUid'])}/category/{self.identifier(account['defaultCategory'])}/transactions-between"
        return self._send('GET',path+'?'+urlencode({'minTransactionTimestamp':start,'maxTransactionTimestamp':end}))['feedItems']

    def request(self, req):
        if req.capability not in self.manifest.capabilities or req.customer_id != self.customer_id:
            raise BankError('BANK_CAPABILITY_OR_OWNER_DENIED')
        p=req.parameters
        if req.capability=='accounts.read':
            if 'account_id' not in p:
                data=[{'id':r['accountUid'],'currency':r['currency'],'customer_id':self.customer_id} for r in self._accounts()]
            else:
                row=self._account(p['account_id']);balance=self._balance(row['accountUid'])
                data={'id':row['accountUid'],'customer_id':self.customer_id,'currency':row['currency'],**balance,'frozen':False,
                      'frozen_status_source':'NOT_EXPOSED_BANK_ENFORCES','version_source':'BALANCE_OBSERVATION_HASH'}
        elif req.capability=='balance.read':
            self._account(p['account_id']);data=self._balance(p['account_id'])
        elif req.capability=='payees.read':
            data=[r for r in self._payees() if (not p.get('recipient_id') or r['id']==p['recipient_id']) and (not p.get('name') or r['name']==p['name'])]
        elif req.capability=='payments.create':
            data=self._create(req)
        elif req.capability=='payments.status':
            data=self._status(p['transaction_id'])
        else:
            account=self._account(p['account_id'])
            from backend.business import month_window
            start,end,_=month_window(p['month'])
            iso=lambda v:datetime.fromtimestamp(v,timezone.utc).isoformat()
            data=[]
            for feed in self._feed(account,iso(start),iso(end)):
                try:
                    amount=feed['amount'];occurred=datetime.fromisoformat(feed['transactionTime'].replace('Z','+00:00')).timestamp()
                    if type(amount['minorUnits']) is not int or amount['minorUnits']<0 or amount['currency'] not in self.manifest.currencies: raise ValueError()
                    data.append({'id':self.identifier(feed['feedItemUid']),'customer_id':self.customer_id,'account_id':account['accountUid'],
                        'amount':amount['minorUnits'],'currency':amount['currency'],'occurred_at':occurred,
                        'status':feed['status'] if feed['status'] in {'SETTLED','PENDING'} else 'UNKNOWN',
                        'direction':feed.get('direction','UNKNOWN'),'merchant':feed.get('counterPartyName',''),
                        'category':'UNCLASSIFIED','kind':'UNKNOWN'})
                except (KeyError,TypeError,ValueError): raise BankError('BANK_INVALID_RESPONSE') from None
        return BankResponse(data,'starling',self.manifest.environment,data.get('status') if isinstance(data,dict) else None)

    def _create(self, req):
        a=req.action
        if (not a or a.get('action_type')!='TRANSFER' or a['customer_id']!=self.customer_id or req.idempotency is None
                or req.idempotency.action_hash!=digest(a) or req.idempotency.key!=a['idempotency_key']
                or req.deadline is None or time.time()>=min(req.deadline,a['expires_at']) or a['currency']!='GBP'):
            raise BankError('BANK_INVALID_WRITE')
        self.safe_mode.check(a,req.parameters.get('scheduled',False))
        key=self.identifier(req.idempotency.key)
        prior=self.db.one('SELECT * FROM external_bank_operations WHERE key=?',(key,))
        if prior:
            if prior['hash']!=digest(a) or prior['provider']!='starling' or prior['environment']!=self.manifest.environment:
                raise BankError('BANK_IDEMPOTENCY_CONFLICT')
            if prior['bank_id']: return self._status(prior['bank_id'])
            raise UncertainBankWrite()
        source=self._account(a['source_account_id'])
        balance=self._balance(a['source_account_id'])
        if balance['balance']!=a['preconditions']['balance'] or balance['version']!=a['preconditions']['account_version']:
            raise BankError('BANK_PRECONDITION_CHANGED')
        if not any(p['id']==a['recipient_id'] for p in self._payees()):
            raise BankError('BANK_INVALID_PAYEE')
        reference=key.replace('-','')[:18]
        record={'action':a,'category':source['defaultCategory'],'reference':reference,'submitted_at':time.time()}
        with self.db.transaction() as conn:
            prior=conn.execute('SELECT * FROM external_bank_operations WHERE key=?',(key,)).fetchone()
            if prior:
                if prior['hash']!=digest(a) or prior['provider']!='starling' or prior['environment']!=self.manifest.environment:
                    raise BankError('BANK_IDEMPOTENCY_CONFLICT')
                # Another worker already claimed this key. Never issue a second PUT.
                raise UncertainBankWrite()
            conn.execute('INSERT INTO external_bank_operations VALUES(?,?,?,?,?,?,?)',(key,digest(a),'starling',self.manifest.environment,'UNKNOWN',None,canonical(record)))
        try:
            path=f"/api/v2/payments/local/account/{self.identifier(source['accountUid'])}/category/{self.identifier(source['defaultCategory'])}"
            result=self._send('PUT',path,{'externalIdentifier':key,'destinationPayeeAccountUid':self.identifier(a['recipient_id']),
                                       'reference':reference,'amount':{'currency':a['currency'],'minorUnits':a['amount']}},min(req.deadline,a['expires_at']))
            bank_id=self.identifier(result['paymentOrderUid'])
            with self.db.transaction() as conn:
                conn.execute("UPDATE external_bank_operations SET status='CREATED',bank_id=? WHERE key=?",(bank_id,key))
            return {'id':bank_id,'status':'CREATED','provider':'starling','amount':a['amount'],'currency':a['currency']}
        except BankError:
            with self.db.transaction() as conn: conn.execute("UPDATE external_bank_operations SET status='FAILED' WHERE key=?",(key,))
            raise
        except Exception:
            raise UncertainBankWrite() from None

    def _status(self, transaction_id):
        transaction_id=self.identifier(transaction_id)
        prior=self.db.one('SELECT * FROM external_bank_operations WHERE bank_id=?',(transaction_id,))
        if not prior or prior['environment']!=self.manifest.environment: raise BankError('BANK_RESOURCE_NOT_FOUND')
        record=json.loads(prior['body']);a=record['action']
        order=self._send('GET',f'/api/v2/payments/local/payment-order/{transaction_id}')
        payments=self._send('GET',f'/api/v2/payments/local/payment-order/{transaction_id}/payments')['payments']
        status='UNKNOWN';evidence={'order':order,'payments':payments,'feed':[]}
        if len(payments)==1:
            row=payments[0];s=row.get('paymentStatusDetails',{}).get('paymentStatus')
            status={'PENDING':'PENDING','ACCEPTED':'PROCESSING','REJECTED':'FAILED'}.get(s,'UNKNOWN')
            if row.get('rejectedAt'): status='FAILED'
            if s=='ACCEPTED' and row.get('completedAt') and not row.get('rejectedAt'):
                account=self._account(a['source_account_id'])
                start=datetime.fromtimestamp(record['submitted_at']-60,timezone.utc).isoformat()
                feed=self._feed(account,start,datetime.now(timezone.utc).isoformat())
                matches=[f for f in feed if f.get('reference')==record['reference'] and f.get('counterPartySubEntityUid')==a['recipient_id']]
                evidence['feed']=matches
                if len(matches)==1 and matches[0].get('status')=='SETTLED' and matches[0].get('direction')=='OUT' and matches[0].get('amount')=={'currency':a['currency'],'minorUnits':a['amount']}:
                    status='SETTLED'
        result={'id':transaction_id,'status':status,'source_account_id':a['source_account_id'],'recipient_id':order.get('payeeAccountUid'),
                'amount':order.get('amount',{}).get('minorUnits'),'currency':order.get('amount',{}).get('currency'),
                'action_hash':prior['hash'],'provider':'starling','evidence':evidence}
        with self.db.transaction() as conn: conn.execute('UPDATE external_bank_operations SET status=? WHERE bank_id=?',(status,transaction_id))
        return result

    def verify(self, action, transaction_id):
        try:
            row=self._status(transaction_id);order=row['evidence']['order'];payments=row['evidence']['payments']
            match=(row['status']=='SETTLED' and row['action_hash']==digest(action) and order.get('paymentOrderUid')==transaction_id
                   and type(row['amount']) is int and row['amount']==action['amount'] and row['currency']==action['currency'] and row['recipient_id']==action['recipient_id']
                   and len(payments)==1 and payments[0].get('amount')==order['amount'] and payments[0].get('payeeAccountUid')==action['recipient_id']
                   and type(payments[0].get('amount',{}).get('minorUnits')) is int
                   and payments[0].get('reference')==order.get('reference')==row['evidence']['feed'][0].get('reference')
                   and type(row['evidence']['feed'][0].get('amount',{}).get('minorUnits')) is int)
            settled_at=datetime.fromisoformat(payments[0]['completedAt'].replace('Z','+00:00')).timestamp() if match else None
            if settled_at is not None and not 0<settled_at<=time.time()+5: match=False;settled_at=None
            return BankVerificationResult('VERIFIED' if match else 'MANUAL_REVIEW','MATCH' if match else 'EXTERNAL_NOT_SETTLED_OR_MISMATCH',row['status'],digest(row),settled_at)
        except Exception:
            return BankVerificationResult('MANUAL_REVIEW','EXTERNAL_STATUS_UNCERTAIN','UNKNOWN',digest({'transaction_id':transaction_id}))

    def webhook(self, raw, signature, public_key):
        try:
            if len(raw)>65536: raise ValueError()
            key=serialization.load_pem_public_key(public_key)
            if not isinstance(key,rsa.RSAPublicKey): raise ValueError()
            key.verify(base64.b64decode(signature,validate=True),raw,padding.PKCS1v15(),hashes.SHA512())
            from backend.llm.validation import strict_json
            payload=strict_json(raw.decode());resource=self.identifier(payload['feedItemUid'])
            hashed=hashlib.sha256(raw).hexdigest()
            event=BankWebhookEvent(hashed,resource,'FEED_ITEM',hashed)
            with self.db.transaction() as conn:
                if conn.execute('SELECT id FROM bank_webhook_events WHERE id=?',(event.event_id,)).fetchone():
                    raise BankError('BANK_WEBHOOK_REPLAY')
                conn.execute('INSERT INTO bank_webhook_events VALUES(?,?)',(event.event_id,hashed))
            return event
        except BankError: raise
        except Exception: raise BankError('BANK_WEBHOOK_SIGNATURE_INVALID') from None
