"""A real HTTP transfer against an isolated persistent SQLite bank, no success mocks."""
import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen
from backend.config import Config
from backend.server import create_server
from backend.service import TrustaService


def main():
    with tempfile.TemporaryDirectory() as directory:
        config = Config(db_path=str(Path(directory) / "demo.sqlite3"), signing_key=b"isolated-demo-signing-key")
        service = TrustaService(config)
        server = create_server(service, "isolated-demo-token", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, body=None):
            req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                          headers={"Authorization": "Bearer isolated-demo-token", "Content-Type": "application/json"})
            with urlopen(req, timeout=10) as response:
                return json.loads(response.read())
        try:
            task = request("/api/tasks", {"text": "给张三转 2000 元"})
            task = request(f"/api/tasks/{task['task_id']}/authorize", {"action_hash": task["authorization"]["action_hash"], "approve": True})
            balance = request("/api/balance")
            audit = request("/api/audit")
            ledger = service.db.rows("SELECT side,SUM(amount) AS amount FROM ledger_entries GROUP BY side")
            restarted = TrustaService(config)
            report = {"task_status": task["status"], "transfer_status": task["execution"]["transaction"]["status"],
                      "balance_cents": balance["balance"], "ledger": ledger, "verification": task["verification"]["status"],
                      "receipt": task["receipt"], "audit_chain_valid": audit["valid"],
                      "restart_preserves_receipt": restarted.get_task(task["task_id"])["receipt"] == task["receipt"]}
            if (report["task_status"] != "COMPLETED" or report["balance_cents"] != 800000 or not audit["valid"]
                    or not report["restart_preserves_receipt"]):
                raise RuntimeError("End-to-end verification failed")
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    main()
