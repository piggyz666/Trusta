"""Operator read-only verifier; never opens bank DB, wallet or sends transactions."""
import argparse
import json
import os
from pathlib import Path
from backend.config import Config
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.independent_anchor import verify_two_rpcs


def main():
    parser=argparse.ArgumentParser()
    for key in ('checkpoint','public-key','contract','publisher','tx-hash','output'): parser.add_argument('--'+key,required=True)
    parser.add_argument('--confirmations',type=int,default=3);parser.add_argument('--code-hash');args=parser.parse_args()
    Config.local() # Load operator configuration; do not emit credentials or URLs.
    urls=[os.getenv(k,'') for k in ('ANCHOR_RPC_URL','ANCHOR_VERIFY_RPC_URL')]
    if not all(urls) or urls[0]==urls[1]: raise SystemExit('Two distinct Sepolia RPC endpoints are required for cross-provider verification.')
    proof=verify_two_rpcs(*(EthereumRPC(url) for url in urls),checkpoint=json.loads(Path(args.checkpoint).read_text(encoding='utf-8')),
        trusted_public_key=Path(args.public_key).read_text(encoding='ascii'),contract=args.contract,publisher=args.publisher,
        tx_hash=args.tx_hash,confirmations=args.confirmations,code_hash=args.code_hash)
    proof['status']='REAL_EXTERNAL_VERIFIED' if proof['external_blockchain_verified'] else 'FAILED'
    Path(args.output).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':proof['status'],'external_blockchain_verified':proof['external_blockchain_verified']}))
    raise SystemExit(0 if proof['external_blockchain_verified'] else 1)

if __name__=='__main__':main()
