"""Three original HTTP scenarios plus V3 identity, key/nonce persistence and verifiable receipt."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
from urllib.request import Request, urlopen
from backend.config import Config
from backend.domain import Rejected, digest
from backend.service import TrustaService
from backend.server import create_server
from backend.audit_trust.records import verify_record


def demo(directory):
    root = Path(directory)
    config = Config(db_path=str(root/"phase4.sqlite3"), signing_key=b"isolated-phase4-demo-root-key", communication_version=3)
    service = TrustaService(config)
    server = create_server(service, "isolated-phase4-token", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def request(path, body=None):
        req = Request(base+path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": "Bearer isolated-phase4-token", "Content-Type": "application/json"})
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read())
    try:
        transfer = request("/api/tasks", {"text": "给张三转2000元"})
        action = transfer["proposed_actions"][0]
        action_hash = digest(action)
        # An accepted extra message is used only to verify nonce persistence; it adds no financial authority.
        message = service.messages.sign("transaction", "supervisor", transfer["task_id"], "T05", "ACTION_PROPOSED",
                                        f"task:{transfer['task_id']}:agent_results:transaction:ACTION_PROPOSED", action)
        service.accept_message(message, action, "supervisor")
        previous_key = message["sender_key_id"]
        rotated = service.crypto_registry.rotate("transaction")
        persisted = TrustaService(config)
        replay_blocked = False
        try:
            persisted.messages.verify(message, action, "supervisor")
        except Rejected as exc:
            replay_blocked = exc.code == "REPLAY_BLOCKED"
        persisted.crypto_registry.revoke_key(previous_key)
        transfer = request(f"/api/tasks/{transfer['task_id']}/authorize", {"action_hash": action_hash, "approve": True})
        before_reads = request("/api/balance")["balance"]
        finance = request("/api/tasks", {"text": "分析2026-09消费"})
        wealth = request("/api/tasks", {"text": "推荐适合我的理财产品"})
        after_reads = request("/api/balance")["balance"]
        # This explicit maintenance call represents the separate worker, AFTER bank completion.
        worker_report = service.anchors.run_once()
        receipt = transfer["receipt"]
        bundle = request("/api/audit/proof?receipt_id="+receipt["receipt_id"])
        public_key = bundle["signer_public_key"]
        independently_verified = verify_record(bundle, public_key, digest(bundle["checkpoint"]))
        tampered = json.loads(json.dumps(bundle))
        tampered["receipt"]["approved_action_hash"] = "0"*64
        tamper_detected = not verify_record(tampered, public_key)
        rebuilt = TrustaService(config)
        restarted_proof = rebuilt.records.query("demo", transaction_id=receipt["bank_transaction_id"])
        tx_count = rebuilt.db.one("SELECT COUNT(*) n FROM transactions")["n"]
        registry_preserved = rebuilt.crypto_registry.resolve("transaction")["key_id"] == rotated["key_id"]
        key_revoked = next(item for item in rebuilt.crypto_registry.snapshot()["keys"] if item["key_id"] == previous_key)["status"] == "REVOKED"
        receipt_preserved = rebuilt.get_task(transfer["task_id"])["receipt"] == receipt
        if not (all(task["status"] == "COMPLETED" for task in (transfer, finance, wealth))
                and transfer["verification"]["status"] == "VERIFIED" and transfer["execution"]["transaction"]["status"] == "SETTLED"
                and before_reads == after_reads == 800000 and tx_count == 1 and replay_blocked and registry_preserved and key_revoked
                and receipt_preserved and independently_verified and tamper_detected and rebuilt.audit.verify_chain()
                and verify_record(restarted_proof, public_key) and rebuilt.anchors.verify_anchor(bundle["checkpoint"]["checkpoint_id"])):
            raise RuntimeError("Phase4 original-scene/crypto/restart checks failed")
        # Public test key is exported for the CLI demo. In production this must be obtained/pinned independently.
        (root/"proof.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        (root/"audit-public.pem").write_text(public_key, encoding="ascii")
        return {"schema_version": 1, "communication_version": 3, "algorithm": "ECDSA-P256-SHA256",
                "TRANSFER": {"request": "给张三转2000元", "status": transfer["status"], "settlement": "SETTLED",
                             "verification": transfer["verification"]["status"], "amount_cents": action["amount"],
                             "balance_cents": before_reads, "transaction_count": tx_count, "action_hash_unchanged": digest(action) == action_hash,
                             "receipt_id": receipt["receipt_id"], "bank_transaction_id": receipt["bank_transaction_id"]},
                "BILL_ANALYSIS": {"request": "分析2026-09消费", "status": finance["status"],
                                  "net_spend_cents": finance["result"]["summary"]["net_spend"],
                                  "anomaly_count": len(finance["result"]["anomalies"]["flags"])},
                "WEALTH_RECOMMENDATION": {"request": "推荐适合我的理财产品", "status": wealth["status"],
                                         "bank_risk_level": wealth["result"]["customer_risk_level"],
                                         "product_ids": [item["product_id"] for item in wealth["result"]["recommendations"]],
                                         "financial_write": False},
                "restart": {"registry_preserved": registry_preserved, "key_rotation_preserved": registry_preserved,
                            "revocation_preserved": key_revoked, "nonce_replay_blocked": replay_blocked,
                            "checkpoint_proof_verified": verify_record(restarted_proof, public_key), "receipt_preserved": receipt_preserved},
                "record_proof": bundle, "independent_proof_verification": independently_verified,
                "receipt_tampering_detected": tamper_detected, "anchor_worker": worker_report,
                "anchor_status": bundle["anchor"]["status"], "anchor_verified_locally": bundle["anchor_verified"],
                "blockchain_externally_verified": False, "did_implemented": False, "zkp_implemented": False,
                "read_only_balance_unchanged": before_reads == after_reads, "audit_chain_valid": rebuilt.audit.verify_chain()}
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/phase4-demo.json")
    parser.add_argument("--proof-output", default="docs/phase4-record-proof.json")
    parser.add_argument("--public-key-output", default="docs/phase4-audit-public.pem")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="trusta-phase4-demo-") as directory:
        report = demo(directory)
    for filename, value in ((args.output, report), (args.proof_output, report["record_proof"])):
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    Path(args.public_key_output).write_text(report["record_proof"]["signer_public_key"], encoding="ascii")
    print(json.dumps({k:v for k,v in report.items() if k not in {"record_proof", "anchor_worker"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
