"""Offline verification; registry must be obtained and pinned separately."""
import argparse
import json
from pathlib import Path
from backend.runtime.handoff_evidence import verify_bundle

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('bundle',type=Path);parser.add_argument('--trusted-registry',type=Path,required=True)
    args=parser.parse_args();registry=json.loads(args.trusted_registry.read_text(encoding='utf-8'))
    valid=verify_bundle(json.loads(args.bundle.read_text(encoding='utf-8')),
        {v['key_id']:v for v in registry['keys']},{v['agent_id']:v for v in registry['agents']})
    print(json.dumps({'verified':valid,'scope':'RUNTIME_CAUSAL_ATTESTATION_NOT_BANK_AUTHORIZATION'}))
    raise SystemExit(0 if valid else 1)

if __name__=='__main__':main()
