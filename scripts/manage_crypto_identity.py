"""Local operator only; no HTTP Agent/key/capability administration endpoint."""
import argparse
import json
import threading
from backend.config import Config
from backend.domain import Rejected
from backend.audit import AuditEngine
from backend.persistence import Database
from backend.security.registry import CapabilityRegistry
from backend.security.crypto_registry import CryptoIdentityRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["supervisor", "planner", "transaction", "finance", "wealth", "service", "audit-checkpoint"])
    parser.add_argument("--rotate", action="store_true")
    parser.add_argument("--grace-seconds", type=int, default=300)
    parser.add_argument("--revoke-key")
    parser.add_argument("--status", choices=["ACTIVE", "SUSPENDED", "REVOKED"])
    parser.add_argument("--sync-capabilities", action="store_true", help="After a reviewed code grant change, update its identity commitment")
    args = parser.parse_args()
    if sum((args.rotate, args.revoke_key is not None, args.status is not None, args.sync_capabilities)) != 1 or (not args.revoke_key and not args.agent):
        parser.error("select exactly one operator action; --agent is required for rotate/status")
    config = Config.local()
    if config.communication_version != 3:
        raise Rejected("PHASE4_DISABLED")
    db = Database(config.db_path)
    audit = AuditEngine(db)
    if not audit.verify_chain():
        raise Rejected("AUDIT_CHAIN_INVALID")
    registry = CryptoIdentityRegistry(db, audit, CapabilityRegistry(config.banking_version), config, threading.RLock(), allow_capability_sync=args.sync_capabilities)
    if args.rotate:
        result = registry.rotate(args.agent, args.grace_seconds)
    elif args.revoke_key:
        registry.revoke_key(args.revoke_key)
        result = {"revoked_key_id": args.revoke_key}
    elif args.sync_capabilities:
        result = registry.sync_capabilities(args.agent)
    else:
        result = registry.set_status(args.agent, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
