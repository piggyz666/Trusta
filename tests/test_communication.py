import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
from backend.agents.registry import REGISTRY
from backend.config import Config
from backend.domain import Rejected, canonical, digest, uid
from backend.security.extensions import AnchorCommitment
from backend.service import TrustaService


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "messages.sqlite3"), signing_key=b"communication-test-key")
        self.s = TrustaService(self.config)

    def message(self):
        task = self.s.create_task("给张三转2000元")
        payload = task["proposed_actions"][0]
        reference = f"task:{task['task_id']}:agent_results:transaction:ACTION_PROPOSED"
        return self.s.messages.sign("transaction", "supervisor", task["task_id"], "T05", "ACTION_PROPOSED", reference, payload), payload

    def legacy(self, message):
        old = {k: v for k, v in message.items() if k not in {"signature", "schema_version", "algorithm", "key_id", "payload_version", "expires_at"}}
        key = hmac.new(self.config.signing_key, old["sender_agent"].encode(), hashlib.sha256).digest()
        old["signature"] = hmac.new(key, canonical(old).encode(), hashlib.sha256).hexdigest()
        return old

    def test_v2_message_binds_algorithm_epoch_payload_version_and_reference(self):
        message, payload = self.message()
        self.assertEqual(message["schema_version"], 2)
        self.assertEqual(message["algorithm"], "HMAC-SHA256")
        self.assertEqual(message["key_id"], "message-v1")
        self.assertEqual(message["payload_version"], self.s.get_task(message["task_id"])["revision"])
        self.s.accept_message(message, payload, "supervisor")
        self.assertTrue(self.s.audit.verify_chain())

    def test_tampered_envelope_and_unknown_algorithm_are_rejected(self):
        message, payload = self.message()
        for field, value in (("algorithm", "ECDSA"), ("key_id", "message-v2"), ("schema_version", 3),
                             ("schema_version", True), ("payload_version", True), ("expires_at", float("nan")),
                             ("payload_ref", "another-task"), ("nonce", []), ("signature", "中文"),
                             ("timestamp", 10**1000), ("expires_at", 10**1000)):
            with self.subTest(field=field), self.assertRaises(Rejected):
                self.s.messages.verify(message | {field: value}, payload, "supervisor")
        self.s.accept_message(message, payload, "supervisor")

    def test_signed_stale_task_version_is_blocked_without_consuming_nonce(self):
        message, payload = self.message()
        self.s.blackboard.update(message["task_id"], "runtime", clarification="new version")
        with self.assertRaises(Rejected) as error:
            self.s.accept_message(message, payload, "supervisor")
        self.assertEqual(error.exception.code, "MESSAGE_STALE_PAYLOAD")
        self.assertFalse(self.s.db.one("SELECT nonce FROM replay_nonces WHERE nonce=?", (message["nonce"],)))
        self.assertEqual(self.s.get_task(message["task_id"])["status"], "BLOCKED")

    def test_signed_wrong_reference_and_illegal_owner_edge_are_rejected(self):
        message, payload = self.message()
        wrong = message | {"payload_ref": "task:other:result"}
        wrong["signature"] = self.s.messages._mac(wrong)
        with self.assertRaises(Rejected) as error:
            self.s.accept_message(wrong, payload, "supervisor")
        self.assertEqual(error.exception.code, "MESSAGE_REFERENCE_DENIED")
        self.assertFalse(self.s.db.one("SELECT nonce FROM replay_nonces WHERE nonce=?", (wrong["nonce"],)))

    def test_replay_survives_rotation_and_restart(self):
        message, payload = self.message()
        self.s.accept_message(message, payload, "supervisor")
        self.s.message_keys.rotate()
        restarted = TrustaService(self.config)
        with self.assertRaises(Rejected) as error:
            restarted.messages.verify(message, payload, "supervisor")
        self.assertEqual(error.exception.code, "REPLAY_BLOCKED")
        self.assertEqual(restarted.message_keys.current_version(), 2)

    def test_rotation_grace_then_old_key_expiry(self):
        message, payload = self.message()
        self.s.message_keys.rotate(2)
        self.s.messages.verify(message, payload, "supervisor", consume=False)
        with patch("backend.security.keyring.time.time", return_value=time.time() + 3):
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(message, payload, "supervisor", consume=False)
            self.assertEqual(error.exception.code, "MESSAGE_KEY_EXPIRED")
        new, value = self.message()
        self.assertEqual(new["key_id"], "message-v2")
        self.s.accept_message(new, value, "supervisor")

    def test_revocation_is_immediate_and_current_key_cannot_be_revoked(self):
        message, payload = self.message()
        with self.assertRaises(Rejected):
            self.s.message_keys.revoke(1)
        self.s.message_keys.rotate()
        self.s.message_keys.revoke(1)
        with self.assertRaises(Rejected) as error:
            self.s.messages.verify(message, payload, "supervisor")
        self.assertEqual(error.exception.code, "MESSAGE_KEY_REVOKED")

    def test_legacy_window_is_bounded_persisted_and_not_reopened(self):
        message, payload = self.message()
        legacy = self.legacy(message)
        self.s.messages.verify(legacy, payload, "supervisor", consume=False)
        with self.s.db.transaction() as db:
            state = json.loads(db.execute("SELECT body FROM message_security_state WHERE id=1").fetchone()["body"])
            state["legacy_until"] = time.time() - 1
            db.execute("UPDATE message_security_state SET body=? WHERE id=1", (canonical(state),))
        restarted = TrustaService(self.config)
        restarted.message_keys.rotate()
        with self.assertRaises(Rejected) as error:
            restarted.messages.verify(legacy, payload, "supervisor")
        self.assertEqual(error.exception.code, "LEGACY_MESSAGE_DISABLED")

    def test_stripping_v2_fields_is_not_a_valid_legacy_signature(self):
        message, payload = self.message()
        stripped = {k: v for k, v in message.items() if k not in {"schema_version", "algorithm", "key_id", "payload_version", "expires_at"}}
        with self.assertRaises(Rejected) as error:
            self.s.messages.verify(stripped, payload, "supervisor")
        self.assertEqual(error.exception.code, "INVALID_SIGNATURE")

    def test_expired_messages_and_future_timestamps_fail_closed(self):
        message, payload = self.message()
        for timestamp in (time.time() - 301, time.time() + 31):
            bad = message | {"timestamp": timestamp, "expires_at": timestamp + 300}
            bad["signature"] = self.s.messages._mac(bad)
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(bad, payload, "supervisor")
            self.assertEqual(error.exception.code, "MESSAGE_EXPIRED")

    def test_root_key_mismatch_fails_startup_without_overwriting_state(self):
        with self.assertRaises(Rejected) as error:
            TrustaService(replace(self.config, signing_key=b"different-root-key"))
        self.assertEqual(error.exception.code, "MESSAGE_ROOT_KEY_MISMATCH")
        self.assertEqual(TrustaService(self.config).message_keys.current_version(), 1)

    def test_concurrent_operators_rotate_to_unique_epochs(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            versions = list(pool.map(lambda _: self.s.message_keys.rotate()["current_version"], range(3)))
        self.assertEqual(set(versions), {2, 3, 4})
        self.assertEqual(self.s.message_keys.current_version(), 4)
        self.assertEqual(sum(v["status"] == "ACTIVE" for v in self.s.message_keys.status()["versions"]), 1)

    def test_rotation_preserves_existing_execution_contract_and_action(self):
        task = self.s.create_task("给张三转2000元")
        action = task["proposed_actions"][0]
        auth = self.s.auth.decide(task["authorization"]["authorization_id"], "demo", action, True)
        contract = self.s.contracts.issue(action, task["policy_decision"]["policy_decision_id"], auth["authorization_id"])
        seal, action_hash = contract["contract_hash"], digest(action)
        self.s.message_keys.rotate(0)
        self.s.message_keys.revoke(1)
        restored = TrustaService(self.config)
        restored.contracts.validate(contract, action)
        self.assertEqual(restored.contracts._seal(contract), seal)
        result = restored.gateway.execute("executor", action, contract)
        self.assertEqual(result["status"], "SETTLED")
        self.assertEqual(digest(action), action_hash)
        self.assertEqual(restored.db.one("SELECT COUNT(*) n FROM transactions")["n"], 1)

    def test_registry_is_consistent_and_inactive_agent_cannot_read_bank_or_sign(self):
        self.s.registry.validate()
        self.assertEqual(len(self.s.registry.snapshot()["agents"]), 5)
        for agent in ("supervisor", "planner", "transaction", "finance", "wealth"):
            with self.assertRaises(Rejected):
                self.s.capabilities.check(agent, "banking.transfer")
        with patch.dict(REGISTRY["finance"], {"active": False}):
            with self.assertRaises(Rejected):
                self.s.capabilities.check("finance", "account.read")
            with self.assertRaises(Rejected):
                self.s.registry.validate()

    def test_extension_interfaces_are_disabled_and_expose_only_digests(self):
        for name in self.s.extensions.NAMES:
            self.assertFalse(self.s.extensions.status()[name]["enabled"])
            with self.assertRaises(Rejected):
                self.s.extensions.require(name)
        AnchorCommitment("a" * 64)
        for value in ("张三", {"account": "acct-demo"}, "a" * 63):
            with self.assertRaises(Rejected):
                AnchorCommitment(value)
        status = canonical(self.s.security_status())
        self.assertNotIn("communication-test-key", status)
        self.assertNotIn("root_binding", status)
