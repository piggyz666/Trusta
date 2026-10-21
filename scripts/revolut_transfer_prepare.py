"""Prepare one 0.01 native-currency Sandbox proposal; never submit a payment."""
from pathlib import Path
import argparse
import json
import sys
import time
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.bank_sdk.contracts import BankRequest
from backend.domain import Rejected
from scripts.revolut_server import build


def prepare(profile):
    profile=Path(profile).resolve()
    raw=tomllib.loads(profile.read_text(encoding='utf-8'))
    if (raw.get('provider')!='revolut' or raw.get('environment')!='sandbox'
            or raw.get('transfer_profile') is not True or raw.get('customer_id')!='demo'
            or len(raw.get('source_accounts',[]))!=1 or len(raw.get('payees',[]))!=1):
        raise Rejected('SANDBOX_DEMO_CONFIGURATION_REQUIRED')
    s,_=build(profile);p=s.gateway._adapter.provider
    # This validates the requested bank-consent marker, not payment acceptance.
    # No HTTP payment call occurs: the only network preflight requests are reads
    # and, if needed, OAuth refresh. Bank PAY becomes proven only by a real /pay.
    p.auth.headers('POST','/api/1.0/pay',b'{}')
    source=s.gateway.read('transaction','demo','account.get',account_id=raw['source_accounts'][0])
    payees=s.gateway.read('transaction','demo','payee.resolve',name=None,recipient_id=raw['payees'][0])
    if (source['frozen'] or source['balance']<1 or len(payees)!=1
            or payees[0]['currency']!=source['currency']):raise Rejected('SANDBOX_DEMO_ROUTE_UNAVAILABLE')
    raw['writes_enabled']=True
    values=lambda v: ('true' if v else 'false') if isinstance(v,bool) else json.dumps(v,ensure_ascii=True)
    temporary=profile.with_suffix('.tmp')
    temporary.write_text('\n'.join(k+' = '+values(v) for k,v in raw.items())+'\n',encoding='utf-8')
    temporary.replace(profile)
    # Only a fresh draft is created. No authorize(), Gateway.execute() or /pay.
    for task in reversed(s.blackboard.list('demo')):
        actions=task['proposed_actions']
        if task['status']=='WAITING_USER' and len(actions)==1:
            a=actions[0]
            if (a['source_account_id']==source['id'] and a['recipient_id']==payees[0]['id']
                    and a['amount']==1 and a['currency']==source['currency'] and a['expires_at']>time.time()):return task
    return s.create_task('从 '+source['id']+' 给 '+payees[0]['name']+' 转 0.01 '+source['currency'],source_account_id=source['id'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config/bank.revolut-transfer.local.toml'))
    args=parser.parse_args()
    try:
        task=prepare(args.config)
        print(json.dumps({'status':task['status'],'task_id':task['task_id'],'bank_pay_calls':0,'next_step':'Open transfer UI history, review recipient/account/0.01 amount, then explicitly authorize.'}))
    except Rejected as exc:
        print(json.dumps({'status':'NOT_PREPARED','reason':exc.code,'bank_pay_calls':0}))
        raise SystemExit(1)


if __name__=='__main__':main()
