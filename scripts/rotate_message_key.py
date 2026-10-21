"""Local operator tool. Messaging epochs only; never changes bank approvals or contract keys."""
import argparse
import json
from backend.config import Config
from backend.service import TrustaService
from backend.domain import Rejected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grace-seconds", type=int, default=300)
    parser.add_argument("--revoke-version", type=int)
    args = parser.parse_args()
    config = Config.local()
    if config.communication_version != 2:
        raise Rejected("V2_OPERATOR_ONLY_USE_MANAGE_CRYPTO_IDENTITY")
    service = TrustaService(config)
    result = service.message_keys.revoke(args.revoke_version) if args.revoke_version is not None else service.message_keys.rotate(args.grace_seconds)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
