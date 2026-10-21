import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from backend.config import Config
from backend.domain import Rejected, canonical, digest
from backend.security.crypto import sign_object, verify_object
from backend.security.message_v3 import MESSAGE_DOMAIN
from backend.security.registry import BANK_GRANTS
from backend.service import TrustaService


class CryptoCommunicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "crypto.db"), signing_key=b"phase4-communication-test-root-key", communication_version=3)
        self.s = TrustaService(self.config)

    def message(self):
        task = self.s.create_task("给张三转2000元")
        self.assertEqual(task["status"], "WAITING_USER")
        payload = task["proposed_actions"][0]
        ref = f"task:{task['task_id']}:agent_results:transaction:ACTION_PROPOSED"
        return self.s.messages.sign("transaction", "supervisor", task["task_id"], "T05", "ACTION_PROPOSED", ref, payload), payload

    def resign(self, message):
        # Test attacker owns a legitimate agent key, but never financial execution authority.
        key = self.s.db.one("SELECT body FROM crypto_keys WHERE key_id=?", (message["sender_key_id"],))
        record = self.s.crypto_registry._decode(key)
        body = {k: v for k, v in message.items() if k != "signature"}
        return body | {"signature": sign_object(self.s.crypto_registry.store.load(record), MESSAGE_DOMAIN, body)}

    def assert_rejected_without_nonce(self, message, payload, code=None):
        with self.assertRaises(Rejected) as error:
            self.s.accept_message(message, payload, "supervisor")
        if code:
            self.assertEqual(error.exception.code, code)
        self.assertFalse(self.s.db.one("SELECT nonce FROM replay_nonces WHERE nonce=?", (message["nonce"],)))
        self.assertEqual(self.s.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"], 1000000)

    def test_real_asymmetric_signature_can_be_verified_using_only_public_key(self):
        message, payload = self.message()
        identity = self.s.crypto_registry.resolve("transaction")
        self.assertEqual(message["schema_version"], 3)
        self.assertEqual(message["signature_algorithm"], "ECDSA-P256-SHA256")
        verify_object(identity["public_key"], MESSAGE_DOMAIN, {k: v for k, v in message.items() if k != "signature"}, message["signature"])
        self.s.accept_message(message, payload, "supervisor")
        self.assertTrue(self.s.audit.verify_chain())
        status = canonical(self.s.security_status())
        self.assertNotIn("PRIVATE KEY", status)
        self.assertNotIn("registry_seal", status)
        self.assertEqual(len(self.s.security_status()["messages"]["agents"]), 5)

    def test_forged_identity_rejected(self):
        message, payload = self.message()
        self.assert_rejected_without_nonce(message | {"sender_identity": "urn:trusta:attacker"}, payload, "FORGED_AGENT_IDENTITY")

    def test_cross_agent_key_cannot_spoof_sender(self):
        message, payload = self.message()
        self.assert_rejected_without_nonce(message | {"sender_agent": "wealth"}, payload, "IDENTITY_KEY_MISMATCH")

    def test_signature_and_payload_tampering_fail_before_result_write(self):
        message, payload = self.message()
        original = copy.deepcopy(self.s.blackboard.get(message["task_id"])["agent_results"])
        self.assert_rejected_without_nonce(message | {"signature": "A" * 96}, payload, "INVALID_SIGNATURE")
        self.assertEqual(original, self.s.blackboard.get(message["task_id"])["agent_results"])
        message, payload = self.message()
        self.assert_rejected_without_nonce(message, payload | {"amount": 1}, "PAYLOAD_TAMPERED")

    def test_all_envelope_security_fields_are_signed(self):
        message, payload = self.message()
        for field, value in (("message_id", "forged"), ("task_id", "other"), ("step_id", "T08"),
                             ("receiver_agent", "wealth"), ("message_type", "PLAN"), ("payload_ref", "other"),
                             ("payload_hash", "0" * 64), ("payload_version", 1),
                             ("capability_snapshot_hash", "0" * 64), ("issued_at", message["issued_at"] - 1),
                             ("expires_at", message["expires_at"] + 1), ("nonce", "forged")):
            with self.subTest(field=field), self.assertRaises(Rejected):
                self.s.messages.verify(message | {field: value}, payload, "supervisor", consume=False)
        self.s.accept_message(message, payload, "supervisor")

    def test_unknown_schema_algorithm_and_stripping_fields_cannot_downgrade(self):
        message, payload = self.message()
        for changed in (message | {"schema_version": 2}, message | {"schema_version": True},
                        message | {"signature_algorithm": "HMAC-SHA256"},
                        {k: v for k, v in message.items() if k != "schema_version"},
                        message | {"unknown": "value"}):
            with self.assertRaises(Rejected):
                self.s.messages.verify(changed, payload, "supervisor", consume=False)
        self.s.accept_message(message, payload, "supervisor")

    def test_replay_is_persistent_across_rotation_and_restart(self):
        message, payload = self.message()
        self.s.accept_message(message, payload, "supervisor")
        self.s.crypto_registry.rotate("transaction")
        restarted = TrustaService(self.config)
        with self.assertRaises(Rejected) as error:
            restarted.messages.verify(message, payload, "supervisor")
        self.assertEqual(error.exception.code, "REPLAY_BLOCKED")

    def test_expired_and_future_signed_messages_rejected(self):
        message, payload = self.message()
        for offset in (-301000, 31000):
            changed = self.resign(message | {"issued_at": int(time.time()*1000) + offset,
                                            "expires_at": int(time.time()*1000) + offset + 300000})
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(changed, payload, "supervisor", consume=False)
            self.assertEqual(error.exception.code, "MESSAGE_EXPIRED")
        self.s.accept_message(message, payload, "supervisor")

    def test_rotation_grace_and_expiration(self):
        message, payload = self.message()
        old_key = message["sender_key_id"]
        updated = self.s.crypto_registry.rotate("transaction", 2)
        self.assertNotEqual(old_key, updated["key_id"])
        self.s.messages.verify(message, payload, "supervisor", consume=False)
        with patch("backend.security.crypto_registry.time.time", return_value=time.time()+3):
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(message, payload, "supervisor", consume=False)
            self.assertEqual(error.exception.code, "MESSAGE_KEY_EXPIRED")

    def test_revocation_immediate_but_current_key_requires_rotation(self):
        message, payload = self.message()
        with self.assertRaises(Rejected):
            self.s.crypto_registry.revoke_key(message["sender_key_id"])
        self.s.crypto_registry.rotate("transaction")
        self.s.crypto_registry.revoke_key(message["sender_key_id"])
        self.assert_rejected_without_nonce(message, payload, "MESSAGE_KEY_REVOKED")

    def test_suspended_and_revoked_agent_is_blocked_after_restart(self):
        message, payload = self.message()
        self.s.crypto_registry.set_status("transaction", "SUSPENDED")
        restarted = TrustaService(self.config)
        with self.assertRaises(Rejected):
            restarted.messages.verify(message, payload, "supervisor")
        with self.assertRaises(Rejected):
            restarted.gateway.read("transaction", "demo", "account.get", account_id="acct-demo")
        self.s.crypto_registry.set_status("transaction", "ACTIVE")
        self.s.crypto_registry.set_status("transaction", "REVOKED")
        with self.assertRaises(Rejected):
            self.s.crypto_registry.set_status("transaction", "ACTIVE")

    def test_signed_capability_commitment_tampering_cannot_escalate(self):
        message, payload = self.message()
        forged = self.resign(message | {"capability_snapshot_hash": digest(["banking.transfer"])})
        self.assert_rejected_without_nonce(forged, payload, "CAPABILITY_SNAPSHOT_MISMATCH")

    def test_capability_change_requires_sync_and_invalidates_old_message(self):
        message, payload = self.message()
        old = set(BANK_GRANTS["transaction"])
        try:
            BANK_GRANTS["transaction"].remove("balance.read")
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(message, payload, "supervisor", consume=False)
            self.assertEqual(error.exception.code, "CAPABILITY_COMMITMENT_STALE")
            self.s.crypto_registry.sync_capabilities("transaction")
            with self.assertRaises(Rejected) as error:
                self.s.messages.verify(message, payload, "supervisor", consume=False)
            self.assertEqual(error.exception.code, "CAPABILITY_SNAPSHOT_MISMATCH")
        finally:
            BANK_GRANTS["transaction"].clear()
            BANK_GRANTS["transaction"].update(old)
            self.s.crypto_registry.sync_capabilities("transaction")

    def test_valid_signed_message_does_not_grant_bank_execute_authority(self):
        message, payload = self.message()
        self.s.accept_message(message, payload, "supervisor")
        with self.assertRaises(Rejected):
            self.s.gateway.execute("transaction", payload, None)
        with self.assertRaises(Rejected):
            self.s.contracts.issue(payload, self.s.get_task(message["task_id"])["policy_decision"]["policy_decision_id"], "missing")
        self.assertEqual(self.s.db.rows("SELECT * FROM transactions"), [])

    def test_signed_wrong_task_revision_ref_receiver_or_edge_rejected(self):
        for update, code in (({"payload_version": 0}, "MESSAGE_STALE_PAYLOAD"),
                             ({"payload_ref": "task:other"}, "MESSAGE_REFERENCE_DENIED"),
                             ({"receiver_agent": "wealth"}, "INVALID_RECEIVER"),
                             ({"step_id": "T08"}, "MESSAGE_CONTEXT_DENIED")):
            message, payload = self.message()
            self.assert_rejected_without_nonce(self.resign(message | update), payload, code)

    def test_illegal_handoff_does_not_commit_agent_results(self):
        message, payload = self.message()
        original = copy.deepcopy(self.s.blackboard.get(message["task_id"])["agent_results"])
        with patch.object(self.s.messages, "verify_in", side_effect=Rejected("INVALID_SIGNATURE")):
            with self.assertRaises(Rejected):
                self.s._agent_message("transaction", "supervisor", message["task_id"], "T05", "ACTION_PROPOSED", payload)
        self.assertEqual(original, self.s.blackboard.get(message["task_id"])["agent_results"])

    def test_message_acceptance_audit_failure_rolls_back_nonce(self):
        message, payload = self.message()
        with patch.object(self.s.audit, "append_in", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.s.accept_message(message, payload, "supervisor")
        self.assertFalse(self.s.db.one("SELECT nonce FROM replay_nonces WHERE nonce=?", (message["nonce"],)))
        self.s.accept_message(message, payload, "supervisor")

    def test_upgrade_rejects_runtime_downgrade_and_preserves_legacy_contract(self):
        with self.assertRaises(Rejected) as error:
            TrustaService(replace(self.config, communication_version=2))
        self.assertEqual(error.exception.code, "MESSAGE_DOWNGRADE_DENIED")
        message, payload = self.message()
        task = self.s.get_task(message["task_id"])
        auth = self.s.auth.decide(task["authorization"]["authorization_id"], "demo", payload, True)
        contract = self.s.contracts.issue(payload, task["policy_decision"]["policy_decision_id"], auth["authorization_id"])
        self.s.crypto_registry.rotate("transaction", 0)
        self.s.crypto_registry.revoke_key(message["sender_key_id"])
        TrustaService(self.config).contracts.validate(contract, payload)

    def test_missing_key_file_is_never_silently_regenerated(self):
        key_id = self.s.crypto_registry.resolve("transaction")["key_id"]
        path = self.s.crypto_registry.store.path(key_id)
        path.unlink()
        with self.assertRaises(Rejected) as error:
            TrustaService(self.config)
        self.assertEqual(error.exception.code, "PRIVATE_KEY_UNAVAILABLE")
        self.assertFalse(path.exists())

    def test_registry_metadata_and_public_key_tampering_detected(self):
        with self.s.db.transaction() as conn:
            record = json.loads(conn.execute("SELECT body FROM crypto_identities WHERE agent_id='transaction'").fetchone()["body"])
            record["capability_hash"] = "0" * 64
            conn.execute("UPDATE crypto_identities SET body=? WHERE agent_id='transaction'", (canonical(record),))
        with self.assertRaises(Rejected) as error:
            TrustaService(self.config)
        self.assertEqual(error.exception.code, "IDENTITY_REGISTRY_TAMPERED")

    def test_key_store_contains_only_encrypted_pkcs8_and_unique_public_keys(self):
        identities = self.s.crypto_registry.snapshot()["agents"]
        self.assertEqual(len({item["public_key_fingerprint"] for item in identities}), 5)
        for identity in identities:
            self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", self.s.crypto_registry.store.path(identity["key_id"]).read_bytes())
        self.assertNotIn("PRIVATE KEY", canonical(self.s.db.rows("SELECT body FROM crypto_keys")))

    def test_crypto_identity_no_pseudo_did_and_truthful_extensions(self):
        self.assertIs(self.s.extensions.require("ecdsa"), self.s.messages)
        self.assertIs(self.s.extensions.require("merkle_audit"), self.s.checkpoints)
        for name in ("did", "blockchain_anchor", "zkp"):
            with self.assertRaises(Rejected):
                self.s.extensions.require(name)
        self.assertFalse(self.s.security_status()["messages"]["did_implemented"])
        self.assertTrue(all(i["identity_uri"].startswith("urn:trusta:") for i in self.s.crypto_registry.snapshot()["agents"]))

    def test_suspended_agent_cannot_read_memory_or_call_configured_model(self):
        from backend.llm.contracts import InferenceRequest, InferenceContext, InferenceError
        from backend.llm.configuration import LLMSettings, ProviderConfig
        from backend.llm.gateway import LLMGateway
        from backend.domain import uid
        task = self.s.create_task("分析2026-09消费")
        gateway = LLMGateway(LLMSettings(mode="shadow", providers=(ProviderConfig("offline", max_sensitivity="restricted"),)), self.s.db, self.s.audit)
        gateway.identity_guard = self.s.crypto_registry.require_active
        self.s.crypto_registry.set_status("finance", "SUSPENDED")
        with self.assertRaises(Rejected):
            self.s.memory.read("finance", "demo", "PREFERENCE")
        request = InferenceRequest(uid(), InferenceContext(task["task_id"], "demo", "finance", "BILL_ANALYSIS", task["revision"]),
                                   "finance_explanation", {"month": "2026-09", "net_spend_cents": 542500,
                                                           "settled_count": 11, "evidence_refs": ["monthly_summary"]})
        with self.assertRaises(InferenceError) as error:
            gateway.infer(request)
        self.assertEqual(error.exception.code, "LLM_CAPABILITY_DENIED")
        self.assertEqual(self.s.db.rows("SELECT * FROM llm_calls"), [])

    def test_concurrent_rotations_create_unique_persisted_versions(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.s.crypto_registry.rotate("transaction"), range(3)))
        self.assertEqual({item["key_version"] for item in results}, {2, 3, 4})
        restored = TrustaService(self.config)
        self.assertEqual(restored.crypto_registry.resolve("transaction")["key_version"], 4)
        self.assertEqual(sum(item["status"] == "ACTIVE" for item in restored.crypto_registry.snapshot()["keys"] if item["agent_id"] == "transaction"), 1)
