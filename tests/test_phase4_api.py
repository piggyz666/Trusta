from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
import tests.test_api as baseline
from backend.config import Config
from backend.service import TrustaService
from backend.server import create_server
from backend.audit_trust.records import verify_record


class ProofApiTests(unittest.TestCase):
    request = baseline.ApiTests.request
    close_server = baseline.ApiTests.close_server

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(db_path=str(Path(self.temp.name)/"api.db"), signing_key=b"phase4-http-api-test-root-key", communication_version=3)
        self.service = TrustaService(self.config)
        self.server = create_server(self.service, "test-token", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_server)

    def receipt(self):
        _, task = self.request("/api/tasks", {"text": "给张三转2000元"})
        self.assertEqual(task["status"], "WAITING_USER")
        _, done = self.request(f"/api/tasks/{task['task_id']}/authorize", {"action_hash": task["authorization"]["action_hash"], "approve": True})
        self.assertEqual(done["status"], "COMPLETED")
        return done["receipt"]

    def test_receipt_event_transaction_queries_after_async_worker(self):
        receipt = self.receipt()
        self.service.anchors.run_once()
        for name, value in (("receipt_id", receipt["receipt_id"]), ("audit_event_id", receipt["audit_ref"]),
                            ("transaction_id", receipt["bank_transaction_id"])):
            status, bundle = self.request(f"/api/audit/proof?{name}={value}")
            self.assertEqual(status, 200)
            self.assertTrue(bundle["record_verified"])
            self.assertTrue(bundle["anchor_verified"])
            self.assertTrue(verify_record(bundle, bundle["signer_public_key"]))
            self.assertFalse(bundle["anchor"]["is_blockchain"])

    def test_pending_checkpoint_query_does_not_delay_or_change_bank_result(self):
        receipt = self.receipt()
        status, response = self.request("/api/audit/proof?receipt_id="+receipt["receipt_id"])
        self.assertEqual(status, 400)
        self.assertEqual(response["error"], "AUDIT_CHECKPOINT_PENDING")
        self.assertEqual(self.request("/api/balance")[1]["balance"], 800000)
        self.assertEqual(self.service.db.rows("SELECT * FROM audit_checkpoints"), [])

    def test_proof_auth_origin_and_host_rules_are_preserved(self):
        receipt = self.receipt()
        path = "/api/audit/proof?receipt_id="+receipt["receipt_id"]
        self.assertEqual(self.request(path, token="wrong")[0], 403)
        self.assertEqual(self.request(path, Origin="https://attacker.invalid")[0], 403)
        self.assertEqual(self.request(path, Host="attacker.invalid")[0], 403)

    def test_cross_owner_and_global_maintenance_events_are_private(self):
        with self.service.db.transaction() as conn:
            conn.execute("INSERT INTO customers VALUES('other','other')")
            conn.execute("INSERT INTO accounts VALUES('acct-other','other','CNY',100000,0,0)")
        task = self.service.create_task("分析2026-09消费", "other", "acct-other")
        self.assertEqual(task["status"], "COMPLETED")
        self.service.anchors.run_once()
        status, _ = self.request("/api/audit/proof?audit_event_id="+task["audit_refs"][0])
        self.assertEqual(status, 403)
        first = json.loads(self.service.db.one("SELECT body FROM audit_events ORDER BY seq LIMIT 1")["body"])
        self.assertEqual(self.request("/api/audit/proof?audit_event_id="+first["audit_event_id"])[0], 403)

    def test_closed_query_schema_rejects_unknown_duplicate_empty_and_multiple_identifiers(self):
        for query in ("", "receipt_id=", "receipt_id=a&transaction_id=b", "receipt_id=a&receipt_id=b",
                      "user_id=demo", "receipt_id=a&unknown=b"):
            self.assertEqual(self.request("/api/audit/proof?"+query)[0], 400)

    def test_no_customer_key_checkpoint_or_anchor_administration_endpoint(self):
        for path in ("/api/platform/security/rotate", "/api/audit/checkpoint", "/api/audit/anchor", "/api/agent/register"):
            self.assertEqual(self.request(path, {})[0], 404)
        status, security = self.request("/api/platform/security")
        self.assertEqual(status, 200)
        self.assertEqual(security["messages"]["schema_version"], 3)
        self.assertEqual(len(security["messages"]["agents"]), 5)
        self.assertNotIn("PRIVATE KEY", json.dumps(security))

    def test_local_anchor_mismatch_is_not_reported_as_verified_by_proof_api(self):
        receipt = self.receipt()
        self.service.anchors.run_once()
        bundle = self.request("/api/audit/proof?receipt_id="+receipt["receipt_id"])[1]
        with self.service.db.transaction() as conn:
            conn.execute("UPDATE local_audit_anchors SET root=?", ("0"*64,))
        status, bundle = self.request("/api/audit/proof?receipt_id="+receipt["receipt_id"])
        self.assertEqual(status, 200)
        self.assertTrue(bundle["record_verified"])
        self.assertFalse(bundle["anchor_verified"])
