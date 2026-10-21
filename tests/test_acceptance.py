import ast
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from backend.config import Config
from backend.domain import Rejected, canonical, cents, digest
from backend.service import TrustaService


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "bank.sqlite3"), signing_key=b"unit-test-signing-key-32-bytes-long",
                             strong_auth_secret="test-secret")
        self.service = TrustaService(self.config)

    def proposal(self, text="给张三转 2000 元"):
        task = self.service.create_task(text)
        self.assertEqual(task["status"], "WAITING_USER")
        return task

    def complete(self, text="给张三转 2000 元"):
        task = self.proposal(text)
        return self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True, "test-secret")

    def prepared(self):
        task = self.proposal()
        action = task["proposed_actions"][0]
        auth = self.service.auth.decide(task["authorization"]["authorization_id"], "demo", action, True)
        contract = self.service.contracts.issue(action, task["policy_decision"]["policy_decision_id"], auth["authorization_id"])
        return action, contract

    def balance(self):
        return self.service.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"]

    def test_01_transfer_settled(self):
        task = self.complete()
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(task["execution"]["transaction"]["status"], "SETTLED")

    def test_02_final_balance(self):
        self.complete()
        self.assertEqual(self.balance(), 800000)

    def test_03_balanced_ledger(self):
        self.complete()
        totals = self.service.db.rows("SELECT side,SUM(amount) AS total FROM ledger_entries GROUP BY side")
        self.assertEqual({r["side"]: r["total"] for r in totals}, {"DEBIT": 200000, "CREDIT": 200000})
        self.assertEqual(self.service.db.one("SELECT balance FROM accounts WHERE id='settlement'")["balance"], 200000)

    def test_04_transaction_matches_approval(self):
        task = self.complete()
        action, tx = task["proposed_actions"][0], task["execution"]["transaction"]
        for key in ("amount", "currency", "recipient_id", "source_account_id"):
            self.assertEqual(tx[key], action[key])
        self.assertEqual(task["authorization"]["action_hash"], digest(action))

    def test_05_receipt_generated(self):
        receipt = self.complete()["receipt"]
        self.assertEqual(receipt["verification_status"], "VERIFIED")
        self.assertIsNotNone(self.service.db.one("SELECT * FROM receipts WHERE id=?", (receipt["receipt_id"],)))

    def test_06_audit_chain_and_tamper_detection(self):
        self.complete()
        self.assertTrue(self.service.audit.verify_chain())
        with self.service.db.transaction() as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE audit_events SET body='{}' WHERE seq=1")
        # Simulate an attacker with database-file privileges bypassing the append-only trigger.
        with self.service.db.transaction() as db:
            db.execute("DROP TRIGGER audit_no_update")
            event = json.loads(db.execute("SELECT body FROM audit_events WHERE seq=1").fetchone()["body"])
            event["actor"] = "attacker"
            db.execute("UPDATE audit_events SET body=? WHERE seq=1", (canonical(event),))
        self.assertFalse(self.service.audit.verify_chain())
        with self.assertRaises(Rejected):
            TrustaService(self.config)

    def test_07_amount_tampering_blocked(self):
        action, contract = self.prepared()
        changed = copy.deepcopy(action)
        changed["amount"] = 100000
        changed["expected_postconditions"]["after_balance"] = 900000
        with self.assertRaises(Rejected):
            self.service.gateway.execute("executor", changed, contract)
        self.assertEqual(self.balance(), 1000000)

    def test_08_recipient_tampering_blocked(self):
        action, contract = self.prepared()
        changed = dict(action, recipient_id="payee-li")
        with self.assertRaises(Rejected):
            self.service.gateway.execute("executor", changed, contract)
        self.assertEqual(self.balance(), 1000000)

    def test_09_missing_authorization_blocked(self):
        task = self.proposal()
        action = task["proposed_actions"][0]
        with self.assertRaises(Rejected):
            self.service.contracts.issue(action, task["policy_decision"]["policy_decision_id"], "missing")
        with self.assertRaises(Rejected):
            self.service.gateway.execute("executor", action, None)
        self.assertEqual(self.balance(), 1000000)

    def test_10_capability_bypass_blocked(self):
        action, contract = self.prepared()
        with self.assertRaises(Rejected):
            self.service.gateway.execute("transaction", action, contract)
        with self.assertRaises(Rejected):
            self.service.gateway.read("transaction", "demo", "transfer.create")
        self.assertEqual(self.balance(), 1000000)

    def message(self, task):
        payload = task["proposed_actions"][0]
        reference = f"task:{task['task_id']}:agent_results:transaction:ACTION_PROPOSED"
        message = self.service.messages.sign("transaction", "supervisor", task["task_id"], "T05", "ACTION_PROPOSED", reference, payload)
        return message, payload

    def test_11_invalid_signature_blocked(self):
        task = self.proposal()
        message, payload = self.message(task)
        message["signature"] = "0" * 64
        with self.assertRaises(Rejected):
            self.service.accept_message(message, payload, "supervisor")
        self.assertEqual(self.service.get_task(task["task_id"])["status"], "BLOCKED")

    def test_12_payload_tampering_blocked(self):
        task = self.proposal()
        message, payload = self.message(task)
        with self.assertRaises(Rejected):
            self.service.accept_message(message, dict(payload, amount=1), "supervisor")
        self.assertEqual(self.balance(), 1000000)

    def test_13_replay_nonce_blocked_after_restart(self):
        task = self.proposal()
        message, payload = self.message(task)
        self.service.accept_message(message, payload, "supervisor")
        restarted = TrustaService(self.config)
        with self.assertRaises(Rejected):
            restarted.accept_message(message, payload, "supervisor")
        self.assertEqual(self.balance(), 1000000)

    def test_14_idempotency_never_double_debits(self):
        action, contract = self.prepared()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.gateway.execute("executor", action, contract), range(4)))
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(self.balance(), 800000)
        self.assertEqual(len(self.service.db.rows("SELECT * FROM transactions")), 1)
        self.assertEqual(len(self.service.db.rows("SELECT * FROM ledger_entries")), 2)

    def test_15_insufficient_balance_blocked(self):
        task = self.service.create_task("给张三转 20000 元")
        self.assertEqual(task["status"], "BLOCKED")
        self.assertIn("INSUFFICIENT_BALANCE", task["errors"])
        self.assertEqual(self.balance(), 1000000)

    def test_16_frozen_account_blocked(self):
        with self.service.db.transaction() as db:
            db.execute("UPDATE accounts SET frozen=1 WHERE id='acct-demo'")
        task = self.service.create_task("给张三转 2000 元")
        self.assertEqual(task["status"], "BLOCKED")
        self.assertIn("FROZEN_SOURCE", task["errors"])

    def test_17_waiting_user_survives_restart(self):
        task = self.proposal()
        restarted = TrustaService(self.config)
        restored = restarted.get_task(task["task_id"])
        self.assertEqual(restored["status"], "WAITING_USER")
        self.assertEqual(restored["authorization"], task["authorization"])
        completed = restarted.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(completed["status"], "COMPLETED")

    def test_18_authorization_expiry_enforced(self):
        task = self.proposal()
        with patch("time.time", return_value=time.time() + self.config.authorization_ttl + 1):
            expired = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(expired["status"], "WAITING_USER")
        self.assertEqual(expired["authorization"]["status"], "EXPIRED")
        self.assertEqual(self.balance(), 1000000)

    def test_19_bank_failure_cannot_produce_false_success(self):
        task = self.proposal()
        # A real precondition changes between approval preparation and execution.
        with self.service.db.transaction() as db:
            db.execute("UPDATE accounts SET frozen=1 WHERE id='acct-demo'")
        failed = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(failed["status"], "FAILED")
        self.assertIsNone(failed["receipt"])
        self.assertEqual(self.balance(), 1000000)
        self.assertEqual(self.service.db.rows("SELECT * FROM transactions"), [])

    def test_20_uncertain_verification_manual_review(self):
        task = self.proposal()
        original = self.service.gateway.read
        def uncertain(actor, customer, capability, **params):
            if actor == "verifier":
                raise TimeoutError("bank read timeout")
            return original(actor, customer, capability, **params)
        with patch.object(self.service.gateway, "read", side_effect=uncertain):
            result = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(result["status"], "MANUAL_REVIEW")
        self.assertIsNone(result["receipt"])
        self.assertEqual(self.balance(), 800000)
        with self.assertRaises(Rejected):
            self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(len(self.service.db.rows("SELECT * FROM transactions")), 1)

    def test_strong_auth_required(self):
        task = self.proposal("给张三转 5000 元")
        self.assertEqual(task["policy_decision"]["decision"], "STRONG_AUTH")
        with self.assertRaises(Rejected):
            self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True, "wrong")
        self.assertEqual(self.balance(), 1000000)
        completed = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True, "test-secret")
        self.assertEqual(completed["status"], "COMPLETED")

    def test_denial_cancels(self):
        task = self.proposal()
        denied = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), False)
        self.assertEqual(denied["status"], "CANCELLED")
        self.assertEqual(self.balance(), 1000000)

    def test_bank_rechecks_stale_preconditions(self):
        action1, contract1 = self.prepared()
        action2, contract2 = self.prepared()
        self.service.gateway.execute("executor", action1, contract1)
        with self.assertRaises(Rejected):
            self.service.gateway.execute("executor", action2, contract2)
        self.assertEqual(self.balance(), 800000)

    def test_restart_mid_execution_no_retry(self):
        task = self.proposal()
        self.service.blackboard.update(task["task_id"], "test", status="EXECUTING")
        restarted = TrustaService(self.config)
        self.assertEqual(restarted.get_task(task["task_id"])["status"], "MANUAL_REVIEW")
        self.assertEqual(self.balance(), 1000000)

    def test_unknown_payee_waiting_user(self):
        task = self.service.create_task("给王五转 200 元")
        self.assertEqual(task["status"], "WAITING_USER")
        self.assertIsNone(task["authorization"])

    def test_scheduler_dependency_and_transition(self):
        task = self.proposal()
        with self.assertRaises(Rejected):
            self.service.scheduler.start(task["task_id"], "T09")
        with self.assertRaises(Rejected):
            self.service.scheduler.transition(task["task_id"], "T01", "RUNNING")

    def test_expired_contract_blocked(self):
        action, contract = self.prepared()
        with patch("time.time", return_value=contract["expires_at"] + 1):
            with self.assertRaises(Rejected):
                self.service.gateway.execute("executor", action, contract)
        self.assertEqual(self.balance(), 1000000)

    def test_contract_forgery_blocked(self):
        action, contract = self.prepared()
        with self.assertRaises(Rejected):
            self.service.gateway.execute("executor", action, dict(contract, expires_at=contract["expires_at"] + 1000))

    def test_bank_deadline_rechecked_after_gateway(self):
        action, contract = self.prepared()
        original = self.service.gateway._adapter.create_transfer
        def delayed(action, action_hash, deadline):
            with patch("time.time", return_value=deadline + 1):
                return original(action, action_hash, deadline)
        with patch.object(self.service.gateway._adapter, "create_transfer", side_effect=delayed):
            with self.assertRaises(Rejected):
                self.service.gateway.execute("executor", action, contract)
        self.assertEqual(self.balance(), 1000000)

    def test_lost_response_after_commit_no_retry(self):
        task = self.proposal()
        original = self.service.gateway._adapter.create_transfer
        def lost_response(action, action_hash, deadline):
            original(action, action_hash, deadline)
            raise TimeoutError("response lost after commit")
        with patch.object(self.service.gateway._adapter, "create_transfer", side_effect=lost_response):
            result = self.service.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertEqual(result["status"], "MANUAL_REVIEW")
        self.assertEqual(self.balance(), 800000)
        self.assertIsNone(result["receipt"])
        restarted = TrustaService(self.config)
        self.assertEqual(restarted.get_task(task["task_id"])["status"], "MANUAL_REVIEW")
        self.assertEqual(len(self.service.db.rows("SELECT * FROM transactions")), 1)

    def test_authorization_renewal(self):
        task = self.proposal()
        with patch("time.time", return_value=task["authorization"]["expires_at"] + 1):
            renewed = self.service.renew_authorization(task["task_id"], "demo")
            self.assertNotEqual(renewed["authorization"]["authorization_id"], task["authorization"]["authorization_id"])
            self.assertEqual(renewed["authorization"]["action_hash"], digest(task["proposed_actions"][0]))

    def test_money_precision_and_invalid_input(self):
        self.assertEqual(cents("0.01"), 1)
        for value in ["0", "-1", "1.001", "NaN", "Infinity"]:
            with self.assertRaises(Rejected):
                cents(value)

    def test_bank_write_call_sites_are_restricted(self):
        root = Path(__file__).resolve().parents[1] / "backend"
        allowed = {"banking/adapter.py", "execution/gateway.py"}
        for file in root.rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_transfer":
                    self.assertIn(file.relative_to(root).as_posix(), allowed)
        for file in (root / "agents").rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"execute", "create_transfer"})


if __name__ == "__main__":
    unittest.main()
