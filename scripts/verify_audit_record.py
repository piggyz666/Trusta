"""Independent public-only verifier. Requires a public key pinned outside the proof."""
import argparse
import json
from pathlib import Path
from backend.audit_trust.records import verify_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("proof")
    parser.add_argument("--trusted-public-key", required=True)
    parser.add_argument("--expected-checkpoint-hash")
    args = parser.parse_args()
    bundle = json.loads(Path(args.proof).read_text(encoding="utf-8"))
    key = Path(args.trusted_public_key).read_text(encoding="ascii")
    valid = verify_record(bundle, key, args.expected_checkpoint_hash)
    print(json.dumps({"record_verified": valid, "trust_source": "externally_pinned_public_key",
                      "anchor_externally_verified": False}))
    raise SystemExit(0 if valid else 1)


if __name__ == "__main__":
    main()
