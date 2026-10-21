"""Credential presence only by default; optional bounded READ-ONLY Sepolia probe."""
import argparse
import json
import os
from pathlib import Path
from backend.config import Config
from backend.audit_trust.external_anchor import EthereumRPC


def check(probe=False):
    cfg=Config.local()
    present={k:bool(os.getenv(k)) for k in ('ANCHOR_RPC_URL','ANCHOR_VERIFY_RPC_URL','ANCHOR_CONTRACT_ADDRESS','ANCHOR_WALLET_PRIVATE_KEY')}
    blockers=[k+' is not configured locally' for k,v in present.items() if not v]
    if not cfg.blockchain_anchor_enabled:blockers.append('Async Anchor Worker external adapter is disabled')
    report={'status':'BLOCKED_BY_EXTERNAL_CREDENTIAL' if blockers else 'NOT_TESTED','credential_presence':present,
        'worker_external_enabled':cfg.blockchain_anchor_enabled,'network':'Sepolia','expected_chain_id':11155111,
        'chain_id_verified':False,'wallet_balance_verified':False,'contract_verified':False,
        'external_blockchain_verified':False,'read_only_probe_requested':probe,'blockers':blockers,
        'broadcasts':0,'deployments':0,'test_eth_spent':0,
        'next_action':'Configure user-controlled credentials locally; approve a specific deployment/commit before any broadcast.'}
    if probe and present['ANCHOR_RPC_URL']:
        try:
            rpc=EthereumRPC(os.environ['ANCHOR_RPC_URL']);chain=int(rpc.call('eth_chainId',[]),16)
            report['observed_chain_id']=chain;report['chain_id_verified']=chain==11155111
            if chain!=11155111:raise ValueError('Unexpected chain')
            if present['ANCHOR_CONTRACT_ADDRESS']:
                from eth_utils import is_address
                address=os.environ['ANCHOR_CONTRACT_ADDRESS']
                if not is_address(address):raise ValueError('Invalid contract address')
                report['contract_verified']=rpc.call('eth_getCode',[address,'latest']) not in {'0x','0x0'}
            if present['ANCHOR_WALLET_PRIVATE_KEY']:
                from eth_account import Account
                address=Account.from_key(os.environ['ANCHOR_WALLET_PRIVATE_KEY']).address
                balance=int(rpc.call('eth_getBalance',[address,'latest']),16)
                report.update(wallet_address=address,wallet_balance_wei=balance,wallet_balance_verified=True)
        except Exception:
            report['probe_result']='FAILED; RPC or public configuration unavailable/invalid (credentials omitted)'
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--read-only-probe',action='store_true')
    parser.add_argument('--output',default='docs/external-anchor-validation.json');args=parser.parse_args()
    report=check(args.read_only_probe);Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'external_blockchain_verified':False,'blockers':report['blockers']}))

if __name__=='__main__':main()
