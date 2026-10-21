"""Read-only deployment readiness; no signer call, real signature or broadcast."""
import argparse
import json
import os
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eth_utils import keccak, to_checksum_address
import rlp
from scripts.estimate_sepolia_costs import assess, ReadOnlyRPC, runtime_for_publisher, calldata
from backend.audit_trust.transaction_safety import (
    require_sepolia, prepare_transaction, AnchorFeePolicy, plan_hash, signing_ready,
    load_anchor_artifact, CREATION_SHA256, SOURCE_SHA256,
)
from backend.domain import Rejected


def check():
    measured=assess()
    if measured.get('status')!='RPC_ESTIMATED':
        return {'status':'READINESS_BLOCKED','read_only_estimation':measured,
                'real_signatures':0,'broadcasts':0,'test_eth_spent':0}
    rpc=ReadOnlyRPC(os.environ['ANCHOR_RPC_URL'])
    artifact=load_anchor_artifact();policy=AnchorFeePolicy()
    sender=measured['wallet_public_address']
    if rpc.call('eth_getCode',[sender,'latest']) not in {'0x','0x0'}:
        raise Rejected('ANCHOR_EOA_SIGNER_REQUIRED')
    plan=prepare_transaction(rpc,sender,'0x'+artifact['bin'],'deploy',policy=policy)
    runtime=runtime_for_publisher(artifact,sender)
    expected_codehash='0x'+keccak(hexstr=runtime).hex()
    predicted=to_checksum_address(keccak(rlp.encode([bytes.fromhex(sender[2:]),plan['transaction']['nonce']]))[-20:])
    commit_gas=(measured['commit_rpc_estimate_gas']*120+99)//100
    if commit_gas>policy.commit_max_gas:
        raise Rejected('ANCHOR_GAS_LIMIT')
    fee=plan['transaction']['maxFeePerGas']
    commit_worst=commit_gas*fee
    if commit_worst>policy.commit_max_cost:
        raise Rejected('ANCHOR_TOTAL_FEE_LIMIT')
    total=plan['worst_cost_wei']+commit_worst
    balance=min(plan['balance_wei'],int(Decimal(measured['wallet_balance_sepolia_eth'])*10**18))
    if balance<total+policy.minimum_balance:
        raise Rejected('ANCHOR_BALANCE_RESERVE')
    secondary=os.getenv('ANCHOR_VERIFY_RPC_URL','')
    independent={'configured':bool(secondary),'chain_verified':False,'distinct_endpoint':False,
                 'operator_independence':'Must be an independently operated provider; URL difference alone is insufficient.'}
    blockers=['Exact transaction approval not granted; signing/broadcast remain disabled.',
              'Contract not deployed: on-chain code/publisher/receipt/readback cannot yet be verified.']
    if secondary:
        from urllib.parse import urlsplit
        independent['distinct_endpoint']=urlsplit(secondary).hostname!=urlsplit(os.environ['ANCHOR_RPC_URL']).hostname
        try:
            require_sepolia(ReadOnlyRPC(secondary));independent['chain_verified']=True
        except Rejected:
            blockers.append('Independent RPC chain/read check failed.')
        if not independent['distinct_endpoint']:blockers.append('Independent RPC shares primary provider host.')
    else:
        blockers.append('ANCHOR_VERIFY_RPC_URL missing; configure independent provider for acceptance.')
    signing=signing_ready()
    if not signing['encrypted_keystore_configured'] and not signing['legacy_private_key_environment_present']:
        blockers.append('Controlled local signer not configured; only public wallet address is available.')
    report={'status':'PREPARATION_COMPLETE_EXECUTION_NOT_AUTHORIZED','chain_id':11155111,
            'wallet_public_address':sender,'wallet_balance_wei':balance,
            'unsigned_deployment_plan':plan,'unsigned_plan_hash':plan_hash(plan),
            'predicted_contract_address_nonce_bound':predicted,
            'artifact_creation_sha256':CREATION_SHA256,'source_sha256':SOURCE_SHA256,
            'expected_publisher':sender,'expected_runtime_keccak256':expected_codehash,
            'publisher_match_scope':'Constructor/patched runtime simulation only; no on-chain contract yet.',
            'commit_estimate_gas':measured['commit_rpc_estimate_gas'],'commit_gas_with_margin':commit_gas,
            'commit_estimation_scope':measured['commit_estimation_scope'],
            'policy':asdict(policy),'deploy_worst_cost_wei':plan['worst_cost_wei'],
            'commit_worst_cost_wei':commit_worst,'combined_quote_worst_cost_wei':total,
            'combined_policy_absolute_fee_ceiling_wei':policy.deploy_max_cost+policy.commit_max_cost,
            'balance_remaining_after_quote_worst_wei':balance-total,
            'balance_covers_policy_ceilings_plus_reserve':balance>=policy.deploy_max_cost+policy.commit_max_cost+policy.minimum_balance,
            'signing':signing,'signer_address_proven_by_real_signature':False,
            'independent_rpc':independent,'blockers':blockers,'real_signatures':0,'broadcasts':0,
            'external_deployments':0,'test_eth_spent':0,'read_only_estimation':measured,
            'quote_ttl_note':'120 seconds; regenerate and approve exact plan before future execution. No cached quote may be silently repriced.'}
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=check()
    except Rejected as exc:report={'status':'READINESS_BLOCKED','reason':exc.code,'real_signatures':0,'broadcasts':0,'test_eth_spent':0}
    except Exception as exc:report={'status':'READINESS_BLOCKED','failure_type':type(exc).__name__,'real_signatures':0,'broadcasts':0,'test_eth_spent':0}
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','reason','failure_type','chain_id','wallet_public_address','real_signatures','broadcasts','test_eth_spent') if k in report}))
    raise SystemExit(0 if report['status']=='PREPARATION_COMPLETE_EXECUTION_NOT_AUTHORIZED' else 1)


if __name__=='__main__':main()
