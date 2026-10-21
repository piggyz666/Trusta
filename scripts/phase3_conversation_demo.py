"""Phase 3.2 HTTP dialogue with offline inference fixtures and real Virtual Bank writes."""
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
    llm.write_text('[gateway]\nmode="assisted"\nversion="phase3.2-demo"\n[[providers]]\nname="offline"\nkind="mock"\nmax_sensitivity="restricted"\n', encoding="utf-8")
    config = Config(db_path=str(root / "phase3.2.sqlite3"), signing_key=b"phase3.2-demo-key", llm_config_path=str(llm))
    service = TrustaService(config)
    server = create_server(service, "isolated-phase3.2-token", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, body=None):
        outbound = Request(f"http://127.0.0.1:{server.server_port}" + path,
                           data=None if body is None else json.dumps(body).encode(),
                           headers={"Authorization": "Bearer isolated-phase3.2-token", "Content-Type": "application/json"})
        with urlopen(outbound, timeout=15) as response:
            return json.loads(response.read())
    def conversation():
        return request("/api/conversations", {})["conversation_id"]
    def turn(conversation_id, text):
        revision = request(f"/api/conversations/{conversation_id}")["revision"]
        return request(f"/api/conversations/{conversation_id}/turns", {"turn_id": uid(), "revision": revision, "text": text})
    try:
        conv = conversation()
        clarification = turn(conv, "帮我给张三转账")
        original = turn(conv, "两千元")["task"]
        chat_confirmation = turn(conv, "我确认")
        unchanged = request("/api/balance")["balance"] == 1000000
        revised = turn(conv, "金额改成2500元")["task"]
        try:
            request(f"/api/tasks/{original['task_id']}/authorize", {"action_hash": digest(original["proposed_actions"][0]), "approve": True})
            raise RuntimeError("Old approval was accepted")
        except HTTPError as exc:
            if exc.code != 400:
                raise
            exc.close()
        completed = request(f"/api/tasks/{revised['task_id']}/authorize", {"action_hash": digest(revised["proposed_actions"][0]), "approve": True})
        after_transfer = request("/api/balance")["balance"]
        bill = conversation()
        turn(bill, "帮我看看我的账单")
        finance = turn(bill, "2026年9月")["task"]
        wealth = turn(conversation(), "帮我推荐一些适合我的理财产品")["task"]
        persisted = TrustaService(config)
        if not (clarification["status"] == "CLARIFICATION" and chat_confirmation["task"]["status"] == "WAITING_USER"
                and unchanged and completed["verification"]["status"] == "VERIFIED" and after_transfer == 750000
                and request("/api/balance")["balance"] == after_transfer
                and persisted.db.one("SELECT COUNT(*) n FROM transactions")["n"] == 1
                and all(t["status"] == "COMPLETED" for t in (completed, finance, wealth))
                and finance["result"]["summary"]["net_spend"] == 542500
                and wealth["result"]["customer_risk_level"] == 2
                and persisted.audit.verify_chain()
                and persisted.get_task(completed["task_id"])["receipt"] == completed["receipt"]):
            raise RuntimeError("Conversation demo failed")
        return {"inference": "OFFLINE_FIXTURES_ONLY", "TRANSFER": {
            "clarification": clarification["message"], "chat_confirmation_did_not_execute": unchanged,
            "old_approval_rejected": True, "revised_amount_cents": 250000, "balance_cents": after_transfer,
            "status": completed["status"], "verification": completed["verification"]["status"], "transaction_count": 1},
            "BILL_ANALYSIS": {"status": finance["status"], "net_spend_cents": 542500, "presentation": finance["presentation"]["text"]},
            "WEALTH_RECOMMENDATION": {"status": wealth["status"], "bank_risk_level": 2,
                "product_ids": [p["product_id"] for p in wealth["result"]["recommendations"]], "presentation": wealth["presentation"]["text"]},
            "audit_chain_valid": persisted.audit.verify_chain(), "read_only_balance_unchanged": True,
            "conversation_turns_survive_restart": len(persisted.conversations.get(conv)["turns"]) == 4}
    finally:
        server.shutdown()
        thread.join(5)
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
