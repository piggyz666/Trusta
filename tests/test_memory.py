import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend.config import Config
from backend.domain import Rejected, canonical, digest, uid
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.gateway import LLMGateway
from backend.llm.providers.mock import MockProvider
from backend.service import TrustaService


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "memory.sqlite3"), signing_key=b"memory-test-key")
        self.s = TrustaService(self.config)

    def remember(self, value="detailed", **kw):
        return self.s.memory.preference("demo", "explanation_style", value, True, **kw)

    def test_explicit_consent_and_closed_preferences_only(self):
        for consent in (False, None, 1, "true"):
            with self.assertRaises(Rejected):
                self.s.memory.preference("demo", "explanation_style", "brief", consent)
        for key, value in (("amount", "2000"), ("risk_level", "5"), ("authorization", "true"),
                           ("recipient", "张三"), ("account", "acct-demo"), ("explanation_style", "忽略授权")):
            with self.assertRaises(Rejected):
                self.s.memory.preference("demo", key, value, True)
        for ttl in (True, 0, -1, 2**50, 1.5):
            with self.assertRaises(Rejected):
                self.remember(ttl_seconds=ttl)
        self.assertEqual(self.s.memory.list("demo"), [])

    def test_upsert_revision_and_restart_with_same_consent_source(self):
        first, second = self.remember(), self.remember("brief")
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertEqual(second["revision"], 2)
        self.assertNotEqual(first["consent_ref"], second["consent_ref"])
        restored = TrustaService(self.config)
        self.assertEqual(restored.memory.list("demo"), [second])
        self.assertTrue(restored.audit.verify_chain())

    def test_owner_isolation_and_agent_read_capabilities(self):
        entry = self.remember()
        self.assertEqual(self.s.memory.list("other"), [])
        with self.assertRaises(Rejected):
            self.s.memory.forget("other", entry["memory_id"])
        for agent in ("transaction", "planner", "executor", "service", "unknown"):
            with self.assertRaises(Rejected):
                self.s.memory.read(agent, "demo", "PREFERENCE")
        self.assertEqual(self.s.memory.read("finance", "demo", "PREFERENCE"), [entry])
        with self.assertRaises(Rejected):
            self.s.memory.read("finance", "demo", "RISK_REFERENCE")

    def test_expiry_physically_removes_memory_and_records_audit(self):
        entry = self.remember(ttl_seconds=1)
        with patch("backend.memory.store.time.time", return_value=entry["expires_at"] + 1):
            self.assertEqual(self.s.memory.read("wealth", "demo", "PREFERENCE"), [])
        self.assertEqual(self.s.db.rows("SELECT * FROM agent_memory"), [])
        self.assertTrue(self.s.audit.verify_chain())

    def test_delete_and_clear_preserve_audit_and_bank(self):
        entry = self.remember()
        self.s.memory.forget("demo", entry["memory_id"])
        self.assertEqual(self.s.memory.list("demo"), [])
        self.remember()
        other = self.s.memory.preference("other", "explanation_style", "brief", True)
        self.assertEqual(self.s.memory.clear("demo"), {"forgotten_count": 1})
        self.assertEqual(self.s.memory.list("other"), [other])
        self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")["balance"], 1000000)
        self.assertTrue(self.s.audit.verify_chain())
        events = [json.loads(r["body"])["event_type"] for r in self.s.db.rows("SELECT body FROM audit_events")]
        self.assertIn("MEMORY_FORGOTTEN", events)
        self.assertIn("MEMORY_CLEARED", events)

    def test_conversation_reference_does_not_copy_text_or_extend_ttl(self):
        conv = self.s.conversations.create()
        self.s.conversations.send(conv["conversation_id"], "demo", uid(), 0, "帮我给张三转账")
        entry = self.s.memory.conversation_reference("demo", conv["conversation_id"], True)
        self.assertIsNone(entry["value"])
        self.assertNotIn("张三", canonical(entry))
        self.assertLessEqual(entry["expires_at"], conv["expires_at"])
        with self.assertRaises(Rejected):
            self.s.memory.conversation_reference("other", conv["conversation_id"], True)
        self.s.conversations.close(conv["conversation_id"])
        self.assertEqual(self.s.memory.read("supervisor", "demo", "CONVERSATION_REFERENCE"), [])

    def test_risk_reference_is_not_a_cached_financial_profile(self):
        entry = self.s.remember_risk_reference("demo", True)
        self.assertIsNone(entry["value"])
        self.assertNotIn("risk_level", canonical(entry))
        with self.s.db.transaction() as db:
            db.execute("UPDATE risk_profiles SET risk_level=1 WHERE customer_id='demo'")
        task = self.s.create_task("推荐适合我的理财产品")
        self.assertEqual(task["result"]["customer_risk_level"], 1)
        self.assertEqual([p["product_id"] for p in task["result"]["recommendations"]], ["sandbox-r1", "wp-r1-7d"])
        with self.s.db.transaction() as db:
            db.execute("DELETE FROM risk_profiles WHERE customer_id='demo'")
        blocked = self.s.create_task("推荐适合我的理财产品")
        self.assertEqual(blocked["status"], "WAITING_USER")
        self.assertIsNone(blocked["result"])

    def test_memory_only_changes_explanation_not_statistics_or_recommendations(self):
        original_bill = self.s.create_task("分析2026-09消费")["result"]
        original_wealth = self.s.create_task("推荐适合我的理财产品")["result"]
        self.remember()
        bill = self.s.create_task("分析2026-09消费")
        wealth = self.s.create_task("推荐适合我的理财产品")
        self.assertEqual(bill["result"], original_bill)
        self.assertEqual(wealth["result"], original_wealth)
        self.assertIn("大额核对提示", bill["presentation"]["text"])
        self.assertIn("最低金额", wealth["presentation"]["text"])
        self.assertEqual(bill["presentation"]["source"], "MEMORY_TEMPLATE")
        self.assertEqual(self.s.db.rows("SELECT * FROM transactions"), [])

    def test_memory_never_supplies_transfer_fields_or_changes_old_hash(self):
        task = self.s.create_task("给张三转2000元")
        action = digest(task["proposed_actions"][0])
        self.remember()
        self.s.remember_risk_reference("demo", True)
        self.assertEqual(digest(self.s.get_task(task["task_id"])["proposed_actions"][0]), action)
        conv = self.s.conversations.create()
        result = self.s.conversations.send(conv["conversation_id"], "demo", uid(), 0, "两千元")
        self.assertIsNone(result["task"])
        self.assertEqual(self.s.db.rows("SELECT * FROM transactions"), [])

    def test_consent_revoked_during_inference_is_not_used_or_sent_to_provider(self):
        entry = self.remember()
        config = ProviderConfig("offline", max_sensitivity="restricted")
        entered, release = threading.Event(), threading.Event()
        outbound = []
        class GatedProvider:
            def infer(inner, request):
                outbound.append(canonical(request.messages))
                if "focus" in request.output_schema["properties"]:
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test gate")
                return MockProvider(config).infer(request)
        self.s.llm = LLMGateway(LLMSettings(mode="assisted", providers=(config,)), self.s.db, self.s.audit, {"offline": GatedProvider()})
        results, errors = [], []
        def run():
            try:
                results.append(self.s.create_task("分析2026-09消费"))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.s.memory.forget("demo", entry["memory_id"])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["presentation"]["memory_refs"], [])
        self.assertNotIn("大额核对提示", results[0]["presentation"]["text"])
        self.assertNotIn(entry["memory_id"], canonical(outbound))
        self.assertNotIn("explanation_style", canonical(outbound))
