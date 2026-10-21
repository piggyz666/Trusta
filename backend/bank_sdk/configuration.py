import os
import tomllib
from pathlib import Path
from backend.domain import Rejected
from backend.bank_sdk.contracts import ExternalBankSafeMode
from backend.bank_sdk.starling import ReferenceBankAdapter
from backend.bank_sdk.transport import BankTransport
from backend.bank_sdk.auth import StarlingAuth

def reference_from_config(path, db):
    try:
        raw=tomllib.loads(Path(path).read_text(encoding='utf-8'))
        if raw.get('provider')=='revolut':
            from backend.bank_sdk.revolut import RevolutReadOnlyAdapter, decimal_json
            from backend.bank_sdk.revolut_auth import RevolutAuth, SANDBOX
            from backend.audit import AuditEngine
            allowed={'provider','environment','customer_id','client_id','issuer','key_path',
                     'certificate_path','token_path','writes_enabled','page_size','max_pages',
                     'transfer_profile','source_accounts','payees','maximum_amount'}
            transfer=raw.get('transfer_profile',False)
            if type(transfer) is not bool or type(raw.get('writes_enabled',False)) is not bool: raise ValueError()
            if (set(raw)-allowed or raw.get('environment','sandbox')!='sandbox'
                    or not transfer and (raw.get('writes_enabled',False) or set(raw)&{'source_accounts','payees','maximum_amount'})): raise ValueError()
            transport=BankTransport(SANDBOX,response_decoder=decimal_json)
            resolve=lambda name: str((Path(path).resolve().parent/Path(raw[name])).resolve())
            if transfer:
                from backend.bank_sdk.revolut_auth import RevolutTransferAuth
                from backend.bank_sdk.revolut_transfer import RevolutTransferAdapter
                for name in ('source_accounts','payees'):
                    if name in raw and (not isinstance(raw[name],list) or any(not isinstance(v,str) for v in raw[name])): raise ValueError()
            auth_class=RevolutTransferAuth if transfer else RevolutAuth
            auth=auth_class(transport,raw['client_id'],resolve('key_path'),resolve('certificate_path'),
                            resolve('token_path'),raw.get('issuer','trusta.localhost'),AuditEngine(db))
            if transfer:
                return RevolutTransferAdapter(db,transport,auth,raw['customer_id'],
                    ExternalBankSafeMode('sandbox',raw.get('writes_enabled',False),frozenset(raw.get('source_accounts',[])),
                                        frozenset(raw.get('payees',[])),raw.get('maximum_amount',100)),
                    page_size=raw.get('page_size',1000),max_pages=raw.get('max_pages',20))
            return RevolutReadOnlyAdapter(db,transport,auth,raw['customer_id'],
                                         raw.get('page_size',1000),raw.get('max_pages',20))
        if set(raw)-{'provider','environment','customer_id','token_env','key_path','key_id','writes_enabled','source_accounts','payees','maximum_amount','scheduled_enabled'} or raw.get('provider')!='starling': raise ValueError()
        environment=raw.get('environment','sandbox')
        if environment not in {'sandbox','production'}: raise ValueError()
        for key in ('writes_enabled','scheduled_enabled'):
            if key in raw and type(raw[key]) is not bool: raise ValueError()
        customer=raw['customer_id']
        if not isinstance(customer,str) or not customer: raise ValueError()
        mode=ExternalBankSafeMode(environment,raw.get('writes_enabled',False),frozenset(raw.get('source_accounts',[])),frozenset(raw.get('payees',[])),raw.get('maximum_amount',100),raw.get('scheduled_enabled',False))
        endpoint={'sandbox':'https://api-sandbox.starlingbank.com','production':'https://api.starlingbank.com'}[environment]
        return ReferenceBankAdapter(db,BankTransport(endpoint),StarlingAuth(raw.get('token_env','BANK_ACCESS_TOKEN'),raw.get('key_path',''),raw.get('key_id','')),customer,mode)
    except Exception:
        raise Rejected('INVALID_BANK_PROVIDER_CONFIGURATION') from None
