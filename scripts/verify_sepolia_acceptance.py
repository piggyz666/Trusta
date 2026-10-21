"""Third-party read-only deployment + commit verifier. Never opens Core DB/keys."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from backend.domain import Rejected, canonical, digest
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.independent_anchor import verify_two_rpcs
from backend.audit_trust.public_ingress import verify_deployment, verify_signed_fields
from backend.audit_trust.records import verify_record
from scripts.estimate_sepolia_costs import load_local_environment


def verify(primary, secondary, plan, deploy_hash, commit_hash, bundle, pinned, commit_plan=None):
    if urlsplit(primary.url).hostname==urlsplit(secondary.url).hostname:
        raise Rejected('ANCHOR_RPC_INDEPENDENCE_REQUIRED')
    first=verify_deployment(primary,plan,deploy_hash);second=verify_deployment(secondary,plan,deploy_hash)
    result={'schema_version':1,'scope':'ETHEREUM_SEPOLIA_PUBLIC','external_blockchain_verified':False,
        'production_compatible':False,'deployment_primary':first,'deployment_independent':second,
        'business_record_verified':verify_record(bundle,pinned),'receipt_commitment':digest(bundle.get('receipt'))}
    same=all(first.get(k)==second.get(k) for k in ('contract','code_hash','block_number','block_hash','receipt_commitment'))
    result['deployment_rpc_consistent']=same
    if not first['verified'] or not second['verified'] or not same or not result['business_record_verified']:
        result['reason']='DEPLOYMENT_OR_BUSINESS_EVIDENCE_NOT_VERIFIED';return result
    proof=verify_two_rpcs(primary,secondary,checkpoint=bundle['checkpoint'],trusted_public_key=pinned,
        contract=first['contract'],publisher=plan['sender'],tx_hash=commit_hash,confirmations=3,code_hash=first['code_hash'])
    result['root_commit']=proof
    from scripts.sepolia_metamask_gateway import commit_data
    result['commit_exact_plan_bound']=bool(commit_plan and commit_plan['kind']=='commit'
        and commit_plan['sender']==plan['sender'] and commit_plan['transaction'].get('to','').lower()==first['contract'].lower()
        and commit_plan['transaction']['data']==commit_data(bundle,pinned)
        and all(verify_signed_fields(rpc.call('eth_getTransactionByHash',[commit_hash]),commit_plan,commit_hash) for rpc in (primary,secondary)))
    result['external_blockchain_verified']=proof['external_blockchain_verified'] and result['commit_exact_plan_bound']
    # Cryptographic chain acceptance is separate from universal wallet routing
    # compatibility and from proving a number of outbound socket attempts.
    result['reason']='VERIFIED' if result['external_blockchain_verified'] else 'COMMIT_NOT_VERIFIED'
    return result


def main():
    p=argparse.ArgumentParser()
    for name in ('deployment-plan','deployment-tx','commit-plan','commit-tx','bundle','public-key','output'):p.add_argument('--'+name,required=True)
    a=p.parse_args();load_local_environment()
    rpc=[EthereumRPC(os.environ[name],timeout=15) for name in ('ANCHOR_RPC_URL','ANCHOR_VERIFY_RPC_URL')]
    result=verify(*rpc,json.loads(Path(a.deployment_plan).read_text(encoding='utf-8')),a.deployment_tx,a.commit_tx,
        json.loads(Path(a.bundle).read_text(encoding='utf-8')),Path(a.public_key).read_text(encoding='utf-8'),
        json.loads(Path(a.commit_plan).read_text(encoding='utf-8')))
    Path(a.output).write_text(canonical(result)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('scope','external_blockchain_verified','production_compatible','reason')}))
    raise SystemExit(0 if result['external_blockchain_verified'] else 1)

if __name__=='__main__':main()
