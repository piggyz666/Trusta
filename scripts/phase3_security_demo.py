"""Phase 3.3: consented memory, messaging epoch rotation, real HTTP sandbox scenarios."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from backend.config import Config
from backend.domain import digest, uid
from backend.server import create_server
from backend.service import TrustaService


def demo(directory):
    root = Path(directory)
    llm = root / "llm.toml"
    llm.write_text('[gateway]\nmode="assisted"\nversion="phase3.3-demo"\n[[providers]]\nname="offline"\nkind="mock"\nmax_sensitivity="restricted"\n', encoding="utf-8")
    config = Config(db_path=str(root / "phase3.3.sqlite3"), signing_key=b"isolated-phase3.3-key", llm_config_path=str(llm))
    service = TrustaService(config)
    server = create_server(service, "isolated-phase3.3-token", 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        outbound = Request(f"http://127.0.0.1:{server.server_port}" + path, data=None if body is None else json.dumps(body).encode(),
                           headers={"Authorization": "Bearer isolated-phase3.3-token", "Content-Type": "application/json"})
        with urlopen(outbound, timeout=15) as response:
            return json.loads(response.read())

    def task(text):
        return request("/api/tasks", {"text": text})

    def turn(conv, text):
        revision = request(f"/api/conversations/{conv}")["revision"]
        return request(f"/api/conversations/{conv}/turns", {"text": text, "revision": revision, "turn_id": uid()})

    try:
        original_finance = task("分析2026-09消费")
        original_wealth = task("推荐适合我的理财产品")
        try:
            request("/api/memory/preferences", {"key": "explanation_style", "value": "detailed", "consent": False})
            raise RuntimeError("Missing consent was accepted")
        except HTTPError as exc:
            if exc.code != 400:
                raise
            exc.close()
        preference = request("/api/memory/preferences", {"key": "explanation_style", "value": "detailed", "consent": True})
        risk = request("/api/memory/risk-reference", {"consent": True})
        conv = request("/api/conversations", {})["conversation_id"]
        context_ref = request("/api/memory/conversation-reference", {"conversation_id": conv, "consent": True})
        clarification = turn(conv, "帮我给张三转账")
        pending = turn(conv, "两千元")["task"]
        original_hash = digest(pending["proposed_actions"][0])
        rotation = service.message_keys.rotate(0)  # Local operator action; no customer rotation endpoint.
        service.message_keys.revoke(1)
        restored = TrustaService(config)
        persisted_memory = restored.memory.list("demo")
        done = request(f"/api/tasks/{pending['task_id']}/authorize", {"action_hash": original_hash, "approve": True})
        balance = request("/api/balance")["balance"]
        finance, wealth = task("分析2026-09消费"), task("推荐适合我的理财产品")
        status = request("/api/platform/security")
        if not (clarification["status"] == "CLARIFICATION" and done["status"] == "COMPLETED"
                and done["verification"]["status"] == "VERIFIED" and digest(done["proposed_actions"][0]) == original_hash
                and balance == 800000 and request("/api/balance")["balance"] == balance
                and digest(finance["result"]) == digest(original_finance["result"])
                and digest(wealth["result"]) == digest(original_wealth["result"])
                and "大额核对提示" in finance["presentation"]["text"] and "最低金额" in wealth["presentation"]["text"]
                and len(persisted_memory) == 3 and risk["value"] is None and context_ref["value"] is None
                and status["messages"]["current_version"] == 2 and len(status["registry"]["agents"]) == 5
                and all(not item["enabled"] for item in status["extensions"].values())):
            raise RuntimeError("Phase 3.3 demo failed")
        request(f"/api/memory/{preference['memory_id']}/forget", {})
        forgotten_finance = task("分析2026-09消费")
        removed_count = request("/api/memory/clear", {})["forgotten_count"]
        persisted = TrustaService(config)
        if not (not forgotten_finance["presentation"]["memory_refs"]
                and "大额核对提示" not in forgotten_finance["presentation"]["text"]
                and persisted.memory.list("demo") == [] and persisted.audit.verify_chain()
                and persisted.get_task(done["task_id"])["receipt"] == done["receipt"]
                and persisted.db.one("SELECT COUNT(*) n FROM transactions")["n"] == 1):
            raise RuntimeError("Deletion/restart verification failed")
        return {"inference": "OFFLINE_FIXTURES_ONLY",
                "MEMORY": {"explicit_consent_required": True, "persisted_before_delete": len(persisted_memory),
                           "risk_and_context_store_references_only": True, "forget_affects_new_explanations": True,
                           "remaining_after_clear": 0, "references_cleared": removed_count},
                "COMMUNICATION": {"schema_version": 2, "active_key_version": rotation["current_version"],
                                  "previous_key_revoked": True, "agent_count": 5, "extensions": status["extensions"]},
                "TRANSFER": {"status": done["status"], "verification": done["verification"]["status"],
                             "amount_cents": 200000, "balance_cents": balance, "action_hash_unchanged": True, "transaction_count": 1},
                "BILL_ANALYSIS": {"net_spend_cents": finance["result"]["summary"]["net_spend"],
                                  "statistics_unchanged": True, "presentation": finance["presentation"]["text"]},
                "WEALTH_RECOMMENDATION": {"bank_risk_level": wealth["result"]["customer_risk_level"],
                                         "product_ids": [p["product_id"] for p in wealth["result"]["recommendations"]],
                                         "recommendations_unchanged": True, "presentation": wealth["presentation"]["text"]},
                "audit_chain_valid": True, "read_only_balance_unchanged": True, "restart_receipt_preserved": True}
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        report = demo(directory)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
