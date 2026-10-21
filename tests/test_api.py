import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from backend.config import Config
from backend.server import create_server
from backend.service import TrustaService


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(db_path=str(Path(self.temp.name) / "api.sqlite3"), signing_key=b"api-test-signing-key")
        self.service = TrustaService(self.config)
        self.server = create_server(self.service, "test-token", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, path, body=None, token="test-token", **headers):
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **headers})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            with response:
                return response.code, json.loads(response.read())

    def test_http_end_to_end(self):
        status, task = self.request("/api/tasks", {"text": "给张三转 2000 元"})
        self.assertEqual(status, 201)
        self.assertEqual(task["status"], "WAITING_USER")
        status, completed = self.request(f"/api/tasks/{task['task_id']}/authorize",
                                        {"action_hash": task["authorization"]["action_hash"], "approve": True})
        self.assertEqual(status, 200)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(self.request("/api/balance")[1]["balance"], 800000)
        self.assertTrue(self.request("/api/audit")[1]["valid"])
        restarted = TrustaService(self.config)
        self.assertEqual(restarted.get_task(task["task_id"])["receipt"], completed["receipt"])

    def test_authentication_required(self):
        self.assertEqual(self.request("/api/tasks", token="bad")[0], 403)
        self.assertEqual(self.request("/api/tasks", {"text": "给张三转 2000 元"}, token="bad")[0], 403)
        self.assertEqual(self.service.blackboard.list("demo"), [])

    def test_cross_origin_blocked(self):
        self.assertEqual(self.request("/api/tasks", {"text": "给张三转 2000 元"}, Origin="https://attacker.invalid")[0], 403)

    def test_customer_identity_not_client_controlled(self):
        self.assertEqual(self.request("/api/tasks", {"text": "给张三转 2000 元", "user_id": "other"})[0], 400)
        self.assertEqual(self.request("/api/tasks", {"text": "给张三转 2000 元", "source_account_id": "settlement"})[1]["status"], "BLOCKED")

    def test_approval_type_and_hash_checked(self):
        _, task = self.request("/api/tasks", {"text": "给张三转 2000 元"})
        path = f"/api/tasks/{task['task_id']}/authorize"
        self.assertEqual(self.request(path, {"action_hash": task["authorization"]["action_hash"], "approve": "false"})[0], 400)
        self.assertEqual(self.request(path, {"action_hash": "changed", "approve": True})[0], 400)
        self.assertEqual(self.request("/api/balance")[1]["balance"], 1000000)

    def test_read_only_http_scenarios_and_no_authorization(self):
        for text, kind in [("分析2026-09消费", "BILL_ANALYSIS"), ("推荐适合我的理财产品", "WEALTH_RECOMMENDATION")]:
            status, task = self.request("/api/tasks", {"text": text})
            self.assertEqual(status, 201)
            self.assertEqual(task["task_type"], kind)
            self.assertEqual(task["status"], "COMPLETED")
            self.assertIsNotNone(task["result"])
            self.assertEqual(self.request(f"/api/tasks/{task['task_id']}/authorize", {"action_hash": "fake", "approve": True})[0], 400)
        self.assertEqual(self.request("/api/balance")[1]["balance"], 1000000)
        self.assertTrue(self.request("/api/audit")[1]["valid"])

    def test_http_cannot_override_wealth_risk_or_execute_purchase(self):
        for body in ({"text": "推荐理财产品", "risk_level": 5}, {"text": "推荐理财产品", "user_id": "other"}):
            self.assertEqual(self.request("/api/tasks", body)[0], 400)
        for path in ("/api/wealth/purchase", "/api/wealth/redeem"):
            self.assertEqual(self.request(path, {})[0], 404)


if __name__ == "__main__":
    unittest.main()
