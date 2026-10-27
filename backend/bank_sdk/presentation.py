"""Authenticated bank catalogue for the local UI; no execution authority."""
from backend.bank_sdk.contracts import BankRequest


def bank_overview(service,user_id):
    provider=service.gateway._adapter.provider
    manifest=provider.manifest
    from backend.bank_sdk.discovery import customer_accounts
    accounts=[{k:a[k] for k in ('id','currency','balance','frozen')}|{'name':a['name'] or a['currency']+'账户'}
              for a in customer_accounts(service,user_id)]
    safe=getattr(provider,'safe_mode',None)
    return {'provider':manifest.provider,'environment':manifest.environment,'accounts':accounts,
            'capabilities':sorted(manifest.capabilities),
            'writes_enabled':manifest.environment=='local' or bool(safe and safe.enabled),
            'maximum_payment_minor_units':safe.maximum_amount if safe else None}


def bank_payees(service,user_id):
    if 'payee.resolve' not in service.gateway._adapter.manifest: return []
    rows=service.gateway.read('transaction',user_id,'payee.resolve',name=None,recipient_id=None)
    return [{k:r[k] for k in ('id','name')}|{'currency':r.get('currency','CNY')} for r in rows]
