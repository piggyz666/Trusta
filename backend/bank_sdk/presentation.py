"""Authenticated bank catalogue for the local UI; no execution authority."""
from backend.bank_sdk.contracts import BankRequest


def bank_overview(service,user_id):
    provider=service.gateway._adapter.provider
    manifest=provider.manifest
    if manifest.environment=='local':
        ids=[row['id'] for row in service.db.rows('SELECT id FROM accounts WHERE customer_id=? ORDER BY id',(user_id,))]
        rows=[service.gateway.read('verifier',user_id,'account.get',account_id=value) for value in ids]
    else:
        # Catalogue discovery is infrastructure, not a new Agent grant. The
        # provider binds the caller to the bank-authorised organisation.
        rows=provider.request(BankRequest('accounts.read',user_id)).data
        rows=[r if {'balance','frozen'}<=set(r) else
              service.gateway.read('verifier',user_id,'account.get',account_id=r['id'])
              for r in rows]
    accounts=[{k:r[k] for k in ('id','currency','balance','frozen')}|
              {'name':r.get('name') or r['currency']+'账户'} for r in rows]
    safe=getattr(provider,'safe_mode',None)
    return {'provider':manifest.provider,'environment':manifest.environment,'accounts':accounts,
            'capabilities':sorted(manifest.capabilities),
            'writes_enabled':manifest.environment=='local' or bool(safe and safe.enabled),
            'maximum_payment_minor_units':safe.maximum_amount if safe else None}


def bank_payees(service,user_id):
    if 'payee.resolve' not in service.gateway._adapter.manifest: return []
    rows=service.gateway.read('transaction',user_id,'payee.resolve',name=None,recipient_id=None)
    return [{k:r[k] for k in ('id','name')}|{'currency':r.get('currency','CNY')} for r in rows]
