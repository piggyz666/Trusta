"""Three real HTTP scenarios; isolated persistent bank, no always-success mocks."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen
from backend.config import Config
from backend.server import create_server
from backend.service import TrustaService


def demo(directory):
    config = Config(db_path=str(Path(directory) / "phase2.sqlite3"), signing_key=b"isolated-phase2-demo-key")
    service = TrustaService(config)
    server = create_server(service, "isolated-phase2-token", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def request(path, body=None):
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": "Bearer isolated-phase2-token", "Content-Type": "application/json"})
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    try:
        transfer = request("/api/tasks", {"text": "给张三转 2000 元"})
        transfer = request(f"/api/tasks/{transfer['task_id']}/authorize",
                           {"action_hash": transfer["authorization"]["action_hash"], "approve": True})
        before_reads = request("/api/balance")["balance"]
        finance = request("/api/tasks", {"text": "分析2026-09消费"})
        wealth = request("/api/tasks", {"text": "推荐适合我的理财产品"})
        after_reads = request("/api/balance")["balance"]
        audit_valid = request("/api/audit")["valid"]
        rebuilt = TrustaService(config)
        persisted = all(rebuilt.get_task(t["task_id"])["status"] == "COMPLETED" for t in (transfer, finance, wealth))
        if not (all(t["status"] == "COMPLETED" for t in (transfer, finance, wealth)) and before_reads == after_reads == 800000 and audit_valid and persisted):
            raise RuntimeError("Three-scenario demo did not pass")
        return {"TRANSFER": {"request": "给张三转 2000 元", "status": transfer["status"], "settlement": transfer["execution"]["transaction"]["status"],
                             "balance_cents": before_reads, "verification": transfer["verification"]["status"], "receipt_id": transfer["receipt"]["receipt_id"]},
                "BILL_ANALYSIS": {"request": "分析2026-09消费", "status": finance["status"], "result": finance["result"]},
                "WEALTH_RECOMMENDATION": {"request": "推荐适合我的理财产品", "status": wealth["status"], "result": wealth["result"]},
                "read_only_balance_unchanged": before_reads == after_reads, "audit_chain_valid": audit_valid, "tasks_survive_restart": persisted}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Optional path for the JSON demo report")
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
