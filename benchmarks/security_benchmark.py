"""18 real attack workloads, positive controls and wall-clock measurements. No unittest mocks."""
import argparse
import copy
import json
import math
from pathlib import Path
import platform
import statistics
import tempfile
import time
import cryptography
from cryptography.hazmat.backends.openssl.backend import backend as openssl_backend
from backend.config import Config
from backend.domain import Rejected, canonical, digest
from backend.service import TrustaService
from backend.security.crypto import sign_object
from backend.security.message_v3 import MESSAGE_DOMAIN
from backend.audit_trust.records import verify_record
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.audit_trust.merkle import verify_inclusion_proof

CASE_NAMES = ("forged_agent_identity", "invalid_signature", "payload_tampering", "replay_attack",
              "expired_message", "revoked_key", "old_key_outside_grace", "capability_escalation",
              "capability_snapshot_tampering", "unauthorized_tool_invocation", "authorization_action_hash_mismatch",
              "duplicate_financial_execution", "audit_event_tampering", "merkle_leaf_tampering",
              "invalid_merkle_proof", "checkpoint_tampering", "anchor_root_mismatch", "signature_algorithm_downgrade")


def latency(values):
    ordered = sorted(values)
    return {"samples": len(values), "mean_ms": statistics.mean(values), "p50_ms": statistics.median(values),
            "p95_ms": ordered[max(0, math.ceil(len(ordered)*0.95)-1)], "max_ms": max(values), "raw_ms": values}


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def run_benchmark(trials=5, valid_per_trial=20, audit_events=128, banking_version=4):
    if banking_version not in (4,5):
        raise ValueError("unsupported banking profile")
    if type(trials) is not int or not 1 <= trials <= 50 or not 1 <= valid_per_trial <= 200 or not 32 <= audit_events <= 4096:
        raise ValueError("unsupported benchmark workload size")
    started = time.perf_counter()
    cases = {name: [] for name in CASE_NAMES}
    samples = {"sign": [], "verify": [], "checkpoint": []}
    valid_messages = valid_proofs = rejected_valid_messages = rejected_valid_proofs = 0
    unauthorized_attempts = unauthorized_writes = 0
    batches = []
    versions = (3,) if banking_version==5 else (2,3)
    scene_samples = {str(version): {kind: [] for kind in ("TRANSFER", "BILL_ANALYSIS", "WEALTH_RECOMMENDATION")} for version in versions}
    with tempfile.TemporaryDirectory(prefix="trusta-security-benchmark-") as directory:
        for trial in range(trials):
            s = TrustaService(Config(db_path=str(Path(directory)/f"trial-{trial}.sqlite3"),
                                    signing_key=b"isolated-repeatable-benchmark-root-key", communication_version=3,banking_version=banking_version))
            task = s.create_task("给张三转2000元")
            if task["status"] != "WAITING_USER":
                raise RuntimeError("baseline transfer proposal failed")
            action = task["proposed_actions"][0]
            auth = s.auth.decide(task["authorization"]["authorization_id"], "demo", action, True)
            contract = s.contracts.issue(action, task["policy_decision"]["policy_decision_id"], auth["authorization_id"])
            def message():
                return s.messages.sign("transaction", "supervisor", task["task_id"], "T05", "ACTION_PROPOSED",
                                       f"task:{task['task_id']}:agent_results:transaction:ACTION_PROPOSED", action)
            def resign(envelope):
                record = s.crypto_registry._decode(s.db.one("SELECT body FROM crypto_keys WHERE key_id=?", (envelope["sender_key_id"],)))
                body = {k: v for k, v in envelope.items() if k != "signature"}
                return body | {"signature": sign_object(s.crypto_registry.store.load(record), MESSAGE_DOMAIN, body)}
            def check(envelope, payload=None):
                s.messages.verify(envelope, action if payload is None else payload, "supervisor", consume=False)
            def rejected_case(name, operation, expected_codes, financial=False):
                nonlocal unauthorized_attempts, unauthorized_writes
                before = s.db.one("SELECT COUNT(*) n FROM transactions")["n"]
                observed = None
                try:
                    operation()
                except Rejected as exc:
                    observed = exc.code
                after = s.db.one("SELECT COUNT(*) n FROM transactions")["n"]
                if financial:
                    unauthorized_attempts += 1
                    unauthorized_writes += int(after != before)
                cases[name].append({"trial": trial, "detected": observed in expected_codes and before == after,
                                    "observed_code": observed, "unexpected_transactions": after-before})
            def predicate_case(name, detected, observation):
                cases[name].append({"trial": trial, "detected": bool(detected), "observation": observation})

            for _ in range(valid_per_trial):
                begin = time.perf_counter_ns()
                envelope = message()
                samples["sign"].append((time.perf_counter_ns()-begin)/1e6)
                begin = time.perf_counter_ns()
                valid_messages += 1
                try:
                    check(envelope)
                except Rejected:
                    rejected_valid_messages += 1
                samples["verify"].append((time.perf_counter_ns()-begin)/1e6)
            original = message()
            rejected_case("forged_agent_identity", lambda: check(original | {"sender_identity": "urn:trusta:forged"}), {"FORGED_AGENT_IDENTITY"})
            rejected_case("invalid_signature", lambda: check(original | {"signature": "A"*96}), {"INVALID_SIGNATURE"})
            rejected_case("payload_tampering", lambda: check(original, action | {"amount": 1}), {"PAYLOAD_TAMPERED"})
            s.accept_message(original, action, "supervisor")
            rejected_case("replay_attack", lambda: s.messages.verify(original, action, "supervisor"), {"REPLAY_BLOCKED"})
            expired = resign(message() | {"issued_at": int(time.time()*1000)-301000, "expires_at": int(time.time()*1000)-1000})
            rejected_case("expired_message", lambda: check(expired), {"MESSAGE_EXPIRED"})
            revoked = message()
            s.crypto_registry.rotate("transaction")
            s.crypto_registry.revoke_key(revoked["sender_key_id"])
            rejected_case("revoked_key", lambda: check(revoked), {"MESSAGE_KEY_REVOKED"})
            outside_grace = message()
            s.crypto_registry.rotate("transaction", 0)
            rejected_case("old_key_outside_grace", lambda: check(outside_grace), {"MESSAGE_KEY_EXPIRED"})
            rejected_case("capability_escalation", lambda: s.gateway.execute("transaction", action, contract), {"CAPABILITY_VIOLATION"}, True)
            forged_cap = resign(message() | {"capability_snapshot_hash": digest(["banking.transfer"])})
            rejected_case("capability_snapshot_tampering", lambda: check(forged_cap), {"CAPABILITY_SNAPSHOT_MISMATCH"})
            rejected_case("unauthorized_tool_invocation", lambda: s.gateway.execute("finance", action, contract), {"CAPABILITY_VIOLATION"}, True)
            changed = copy.deepcopy(action)
            changed["amount"] += 100
            changed["expected_postconditions"]["after_balance"] -= 100
            rejected_case("authorization_action_hash_mismatch", lambda: s.gateway.execute("executor", changed, contract), {"ACTION_HASH_MISMATCH"}, True)
            first = s.gateway.execute("executor", action, contract)
            second = s.gateway.execute("executor", action, contract)
            count = s.db.one("SELECT COUNT(*) n FROM transactions")["n"]
            balance = s.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"]
            predicate_case("duplicate_financial_execution", first["id"] == second["id"] and count == 1 and balance == 800000,
                           {"transaction_count": count, "balance_cents": balance, "same_transaction": first["id"] == second["id"]})
            if s.verifier.verify(action, first["id"])["status"] != "VERIFIED":
                raise RuntimeError("benchmark bank result was not verified")
            record_id = s.audit.append(task["task_id"], action["action_id"], "BENCHMARK_VERIFIED_EXECUTION", "benchmark", {"transaction_hash": digest(first)})
            for index in range(audit_events):
                s.audit.append(task["task_id"], None, "BENCHMARK_AUDIT_EVENT", "benchmark", {"trial": trial, "index": index})
            checkpoints = []
            while True:
                begin = time.perf_counter_ns()
                checkpoint = s.checkpoints.generate_checkpoint(64)
                elapsed = (time.perf_counter_ns()-begin)/1e6
                if checkpoint is None:
                    break
                samples["checkpoint"].append(elapsed)
                batches.append(checkpoint["leaf_count"])
                checkpoints.append(checkpoint)
                s.anchors.process_one(checkpoint["checkpoint_id"])
            bundle = s.records.query("demo", audit_event_id=record_id)
            # Positive proofs span every batch and both ends of each tree.
            for checkpoint in checkpoints:
                leaves = s.db.rows("SELECT event_id FROM audit_checkpoint_leaves WHERE checkpoint_id=? ORDER BY leaf_index", (checkpoint["checkpoint_id"],))
                for entry in (leaves[0], leaves[-1]):
                    evidence = s.checkpoints.generate_inclusion_proof(entry["event_id"])
                    valid_proofs += 1
                    rejected_valid_proofs += int(not verify_record(evidence, evidence["signer_public_key"]))
            # Database-file attacker bypasses append-only trigger; restore after observing the real chain guard.
            sequence = bundle["event_sequence"]
            body = canonical(bundle["event"])
            with s.db.transaction() as conn:
                conn.execute("DROP TRIGGER audit_no_update")
                conn.execute("UPDATE audit_events SET body=? WHERE seq=?", (canonical(bundle["event"] | {"actor": "attacker"}), sequence))
            try:
                predicate_case("audit_event_tampering", not s.audit.verify_chain(), {"hash_chain_valid": s.audit.verify_chain()})
            finally:
                with s.db.transaction() as conn:
                    conn.execute("UPDATE audit_events SET body=? WHERE seq=?", (body, sequence))
                    conn.execute("CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'append only'); END")
            predicate_case("merkle_leaf_tampering", not verify_inclusion_proof("0"*64, bundle["leaf_index"], bundle["checkpoint"]["leaf_count"], bundle["inclusion_proof"], bundle["merkle_root"]), {"modified_event_hash": True})
            bad_proof = copy.deepcopy(bundle)
            bad_proof["inclusion_proof"][0]["hash"] = "0"*64
            predicate_case("invalid_merkle_proof", not verify_record(bad_proof, bundle["signer_public_key"]), {"modified_sibling": True})
            predicate_case("checkpoint_tampering", not verify_checkpoint(bundle["checkpoint"] | {"merkle_root": "0"*64}, bundle["signer_public_key"]), {"modified_signed_root": True})
            checkpoint_id = bundle["checkpoint"]["checkpoint_id"]
            job = s.anchors.status(checkpoint_id)
            row = s.db.one("SELECT * FROM local_audit_anchors WHERE anchor_ref=?", (job["anchor_ref"],))
            with s.db.transaction() as conn:
                changed_anchor = json.loads(row["body"]) | {"root": "0"*64}
                conn.execute("UPDATE local_audit_anchors SET root=?,body=? WHERE anchor_ref=?", ("0"*64, canonical(changed_anchor), row["anchor_ref"]))
            try:
                predicate_case("anchor_root_mismatch", not s.anchors.verify_anchor(checkpoint_id), {"modified_anchor_root": True})
            finally:
                with s.db.transaction() as conn:
                    conn.execute("UPDATE local_audit_anchors SET root=?,body=? WHERE anchor_ref=?", (row["root"], row["body"], row["anchor_ref"]))
            rejected_case("signature_algorithm_downgrade", lambda: check(message() | {"signature_algorithm": "HMAC-SHA256"}), {"INVALID_MESSAGE_ALGORITHM"})
            # Controls after attacks make failure-to-restore visible.
            valid_messages += 1
            try:
                check(message())
            except Rejected:
                rejected_valid_messages += 1
            if not s.audit.verify_chain() or not s.checkpoints.verify_storage() or not s.anchors.verify_anchor(checkpoint_id):
                raise RuntimeError("post-attack restoration check failed")
        # Paired complete business runs, with service/key initialization outside the measured interval.
        for trial in range(trials):
            for version in (versions if trial % 2 == 0 else tuple(reversed(versions))):
                scene_service = TrustaService(Config(db_path=str(Path(directory)/f"scenes-v{version}-{trial}.sqlite3"),
                                                    signing_key=b"isolated-business-latency-root-key", communication_version=version,banking_version=banking_version))
                for kind, text in (("TRANSFER", "给张三转2000元"), ("BILL_ANALYSIS", "分析2026-09消费"),
                                   ("WEALTH_RECOMMENDATION", "推荐适合我的理财产品")):
                    begin = time.perf_counter_ns()
                    result = scene_service.create_task(text)
                    if kind == "TRANSFER":
                        result = scene_service.authorize(result["task_id"], "demo", result["authorization"]["action_hash"], True)
                    elapsed = (time.perf_counter_ns()-begin)/1e6
                    if result["status"] != "COMPLETED":
                        raise RuntimeError("business latency control failed")
                    scene_samples[str(version)][kind].append(elapsed)
                if scene_service.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"] != 800000:
                    raise RuntimeError("business latency read-only balance control failed")
    def attack_rate(names):
        observations = [item for name in names for item in cases[name]]
        return rate(sum(item["detected"] for item in observations), len(observations))
    metrics = {"forgery_detection_rate": attack_rate(["forged_agent_identity", "invalid_signature"]),
               "message_tamper_detection_rate": attack_rate(["payload_tampering", "capability_snapshot_tampering"]),
               "replay_block_rate": attack_rate(["replay_attack"]),
               "unauthorized_execution_rate": rate(unauthorized_writes, unauthorized_attempts),
               "audit_tamper_detection_rate": attack_rate(["audit_event_tampering", "checkpoint_tampering"]),
               "merkle_proof_verification_rate": rate(valid_proofs-rejected_valid_proofs, valid_proofs),
               "false_rejection_rate": rate(rejected_valid_messages+rejected_valid_proofs, valid_messages+valid_proofs)}
    passed = (all(item["detected"] for observations in cases.values() for item in observations)
              and unauthorized_writes == 0 and rejected_valid_messages == 0 and rejected_valid_proofs == 0)
    return {"schema_version": 1, "run_at_unix_ms": int(time.time()*1000), "passed": passed,"banking_version":banking_version,
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "cryptography": cryptography.__version__, "openssl": openssl_backend.openssl_version_text()},
            "workload": {"trials": trials, "attack_categories": len(cases), "attack_attempts": sum(map(len, cases.values())),
                         "valid_messages": valid_messages, "valid_proofs": valid_proofs,
                         "synthetic_audit_events_per_trial": audit_events, "checkpoint_batch_sizes": batches,
                         "isolated_sqlite_bank": True, "unit_test_harness": False},
            "metrics": metrics, "cases": cases, "latency": {name: latency(values) for name, values in samples.items()},
            "latency_scope": {"sign": "registry lookup + in-memory ECDSA private signing; key generation/decryption excluded",
                              "verify": "public signature + identity/capability + task context; nonce consumption/audit excluded",
                              "checkpoint": "whole-chain validation + batch Merkle + signature + durable checkpoint/outbox"},
            "business_latency_ms": {version: {kind: latency(values) for kind, values in scenarios.items()} for version,scenarios in scene_samples.items()},
            "business_latency_scope": "create task through completed result including sandbox user-authorization call, excluding human think-time/HTTP/startup; models disabled",
            "business_latency_order": "V3 only on six-agent Phase5" if banking_version==5 else "paired V2/V3 order alternates by trial; ordinary Windows host, no isolated performance lab",
            "duration_seconds": time.perf_counter()-started,
            "limits": ["Local controlled attacks, not a production penetration test or probability-of-security estimate",
                       "Local development anchor only; no externally verified blockchain",
                       "Synthetic sandbox bank and audit records; measured filesystem/SQLite costs depend on host"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--valid-per-trial", type=int, default=20)
    parser.add_argument("--audit-events", type=int, default=128)
    parser.add_argument("--output", default="docs/phase4-security-benchmark.json")
    parser.add_argument("--banking-version",type=int,choices=(4,5),default=4)
    args = parser.parse_args()
    report = run_benchmark(args.trials, args.valid_per_trial, args.audit_events,args.banking_version)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "workload": report["workload"], "metrics": report["metrics"],
                      "latency": {name: {k:v for k,v in values.items() if k != "raw_ms"} for name,values in report["latency"].items()}}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
