"""Revolut sandbox read-only provider; unsupported capabilities fail closed."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from urllib.parse import urlencode
import uuid

from backend.audit import AuditEngine
from backend.bank_sdk.contracts import BankCapabilityManifest, BankResponse, BankError, ExternalBankSafeMode
from backend.bank_sdk.rate_limit import BankRateLimiter
from backend.bank_sdk.revolut_auth import SANDBOX
from backend.business import month_window
from backend.domain import digest

CURRENCIES=frozenset({'GBP','USD','EUR','AUD'})  # This adapter supports two-decimal units only.


def decimal_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate bank field')
            result[key]=value
        return result
    def invalid(value): raise ValueError('non-finite bank value')
    return json.loads(text,parse_float=Decimal,parse_constant=invalid,object_pairs_hook=pairs)


def minor_units(value,currency):
    if currency not in CURRENCIES or type(value) not in {int,Decimal}:
        raise BankError('BANK_INVALID_MONEY')
    number=Decimal(value)
    if not number.is_finite() or abs(number)>Decimal('1000000000000'):
        raise BankError('BANK_INVALID_MONEY')
    scaled=number*100
    if scaled!=scaled.to_integral_value(): raise BankError('BANK_INVALID_MONEY')
    return int(scaled)


def identifier(value):
    try: return str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError): raise BankError('BANK_INVALID_IDENTIFIER') from None


def timestamp(value):
    try:
        if not isinstance(value,str): raise ValueError()
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None: raise ValueError()
        return parsed.timestamp()
    except (ValueError,TypeError,OverflowError): raise BankError('BANK_INVALID_RESPONSE') from None


class RevolutReadOnlyAdapter:
    def __init__(self,db,transport,auth,customer_id,page_size=1000,max_pages=20):
        if transport.base_url!=SANDBOX: raise BankError('BANK_ENVIRONMENT_MISMATCH')
        if (not isinstance(customer_id,str) or not customer_id or type(page_size) is not int
                or not 1<=page_size<=1000 or type(max_pages) is not int or not 1<=max_pages<=100):
            raise BankError('INVALID_BANK_PROVIDER_CONFIGURATION')
        self.db,self.transport,self.auth,self.customer_id=db,transport,auth,customer_id
        self.page_size,self.max_pages=page_size,max_pages
        self.manifest=BankCapabilityManifest('revolut','sandbox',
            frozenset({'accounts.read','balance.read','transactions.read'}),CURRENCIES,False,5)
        self.safe_mode=ExternalBankSafeMode()  # No capability can enable a write in this provider.
        self.audit=AuditEngine(db)
        self.rate_limiter=BankRateLimiter(5)  # Conservative local pacing, not a claimed official quota.

    def _get(self,path,deadline=None):
        if path!='/api/1.0/accounts' and not path.startswith('/api/1.0/transactions?'):
            raise BankError('BANK_READ_ONLY')
        self.rate_limiter.acquire(deadline)
        headers=self.auth.headers('GET',path,b'')
        try:
            return self.transport.send('GET',path,b'',headers)
        except BankError as exc:
            if exc.http_status!=401: raise
            # Retry only this GET once, after refresh. Never replay financial operations.
            headers=self.auth.headers('GET',path,b'',failed_token=headers['Authorization'][7:])
            self.rate_limiter.acquire(deadline)
            return self.transport.send('GET',path,b'',headers)

    def _accounts(self,deadline=None):
        rows=self._get('/api/1.0/accounts',deadline)
        if not isinstance(rows,list): raise BankError('BANK_INVALID_RESPONSE')
        result=[];seen=set()
        for row in rows:
            try:
                account=identifier(row['id']);currency=row['currency'];balance=minor_units(row['balance'],currency)
                if account in seen or row['state'] not in {'active','inactive'}: raise ValueError()
                seen.add(account)
                value={'id':account,'customer_id':self.customer_id,'currency':currency,'balance':balance,
                    'frozen':row['state']!='active','bank_state':row['state'],
                    'frozen_status_source':'BANK_ACCOUNT_STATE','version_source':'BALANCE_OBSERVATION_HASH',
                    'name':row.get('name','')}
                value['version']=int(digest({'balance':balance,'state':row['state'],'currency':currency})[:12],16)
                result.append(value)
            except (KeyError,TypeError,ValueError): raise BankError('BANK_INVALID_RESPONSE') from None
        return result

    def _account(self,account_id,deadline=None):
        account_id=identifier(account_id)
        result=next((r for r in self._accounts(deadline) if r['id']==account_id),None)
        if result is None: raise BankError('ACCOUNT_ACCESS_DENIED')
        return result

    def _transactions(self,account,month,deadline):
        start,end,_=month_window(month)
        iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
        upper=end;seen={};output=[]
        for _ in range(self.max_pages):
            path='/api/1.0/transactions?'+urlencode({'from':iso(start),'to':iso(upper),
                'account':account['id'],'count':self.page_size})
            rows=self._get(path,deadline)
            if not isinstance(rows,list) or len(rows)>self.page_size: raise BankError('BANK_INVALID_RESPONSE')
            times=[]
            for row in rows:
                try:
                    txid=identifier(row['id']);created=timestamp(row['created_at']);times.append(created)
                    if not start<=created<upper: raise ValueError()
                    if not isinstance(row['legs'],list) or not row['legs']: raise ValueError()
                    state={'completed':'SETTLED','pending':'PENDING','created':'CREATED',
                           'failed':'FAILED','declined':'FAILED','reverted':'REVERSED'}.get(row['state'],'UNKNOWN')
                    kind=row['type'];matched=False;normalized=[];legs_seen=set()
                    if not isinstance(kind,str): raise ValueError()
                    for leg in row['legs']:
                        # Drop unrelated legs entirely, including their private counterparty/card data.
                        if identifier(leg['account_id'])!=account['id']: continue
                        matched=True
                        leg_id=identifier(leg['leg_id'])
                        if leg_id in legs_seen: raise ValueError()
                        legs_seen.add(leg_id)
                        currency=leg['currency'];amount=minor_units(leg['amount'],currency)
                        if currency!=account['currency']: raise ValueError()
                        if kind=='card_payment' and amount<0: classification='PURCHASE'
                        elif kind=='card_refund' and amount>0: classification='REFUND'
                        else: classification='NON_CONSUMPTION'
                        merchant=row.get('merchant') or {}
                        if not isinstance(merchant,dict): raise ValueError()
                        name=merchant.get('name','')
                        if not isinstance(name,str): raise ValueError()
                        normalized.append({'id':txid+':'+leg_id,
                            'customer_id':self.customer_id,'account_id':account['id'],
                            'amount':abs(amount),'currency':currency,'occurred_at':created,'status':state,
                            'direction':'OUT' if amount<0 else 'IN' if amount>0 else 'ZERO',
                            'merchant':name,'category':'UNCLASSIFIED','kind':classification,
                            'provider':'revolut','bank_type':kind,'classification_source':'BANK_TYPE_AND_LEG_SIGN',
                            'period_basis':'CREATED_AT'})
                    if not matched: raise ValueError()
                    observation=digest(sorted(normalized,key=lambda r:r['id']))
                    if txid in seen:
                        if seen[txid]!=observation: raise BankError('BANK_OBSERVATION_CHANGED')
                    else:
                        seen[txid]=observation;output.extend(normalized)
                except (KeyError,TypeError,ValueError): raise BankError('BANK_INVALID_RESPONSE') from None
            if any(a<b for a,b in zip(times,times[1:])): raise BankError('BANK_INVALID_PAGINATION')
            if len(rows)<self.page_size: return sorted(output,key=lambda row:(row['occurred_at'],row['id']))
            oldest=min(times)
            # Include the boundary timestamp again, deduplicate by transaction ID,
            # and fail if a full timestamp group prevents cursor progress. This
            # avoids skipping unseen transactions with the same created_at value.
            next_upper=oldest+0.000001
            if next_upper>=upper:
                raise BankError('BANK_PAGINATION_INCOMPLETE')
            upper=next_upper
        raise BankError('BANK_PAGINATION_LIMIT')

    def request(self,req):
        if req.customer_id!=self.customer_id: raise BankError('BANK_CAPABILITY_OR_OWNER_DENIED')
        if req.capability not in self.manifest.capabilities: raise BankError('BANK_READ_ONLY')
        p={k:v for k,v in req.parameters.items() if k!='_tool'}
        allowed={'accounts.read':[set(),{'account_id'}],'balance.read':[{'account_id'}],
                 'transactions.read':[{'account_id','month'}]}[req.capability]
        if set(p) not in allowed or req.action is not None or req.idempotency is not None:
            raise BankError('BANK_INVALID_READ_PARAMETERS')
        try:
            if req.capability=='accounts.read':
                data=self._account(p['account_id'],req.deadline) if p else self._accounts(req.deadline)
            elif req.capability=='balance.read':
                account=self._account(p['account_id'],req.deadline)
                data={key:account[key] for key in ('id','balance','version','currency')}
            else:
                month_window(p['month'])
                data=self._transactions(self._account(p['account_id'],req.deadline),p['month'],req.deadline)
            self.audit.append(None,None,'BANK_SDK_READ','bank_adapter',{'provider':'revolut','environment':'sandbox',
                'capability':req.capability,'request_hash':digest(p),'result_hash':digest(data)})
            return BankResponse(data,'revolut','sandbox')
        except BankError as exc:
            self.audit.append(None,None,'BANK_SDK_READ_REJECTED','bank_adapter',{'capability':req.capability,'reason':exc.code})
            raise

    def verify(self,action,transaction_id):
        raise BankError('BANK_READ_ONLY')
