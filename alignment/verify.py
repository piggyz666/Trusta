"""Independent public verifier. Requires explicit expected hashes and pinned key."""
import argparse
import json
from pathlib import Path
from alignment.evaluator import verify_execution


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proof', type=Path, required=True)
    p.add_argument('--pinned-key', type=Path, required=True)
    p.add_argument('--expected-plan-hash', required=True)
    p.add_argument('--expected-request-hash', required=True)
    a = p.parse_args()
    try:
        verified = verify_execution(json.loads(a.proof.read_text(encoding='utf-8')),
            a.pinned_key.read_text(encoding='utf-8'), expected_plan_hash=a.expected_plan_hash,
            expected_request_hash=a.expected_request_hash)
    except (OSError, ValueError): verified = False
    print(json.dumps({'verified':verified, 'private_key_required':False,'database_required':False}))
    return 0 if verified else 1


if __name__ == '__main__': raise SystemExit(main())
