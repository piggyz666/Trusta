import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.agents.registry import REGISTRY
from backend.business import CHINA
from backend.config import Config
from backend.domain import Rejected, digest
from backend.service import TrustaService
from datetime import datetime


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "scenarios.sqlite3"), signing_key=b"scenario-test-key")
        self.service = TrustaService(self.config)

    def bank_snapshot(self):
        tables = ["customers", "accounts", "payees", "transfers", "transactions", "ledger_entries",
                  "processed_idempotency_keys", "consumption_records", "risk_profiles", "wealth_products"]
        return {table: self.service.db.rows(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}

    def finance(self):
        return self.service.create_task("分析2026-09消费")

    def wealth(self):
        return self.service.create_task("推荐适合我的理财产品")

    def test_finance_complete_categories_and_month(self):
        task = self.finance()
        self.assertEqual(task["task_type"], "BILL_ANALYSIS")
        self.assertEqual(task["status"], "COMPLETED")
        result = task["result"]
        self.assertEqual(result["summary"], {"currency": "CNY", "purchase_count": 10, "refund_count": 1, "settled_count": 11,
                                             "excluded_count": 1, "gross_spend": 547500, "refunds": 5000, "net_spend": 542500})
        categories = {r["category"]: r for r in result["categories"]}
        self.assertEqual(categories["SHOPPING"]["net_spend"], 513000)
        self.assertEqual(categories["FOOD"]["net_spend"], 14500)
        self.assertEqual(sum(c["net_spend"] for c in result["categories"]), 542500)
        self.assertEqual(result["monthly_comparison"]["previous_summary"]["net_spend"], 30000)
        self.assertEqual(result["monthly_comparison"]["net_change"], 512500)

    def test_finance_anomaly_evidence(self):
        result = self.finance()["result"]
        flags = {f["transaction_id"]: f for f in result["anomalies"]["flags"]}
        self.assertEqual(set(flags), {"sep-large", "sep-repeat-1", "sep-repeat-2"})
        self.assertEqual(flags["sep-large"]["rule_ids"], ["ABOVE_10X_MONTH_MEDIAN", "LARGE_PURCHASE"])
        self.assertIn("POSSIBLE_DUPLICATE", flags["sep-repeat-1"]["rule_ids"])
        for code, item in flags.items():
            self.assertEqual(item["evidence_refs"], [f"bank:consumption_records:{code}"])
        self.assertNotIn("bank:consumption_records:sep-pending", result["evidence_refs"])

    def test_empty_month_and_zero_baseline(self):
        result = self.service.create_task("分析2025-01消费")["result"]
        self.assertEqual(result["summary"]["net_spend"], 0)
        self.assertEqual(result["categories"], [])
        self.assertEqual(result["anomalies"]["flags"], [])
        self.assertIsNone(result["monthly_comparison"]["net_change_bps"])

    def test_month_boundary_china_and_invalid_month(self):
        with self.service.db.transaction() as db:
            db.execute("INSERT INTO consumption_records VALUES('oct-boundary','acct-demo','店','FOOD',100,'CNY',?,'PURCHASE','SETTLED')",
                       (datetime(2026, 10, 1, tzinfo=CHINA).timestamp(),))
        task = self.finance()
        refs = task["result"]["evidence_refs"]
        self.assertIn("bank:consumption_records:sep-end", refs)
        self.assertNotIn("bank:consumption_records:oct-boundary", refs)
        with self.assertRaises(Rejected):
            self.service.create_task("分析2026-13消费")

    def test_unknown_category_unclassified_and_refunds(self):
        records = [{"id": "x", "merchant": "未知", "category": "CUSTOM", "amount": 123, "currency": "CNY",
                    "occurred_at": 0, "kind": "REFUND", "status": "SETTLED"}]
        result = self.service.finance.analyze("2026-09", records, [])
        self.assertEqual(result["summary"]["net_spend"], -123)
        self.assertEqual(result["categories"][0]["category"], "UNCLASSIFIED")
        self.assertEqual(result["categories"][0]["gross_share_bps"], 0)

    def test_finance_reads_account_and_customer_isolation(self):
        with self.service.db.transaction() as db:
            db.execute("INSERT INTO customers VALUES('other','other')")
            db.execute("INSERT INTO accounts VALUES('acct-other','other','CNY',100000,0,0)")
            db.execute("INSERT INTO consumption_records VALUES('other-secret','acct-other','private','FOOD',9999,'CNY',?,'PURCHASE','SETTLED')",
                       (datetime(2026, 9, 1, tzinfo=CHINA).timestamp(),))
        with self.assertRaises(Rejected):
            self.service.finance.records("demo", "acct-other", "2026-09")
        denied = self.service.create_task("分析2026-09消费", source_account_id="acct-other")
        self.assertEqual(denied["status"], "BLOCKED")
        self.assertIsNone(denied["result"])
        self.assertNotIn("bank:consumption_records:other-secret", self.finance()["result"]["evidence_refs"])

    def test_finance_data_failure_no_fabricated_report(self):
        with patch.object(self.service.finance, "records", side_effect=TimeoutError("read failed")):
            result = self.finance()
        self.assertEqual(result["status"], "FAILED")
        self.assertIsNone(result["result"])

    def test_wealth_risk_matching_grounded_reasons(self):
        task = self.wealth()
        self.assertEqual(task["task_type"], "WEALTH_RECOMMENDATION")
        self.assertEqual(task["status"], "COMPLETED")
        report = task["result"]
        self.assertEqual(report["customer_risk_level"], 2)
        self.assertEqual([p["product_id"] for p in report["recommendations"]], ["sandbox-r1", "wp-r1-mmf", "wp-r2-shortbond"])
        for recommendation in report["recommendations"]:
            product = next(p for p in task["context"]["product_catalog"] if p["id"] == recommendation["product_id"])
            self.assertLessEqual(product["risk_level"], report["customer_risk_level"])
            self.assertEqual(recommendation["minimum_amount"], product["minimum_amount"])
            self.assertEqual(recommendation["term_days"], product["term_days"])
            self.assertIn(f"bank:wealth_products:{product['id']}", recommendation["evidence_refs"])
            self.assertIn("bank:risk_profiles:demo", recommendation["evidence_refs"])

    def test_wealth_missing_or_expired_risk_waits(self):
        with self.service.db.transaction() as db:
            db.execute("UPDATE risk_profiles SET expires_at=0 WHERE customer_id='demo'")
        expired = self.wealth()
        self.assertEqual(expired["status"], "WAITING_USER")
        self.assertIsNone(expired["result"])
        self.assertIsNone(expired["authorization"])
        with self.service.db.transaction() as db:
            db.execute("DELETE FROM risk_profiles WHERE customer_id='demo'")
        missing = self.wealth()
        self.assertEqual(missing["errors"], ["RISK_PROFILE_REQUIRED"])
        self.assertEqual(missing["status"], "WAITING_USER")

    def test_wealth_catalog_failure_no_fabrication(self):
        with patch.object(self.service.wealth, "products", side_effect=TimeoutError("catalog timeout")):
            task = self.wealth()
        self.assertEqual(task["status"], "FAILED")
        self.assertIsNone(task["result"])

    def test_wealth_no_match_no_overrisk_fallback(self):
        with self.service.db.transaction() as db:
            db.execute("UPDATE wealth_products SET active=0 WHERE risk_level<=2")
        task = self.wealth()
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(task["result"]["recommendations"], [])

    def test_wealth_risk_identity_cannot_be_overridden(self):
        with self.assertRaises(Rejected):
            self.service.gateway.read("wealth", "demo", "customer.get_risk_profile", customer_id_override="other")
        self.assertIsNone(self.service.gateway.read("wealth", "other", "customer.get_risk_profile"))

    def test_read_only_capabilities_are_least_privilege(self):
        self.assertEqual(self.service.capabilities.GRANTS["finance"], {"account.read", "transaction.read"})
        self.assertEqual(self.service.capabilities.GRANTS["wealth"], {"risk_profile.read", "wealth_product.read"})
        for actor in ("finance", "wealth"):
            for capability in ("banking.transfer", "transfer.propose", "transfer.execute", "wealth.purchase", "wealth.redeem"):
                with self.assertRaises(Rejected):
                    self.service.capabilities.check(actor, capability)
            for capability in ("wealth.purchase", "wealth.redeem", "transfer.create"):
                with self.assertRaises(Rejected):
                    self.service.gateway.read(actor, "demo", capability)
        with self.assertRaises(Rejected):
            self.service.gateway.read("finance", "demo", "customer.get_risk_profile")
        with self.assertRaises(Rejected):
            self.service.gateway.read("wealth", "demo", "account.get", account_id="acct-demo")

    def test_finance_and_wealth_cannot_use_valid_transfer_contract(self):
        task = self.service.create_task("给张三转 2000 元")
        action = task["proposed_actions"][0]
        auth = self.service.auth.decide(task["authorization"]["authorization_id"], "demo", action, True)
        contract = self.service.contracts.issue(action, task["policy_decision"]["policy_decision_id"], auth["authorization_id"])
        before = self.bank_snapshot()
        for actor in ("finance", "wealth"):
            with self.assertRaises(Rejected):
                self.service.gateway.execute(actor, action, contract)
        self.assertEqual(before, self.bank_snapshot())

    def test_reports_never_change_bank_or_create_financial_artifacts(self):
        before = self.bank_snapshot()
        with patch.object(self.service.gateway, "execute", side_effect=AssertionError("read-only task executed")):
            for task in (self.finance(), self.wealth()):
                self.assertEqual(task["status"], "COMPLETED")
                for field in ("authorization", "execution", "receipt", "policy_decision"):
                    self.assertIsNone(task[field])
                self.assertEqual(task["proposed_actions"], [])
        self.assertEqual(before, self.bank_snapshot())
        for table in ("actions", "execution_contracts", "authorizations"):
            self.assertEqual(self.service.db.rows(f"SELECT * FROM {table}"), [])

    def test_purchase_redemption_requests_do_not_execute(self):
        before = self.bank_snapshot()
        for text in ("购买理财产品", "赎回理财产品", "推荐理财产品并买入", "给张三转2000元并购买理财"):
            task = self.service.create_task(text)
            self.assertEqual(task["status"], "WAITING_USER")
            self.assertIsNone(task["result"])
            with self.assertRaises(Rejected):
                self.service.authorize(task["task_id"], "demo", "fake", True)
        self.assertEqual(before, self.bank_snapshot())

    def test_blackboard_dag_audit_and_restart(self):
        tasks = [self.finance(), self.wealth()]
        for task in tasks:
            self.assertEqual(len(task["steps"]), 6)
            self.assertTrue(all(s["status"] == "COMPLETED" for s in task["steps"]))
            self.assertEqual(len(task["audit_refs"]), 1)
            self.assertTrue(self.service.db.one("SELECT * FROM replay_nonces WHERE sender=?", ("finance" if task["task_type"] == "BILL_ANALYSIS" else "wealth",)))
            audit = [json.loads(r["body"]) for r in self.service.db.rows("SELECT body FROM audit_events")]
            self.assertTrue(any(e["task_id"] == task["task_id"] and e["event_type"] == "READ_ONLY_REPORT_PUBLISHED" for e in audit))
        self.assertTrue(self.service.audit.verify_chain())
        restarted = TrustaService(self.config)
        for task in tasks:
            self.assertEqual(restarted.get_task(task["task_id"])["result"], task["result"])
        for task in tasks:
            plan = self.service.planner.plan(task)
            plan["steps"][1]["owner"] = "executor"
            with self.assertRaises(Rejected):
                self.service.blackboard.install_plan(task["task_id"], plan)

    def test_old_transfer_task_persistence_and_registry(self):
        transfer = self.service.create_task("给张三转 2000 元")
        self.finance()
        self.wealth()
        restarted = TrustaService(self.config)
        task = restarted.authorize(transfer["task_id"], "demo", digest(transfer["proposed_actions"][0]), True)
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(set(REGISTRY), {"supervisor", "planner", "transaction", "finance", "wealth", "service"})
        self.assertFalse(REGISTRY["service"]["active"])

    def test_new_agent_code_cannot_access_adapter_or_database(self):
        root = Path(__file__).resolve().parents[1] / "backend" / "agents"
        for name in ("finance.py", "wealth.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"_adapter", "db", "bank", "execute", "create_transfer", "issue", "decide"})


if __name__ == "__main__":
    unittest.main()
