"""Separate audit maintenance process: never called by authorize/execute."""
import argparse
import json
import time
from backend.config import Config
from backend.domain import Rejected


def main():
    parser = argparse.ArgumentParser(description="签名审计检查点与本地开发锚定；独立于金融请求")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--retry-checkpoint")
    args = parser.parse_args()
    if not 1 <= args.interval <= 60:
        parser.error("interval must be 1..60 seconds")
    # The maintenance process does not recover live workflow tasks or seed the bank.
    from backend.audit_trust.checkpoints import MerkleAudit
    from backend.audit_trust.anchor import AnchorWorker,configured_anchor
    from backend.audit import AuditEngine
    from backend.persistence import Database
    from backend.security.crypto_registry import CryptoIdentityRegistry
    from backend.security.registry import CapabilityRegistry
    import threading
    config = Config.local()
    if config.communication_version != 3:
        raise Rejected("PHASE4_DISABLED")
    db, lock = Database(config.db_path), threading.RLock()
    audit = AuditEngine(db)
    if not audit.verify_chain():
        raise Rejected("AUDIT_CHAIN_INVALID")
    identities = CryptoIdentityRegistry(db, audit, CapabilityRegistry(config.banking_version), config, lock)
    checkpoints = MerkleAudit(db, audit, identities, lock)
    adapter = configured_anchor(db,config)
    worker = AnchorWorker(db, checkpoints, adapter)
    if args.retry_checkpoint:
        worker.retry(args.retry_checkpoint)
    try:
        while True:
            report = worker.run_once(args.batch_size)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
