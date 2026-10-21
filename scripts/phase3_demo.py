"""Phase 3.1: real banking HTTP scenarios, explicitly offline inference fixtures."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen
from backend.config import Config
from backend.domain import digest
from backend.server import create_server
from backend.service import TrustaService


def demo(directory):
    root = Path(directory)
    llm_file = root / "llm.toml"
    def configure(provider):
        llm_file.write_text(f'''[gateway]
mode = "shadow"
version = "demo-{provider}"
[[providers]]
name = "{provider}"
kind = "mock"
model = "{provider}-fixture"
max_sensitivity = "restricted"
cost_per_1k_tokens_usd = 0
''', encoding="utf-8")

    config = Config(db_path=str(root / "phase3.sqlite3"), signing_key=b"isolated-phase3-demo-key", llm_config_path=str(llm_file))
    def session(service, operation):
        server = create_server(service, "isolated-phase3-token", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None):
            outbound = Request(f"http://127.0.0.1:{server.server_port}" + path,
                               data=None if body is None else json.dumps(body).encode(),
                               headers={"Authorization": "Bearer isolated-phase3-token", "Content-Type": "application/json"})
            with urlopen(outbound, timeout=10) as response:
                return json.loads(response.read())
        try:
            return operation(request)
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()

    configure("offline-a")
    first = TrustaService(config)
    def prepare(request):
        transfer = request("/api/tasks", {"text": "给张三转 2000 元"})
        finance = request("/api/tasks", {"text": "分析2026-09消费"})
        wealth = request("/api/tasks", {"text": "推荐适合我的理财产品"})
        balance = request("/api/balance")["balance"]
        return transfer, finance, wealth, balance
    transfer, finance, wealth, balance = session(first, prepare)
    frozen_hash = digest(transfer["proposed_actions"][0])
    if balance != 1000000 or transfer["status"] != "WAITING_USER":
        raise RuntimeError("Shadow inference altered bank execution")

    # Replace configuration while a transfer waits for approval; exact IR remains unchanged.
    configure("offline-b")
    second = TrustaService(config)
    second.shadow_task(transfer["task_id"])
    def complete(request):
        return request(f"/api/tasks/{transfer['task_id']}/authorize", {"action_hash": frozen_hash, "approve": True})
    completed = session(second, complete)
    persisted = TrustaService(config)
    ledger = persisted.db.rows("SELECT side,SUM(amount) AS amount FROM ledger_entries GROUP BY side")
    if not (completed["status"] == "COMPLETED" and digest(completed["proposed_actions"][0]) == frozen_hash
            and completed["verification"]["status"] == "VERIFIED"
            and persisted.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"] == 800000
            and persisted.db.one("SELECT COUNT(*) AS n FROM transactions")["n"] == 1
            and {r["side"]: r["amount"] for r in ledger} == {"DEBIT": 200000, "CREDIT": 200000}
            and persisted.audit.verify_chain()
            and all(persisted.get_task(t["task_id"])["status"] == "COMPLETED" for t in (completed, finance, wealth))
            and persisted.get_task(finance["task_id"])["result"] == finance["result"]
            and persisted.get_task(wealth["task_id"])["result"] == wealth["result"]):
        raise RuntimeError("Phase 3.1 banking demo failed")
    calls = [json.loads(row["body"]) for row in persisted.db.rows("SELECT body FROM llm_calls")]
    if len(calls) != 4 or any(c["status"] != "SUCCEEDED" or c.get("candidate_status") != "ATTACHED" for c in calls):
        raise RuntimeError("Shadow candidate demo failed")
    return {"inference": "OFFLINE_FIXTURES_ONLY; no real LLM quality claim",
            "TRANSFER": {"status": completed["status"], "verification": completed["verification"]["status"],
                         "approved_action_unchanged_after_provider_switch": digest(completed["proposed_actions"][0]) == frozen_hash,
                         "transaction_count": 1, "balance_cents": 800000},
            "BILL_ANALYSIS": {"status": finance["status"], "net_spend_cents": finance["result"]["summary"]["net_spend"]},
            "WEALTH_RECOMMENDATION": {"status": wealth["status"], "bank_risk_level": wealth["result"]["customer_risk_level"],
                                     "product_ids": [p["product_id"] for p in wealth["result"]["recommendations"]]},
            "shadow_calls": [{"purpose": c["purpose"], "provider": c["provider"], "status": c["status"],
                              "candidate_status": c["candidate_status"], "config_version": c["config_version"]} for c in calls],
            "read_only_balance_unchanged": balance == 1000000, "audit_chain_valid": persisted.audit.verify_chain(),
            "tasks_survive_restart": True, "ledger_balanced": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        report = demo(directory)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
