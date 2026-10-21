import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend.config import Config
from backend.domain import Rejected, canonical, digest
from backend.audit_trust import merkle
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.audit_trust.records import verify_record
from backend.audit_trust.anchor import AnchorWorker
from backend.security.crypto import public_pem
from cryptography.hazmat.primitives.asymmetric import ec
from backend.service import TrustaService


class AuditProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "audit.db"), signing_key=b"phase4-audit-test-root-key", communication_version=3)
        self.s = TrustaService(self.config)

    def complete(self):
        task = self.s.create_task("给张三转2000元")
        return self.s.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)

    def bundle(self):
        task = self.complete()
        checkpoint = self.s.checkpoints.generate_checkpoint()
        return task, checkpoint, self.s.records.query("demo", receipt_id=task["receipt"]["receipt_id"])

    def test_merkle_single_odd_even_trees_all_leaf_positions(self):
        for count in range(1, 18):
            hashes = [digest({"event": index}) for index in range(count)]
            root = merkle.root(hashes)
            for index, event_hash in enumerate(hashes):
                self.assertTrue(merkle.verify_inclusion_proof(event_hash, index, count, merkle.proof(hashes, index), root))
                self.assertFalse(merkle.verify_inclusion_proof("0"*64, index, count, merkle.proof(hashes, index), root))
        leaf = digest({"x": 1})
        self.assertEqual(merkle.root([leaf]), hashlib.sha256(b"\x00" + bytes.fromhex(leaf)).hexdigest())

    def test_checkpoint_freezes_contiguous_batch_and_retains_hash_chain(self):
        task = self.complete()
        first = self.s.checkpoints.generate_checkpoint(8)
        second = self.s.checkpoints.generate_checkpoint(8)
        self.assertEqual(first["leaf_count"], 8)
        self.assertEqual(second["first_event_sequence"], first["last_event_sequence"] + 1)
        self.assertEqual(second["previous_checkpoint_hash"], digest(first))
        self.assertTrue(self.s.checkpoints.verify_checkpoint(first))
        self.assertTrue(self.s.audit.verify_chain())
        self.assertTrue(self.s.checkpoints.verify_storage())

    def test_receipt_event_transaction_queries_same_verified_evidence(self):
        task, checkpoint, bundle = self.bundle()
        receipt = task["receipt"]
        by_transaction = self.s.records.query("demo", transaction_id=receipt["bank_transaction_id"])
        by_event = self.s.records.query("demo", audit_event_id=receipt["audit_ref"])
        self.assertEqual(by_transaction["event_hash"], bundle["event_hash"])
        self.assertEqual(by_event["checkpoint"], checkpoint)
        self.assertTrue(verify_record(bundle, bundle["signer_public_key"], digest(checkpoint)))
        self.assertEqual(bundle["receipt_schema_version"], 1)
        self.assertEqual(bundle["anchor"]["status"], "PENDING")

    def test_proof_requires_pinned_public_key_not_self_supplied_key(self):
        _, _, bundle = self.bundle()
        wrong = public_pem(ec.generate_private_key(ec.SECP256R1()).public_key())
        self.assertFalse(verify_record(bundle | {"signer_public_key": wrong}, wrong))
        self.assertTrue(verify_record(bundle | {"signer_public_key": wrong}, bundle["signer_public_key"]))
        self.assertFalse(verify_record(bundle, bundle["signer_public_key"], "0" * 64))

    def test_leaf_path_sequence_and_checkpoint_tampering_detected(self):
        _, _, bundle = self.bundle()
        changed = copy.deepcopy(bundle)
        changed["inclusion_proof"][0]["hash"] = "0" * 64
        self.assertFalse(verify_record(changed, bundle["signer_public_key"]))
        for update in ({"event_hash": "0" * 64}, {"leaf_index": True}, {"event_sequence": bundle["event_sequence"]+1},
                       {"merkle_root": "0" * 64}, {"inclusion_proof": bundle["inclusion_proof"][:-1]}):
            self.assertFalse(verify_record(bundle | update, bundle["signer_public_key"]))
        for field, value in (("merkle_root", "0"*64), ("created_at", 0), ("leaf_count", 1),
                             ("previous_checkpoint_hash", "f"*64), ("signature_algorithm", "HMAC-SHA256")):
            changed = copy.deepcopy(bundle)
            changed["checkpoint"][field] = value
            self.assertFalse(verify_record(changed, bundle["signer_public_key"]))

    def test_receipt_hash_binding_detects_amount_action_and_receipt_id_tamper(self):
        _, _, bundle = self.bundle()
        for field in ("receipt_id", "bank_transaction_id", "approved_action_hash", "authorization_id", "post_state_hash"):
            changed = copy.deepcopy(bundle)
            changed["receipt"][field] = "forged"
            self.assertFalse(verify_record(changed, bundle["signer_public_key"]))

    def test_historical_event_tampering_detected_even_with_retained_frozen_root(self):
        task, _, bundle = self.bundle()
        with self.s.db.transaction() as conn:
            conn.execute("DROP TRIGGER audit_no_update")
            event = bundle["event"] | {"actor": "attacker"}
            conn.execute("UPDATE audit_events SET body=? WHERE seq=?", (canonical(event), bundle["event_sequence"]))
        self.assertFalse(self.s.audit.verify_chain())
        with self.assertRaises(Rejected):
            self.s.records.query("demo", receipt_id=task["receipt"]["receipt_id"])
        self.assertTrue(verify_record(bundle, bundle["signer_public_key"]))  # Previously exported historical evidence stays verifiable.
        with self.assertRaises(Rejected):
            TrustaService(self.config)

    def test_immutable_tables_and_corrupt_leaf_fail_startup(self):
        _, checkpoint, bundle = self.bundle()
        with self.s.db.transaction() as conn:
            with self.assertRaises(Exception):
                conn.execute("UPDATE audit_checkpoints SET body='{}'")
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM audit_checkpoint_leaves")
        with self.s.db.transaction() as conn:
            conn.execute("DROP TRIGGER checkpoint_leaf_no_update")
            conn.execute("UPDATE audit_checkpoint_leaves SET event_hash=? WHERE event_id=?", ("0"*64, bundle["audit_event_id"]))
        with self.assertRaises(Rejected):
            self.s.checkpoints.generate_inclusion_proof(bundle["audit_event_id"])
        with self.assertRaises(Rejected):
            TrustaService(self.config)

    def test_capability_keys_and_checkpoints_survive_restart_and_rotation(self):
        task, checkpoint, bundle = self.bundle()
        self.s.crypto_registry.rotate("audit-checkpoint", 0)
        self.s.crypto_registry.revoke_key(checkpoint["signer_key_id"])
        restarted = TrustaService(self.config)
        restored = restarted.records.query("demo", receipt_id=task["receipt"]["receipt_id"])
        self.assertTrue(restored["record_verified"])
        self.assertEqual(restored["signer_key_status"], "REVOKED")
        # Historical mathematical validity is distinct from trusting a compromised old signer.
        self.assertTrue(verify_record(bundle, bundle["signer_public_key"]))
        self.assertEqual(restarted.get_task(task["task_id"])["receipt"], task["receipt"])

    def test_upgrade_preserves_old_receipt_and_action_hash(self):
        old_config = replace(self.config, db_path=str(Path(self.temp.name)/"legacy.db"), communication_version=2)
        old = TrustaService(old_config)
        task = old.create_task("给张三转2000元")
        completed = old.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        upgraded = TrustaService(replace(old_config, communication_version=3))
        self.assertEqual(upgraded.get_task(task["task_id"])["receipt"], completed["receipt"])
        self.assertEqual(upgraded.get_task(task["task_id"])["proposed_actions"], completed["proposed_actions"])
        upgraded.checkpoints.generate_checkpoint()
        bundle = upgraded.records.query("demo", receipt_id=completed["receipt"]["receipt_id"])
        self.assertTrue(verify_record(bundle, bundle["signer_public_key"]))

    def test_owner_check_and_uncheckpointed_record_do_not_fabricate_proof(self):
        task = self.complete()
        with self.assertRaises(Rejected) as error:
            self.s.records.query("demo", receipt_id=task["receipt"]["receipt_id"])
        self.assertEqual(error.exception.code, "AUDIT_CHECKPOINT_PENDING")
        self.s.checkpoints.generate_checkpoint()
        with self.assertRaises(Rejected) as error:
            self.s.records.query("other", receipt_id=task["receipt"]["receipt_id"])
        self.assertEqual(error.exception.code, "TASK_ACCESS_DENIED")
        first = json.loads(self.s.db.one("SELECT body FROM audit_events ORDER BY seq LIMIT 1")["body"])
        with self.assertRaises(Rejected) as error:
            self.s.records.query("demo", audit_event_id=first["audit_event_id"])
        self.assertEqual(error.exception.code, "AUDIT_ACCESS_DENIED")

    def test_valid_proof_is_stable_after_new_events_and_new_batches(self):
        _, checkpoint, bundle = self.bundle()
        self.s.create_task("分析2026-09消费")
        self.s.checkpoints.generate_checkpoint()
        self.assertTrue(verify_record(bundle, bundle["signer_public_key"], digest(checkpoint)))
        self.assertTrue(self.s.checkpoints.verify_storage())

    def test_anchor_pending_submitted_confirmed_and_idempotent_restart(self):
        _, checkpoint, _ = self.bundle()
        self.assertEqual(self.s.anchors.status(checkpoint["checkpoint_id"])["status"], "PENDING")
        original = self.s.anchors.adapter.inspect
        observed = []
        def inspect(reference):
            observed.append(self.s.anchors.status(checkpoint["checkpoint_id"])["status"])
            return original(reference)
        with patch.object(self.s.anchors.adapter, "inspect", side_effect=inspect):
            job = self.s.anchors.process_one(checkpoint["checkpoint_id"])
        self.assertEqual(observed, ["SUBMITTED"])
        self.assertEqual(job["status"], "CONFIRMED")
        restarted = TrustaService(self.config)
        self.assertTrue(restarted.anchors.verify_anchor(checkpoint["checkpoint_id"]))
        self.assertEqual(restarted.anchors.process_one(checkpoint["checkpoint_id"])["anchor_ref"], job["anchor_ref"])
        self.assertEqual(self.s.db.one("SELECT COUNT(*) n FROM local_audit_anchors")["n"], 1)

    def test_failed_anchor_is_nonblocking_and_explicit_retry(self):
        checkpoint = self.s.checkpoints.generate_checkpoint()
        with patch.object(self.s.anchors.adapter, "submit", side_effect=TimeoutError("offline")):
            job = self.s.anchors.process_one(checkpoint["checkpoint_id"])
        self.assertEqual(job["status"], "FAILED")
        done = self.complete()
        self.assertEqual(done["verification"]["status"], "VERIFIED")
        self.s.anchors.retry(checkpoint["checkpoint_id"])
        self.assertEqual(self.s.anchors.process_one(checkpoint["checkpoint_id"])["status"], "CONFIRMED")

    def test_bank_execution_never_waits_for_slow_anchor(self):
        checkpoint = self.s.checkpoints.generate_checkpoint()
        entered, release = threading.Event(), threading.Event()
        original = self.s.anchors.adapter.submit
        def slow(commitment, checkpoint_hash):
            entered.set()
            release.wait(5)
            return original(commitment, checkpoint_hash)
        with patch.object(self.s.anchors.adapter, "submit", side_effect=slow):
            thread = threading.Thread(target=self.s.anchors.process_one, args=(checkpoint["checkpoint_id"],))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.complete()["status"], "COMPLETED")
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_anchor_root_mismatch_detected(self):
        _, checkpoint, _ = self.bundle()
        job = self.s.anchors.process_one(checkpoint["checkpoint_id"])
        with self.s.db.transaction() as conn:
            body = json.loads(conn.execute("SELECT body FROM local_audit_anchors WHERE anchor_ref=?", (job["anchor_ref"],)).fetchone()["body"])
            body["root"] = "0" * 64
            conn.execute("UPDATE local_audit_anchors SET root=?,body=? WHERE anchor_ref=?", (body["root"], canonical(body), job["anchor_ref"]))
        self.assertFalse(self.s.anchors.verify_anchor(checkpoint["checkpoint_id"]))

    def test_real_chain_flag_never_claims_unimplemented_chain_success(self):
        other = TrustaService(replace(self.config, blockchain_anchor_enabled=True))
        done = other.create_task("分析2026-09消费")
        self.assertEqual(done["status"], "COMPLETED")
        report = other.anchors.run_once()
        self.assertTrue(report["jobs"])
        self.assertTrue(all(job["status"] == "FAILED" for job in report["jobs"]))
        self.assertFalse(other.security_status()["extensions"]["blockchain_anchor"]["implemented"])

    def test_anchor_submission_contains_only_two_commitments_no_financial_payload(self):
        _, checkpoint, _ = self.bundle()
        original = self.s.anchors.adapter.submit
        with patch.object(self.s.anchors.adapter, "submit", wraps=original) as submit:
            self.s.anchors.process_one(checkpoint["checkpoint_id"])
        commitment, checkpoint_hash = submit.call_args.args
        self.assertEqual(commitment.digest, checkpoint["merkle_root"])
        self.assertEqual(checkpoint_hash, digest(checkpoint))
        self.assertNotIn("张三", str(submit.call_args))
        self.assertNotIn("acct-demo", str(submit.call_args))

    def test_resume_submitted_anchor_without_resubmitting(self):
        _, checkpoint, _ = self.bundle()
        original = self.s.anchors.adapter.inspect
        with patch.object(self.s.anchors.adapter, "inspect", side_effect=lambda ref: original(ref) | {"status": "SUBMITTED"}):
            job = self.s.anchors.process_one(checkpoint["checkpoint_id"])
        self.assertEqual(job["status"], "SUBMITTED")
        restarted = TrustaService(self.config)
        with patch.object(restarted.anchors.adapter, "submit", side_effect=AssertionError("duplicate submit")):
            self.assertEqual(restarted.anchors.process_one(checkpoint["checkpoint_id"])["status"], "CONFIRMED")

    def test_malformed_historical_audit_fails_closed_instead_of_false_success(self):
        with self.s.db.transaction() as conn:
            conn.execute("DROP TRIGGER audit_no_update")
            conn.execute("UPDATE audit_events SET body='{}' WHERE seq=1")
        self.assertFalse(self.s.audit.verify_chain())
        with self.assertRaises(Rejected) as error:
            TrustaService(self.config)
        self.assertEqual(error.exception.code, "AUDIT_CHAIN_INVALID")

    def test_checkpoint_generator_rejects_tampered_frozen_storage(self):
        _, checkpoint, _ = self.bundle()
        self.s.audit.append(None, None, "TEST_NEW_EVENT", "test", {})
        with self.s.db.transaction() as conn:
            conn.execute("DROP TRIGGER checkpoint_leaf_no_update")
            conn.execute("UPDATE audit_checkpoint_leaves SET event_hash=? WHERE leaf_index=0", ("0"*64,))
        with self.assertRaises(Rejected):
            self.s.checkpoints.generate_checkpoint()
