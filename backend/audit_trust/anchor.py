"""Persistent asynchronous audit delivery, completely outside the financial request path."""
import json
import time
from typing import Protocol
from backend.domain import Rejected, canonical, digest
from backend.security.extensions import AnchorCommitment
from backend.security.crypto import HEX


class AuditAnchor(Protocol):
    name: str
    def submit(self, commitment: AnchorCommitment, checkpoint_hash: str) -> str: ...
    def inspect(self, anchor_ref: str) -> dict: ...


class LocalDevelopmentAnchor:
    name = "LOCAL_DEVELOPMENT"
    def __init__(self, db):
        self.db = db

    def submit(self, commitment, checkpoint_hash):
        if not isinstance(commitment, AnchorCommitment) or not isinstance(checkpoint_hash, str) or not HEX.fullmatch(checkpoint_hash):
            raise Rejected("INVALID_ANCHOR_COMMITMENT")
        reference = "local:" + digest({"root": commitment.digest, "checkpoint_hash": checkpoint_hash})
        with self.db.transaction() as conn:
            prior = conn.execute("SELECT * FROM local_audit_anchors WHERE root=?", (commitment.digest,)).fetchone()
            if prior:
                if prior["checkpoint_hash"] != checkpoint_hash or prior["anchor_ref"] != reference:
                    raise Rejected("ANCHOR_ROOT_MISMATCH")
                return reference
            record = {"schema_version": 1, "root": commitment.digest, "checkpoint_hash": checkpoint_hash,
                      "anchor_ref": reference, "status": "CONFIRMED", "confirmed_at": int(time.time() * 1000),
                      "adapter": self.name, "is_blockchain": False, "externally_verified": False}
            conn.execute("INSERT INTO local_audit_anchors VALUES(?,?,?,?)", (reference, commitment.digest, checkpoint_hash, canonical(record)))
        return reference

    def inspect(self, anchor_ref):
        row = self.db.one("SELECT * FROM local_audit_anchors WHERE anchor_ref=?", (anchor_ref,))
        if not row:
            raise Rejected("ANCHOR_NOT_FOUND")
        record = json.loads(row["body"])
        if record["root"] != row["root"] or record["checkpoint_hash"] != row["checkpoint_hash"] or record["anchor_ref"] != anchor_ref:
            raise Rejected("ANCHOR_ROOT_MISMATCH")
        return record


class UnconfiguredBlockchainAnchor:
    """Adapter boundary only. No RPC/contract/wallet integration, never fakes chain success."""
    name = "BLOCKCHAIN_UNCONFIGURED"
    def submit(self, commitment, checkpoint_hash):
        raise Rejected("BLOCKCHAIN_ADAPTER_NOT_IMPLEMENTED")
    def inspect(self, anchor_ref):
        raise Rejected("BLOCKCHAIN_ADAPTER_NOT_IMPLEMENTED")


def configured_anchor(db,config):
    """Shared server/worker selection; an enabled external profile never falls back locally."""
    import os
    if not config.blockchain_anchor_enabled:
        return LocalDevelopmentAnchor(db)
    if not os.getenv('ANCHOR_RPC_URL') or not os.getenv('ANCHOR_CONTRACT_ADDRESS'):
        return UnconfiguredBlockchainAnchor()
    from backend.audit_trust.external_anchor import EthereumRPC,ExternalBlockchainAnchor
    return ExternalBlockchainAnchor(db,EthereumRPC(os.environ['ANCHOR_RPC_URL']),os.environ['ANCHOR_CONTRACT_ADDRESS'],
        confirmations=int(os.getenv('ANCHOR_CONFIRMATIONS','3')),finalized=os.getenv('ANCHOR_REQUIRE_FINALIZED','false')=='true')


class AnchorWorker:
    def __init__(self, db, checkpoints, adapter):
        self.db, self.checkpoints, self.adapter = db, checkpoints, adapter
        from types import SimpleNamespace
        from backend.audit import AuditEngine
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(SimpleNamespace(db=db,audit=AuditEngine(db)))

    def _save(self, job):
        job["updated_at"] = int(time.time() * 1000)
        with self.db.transaction() as conn:
            conn.execute("UPDATE audit_anchor_jobs SET status=?,body=? WHERE checkpoint_id=?",
                         (job["status"], canonical(job), job["checkpoint_id"]))

    def status(self, checkpoint_id):
        row = self.db.one("SELECT status,body FROM audit_anchor_jobs WHERE checkpoint_id=?", (checkpoint_id,))
        if not row:
            raise Rejected("ANCHOR_JOB_NOT_FOUND")
        job = json.loads(row["body"])
        if row["status"] != job["status"]:
            raise Rejected("ANCHOR_STATE_MISMATCH")
        return job

    def verify_anchor(self, checkpoint_id):
        try:
            job = self.status(checkpoint_id)
            row = self.db.one("SELECT body FROM audit_checkpoints WHERE id=?", (checkpoint_id,))
            checkpoint = json.loads(row["body"]) if row else {}
            if job["status"] != "CONFIRMED" or job["adapter"] != self.adapter.name or not self.checkpoints.verify_checkpoint(checkpoint):
                return False
            observation = self.adapter.inspect(job["anchor_ref"])
            return (observation["status"] == "CONFIRMED" and observation["root"] == checkpoint["merkle_root"] == job["root"]
                    and observation["checkpoint_hash"] == digest(checkpoint) == job["checkpoint_hash"])
        except (Rejected, KeyError, TypeError, ValueError):
            return False

    def process_one(self, checkpoint_id):
        with self.claims.hold('anchor-job:'+checkpoint_id,recover_metadata=True):
            return self._process_one(checkpoint_id)

    def _process_one(self, checkpoint_id):
        job = self.status(checkpoint_id)
        if job["status"] == "CONFIRMED":
            if not self.verify_anchor(checkpoint_id):
                raise Rejected("ANCHOR_ROOT_MISMATCH")
            return job
        try:
            row = self.db.one("SELECT body FROM audit_checkpoints WHERE id=?", (checkpoint_id,))
            checkpoint = json.loads(row["body"]) if row else {}
            if (job["root"] != checkpoint.get("merkle_root") or job["checkpoint_hash"] != digest(checkpoint)
                    or not self.checkpoints.verify_checkpoint(checkpoint)):
                raise Rejected("ANCHOR_ROOT_MISMATCH")
            if job["status"] == "FAILED":
                return job  # Retry must be an explicit local operator action.
            if job["adapter"] not in {None, self.adapter.name}:
                raise Rejected("ANCHOR_ADAPTER_MISMATCH")
            job.update(adapter=self.adapter.name, attempts=job["attempts"] + 1, error=None)
            if not job["anchor_ref"]:
                job["anchor_ref"] = self.adapter.submit(AnchorCommitment(job["root"]), job["checkpoint_hash"])
                job["status"] = "SUBMITTED"
                self._save(job)
            observation = self.adapter.inspect(job["anchor_ref"])
            if observation.get("root") != job["root"] or observation.get("checkpoint_hash") != job["checkpoint_hash"]:
                raise Rejected("ANCHOR_ROOT_MISMATCH")
            if observation.get("status") not in {"SUBMITTED", "CONFIRMED", "FAILED"}:
                raise Rejected("INVALID_ANCHOR_OBSERVATION")
            job["status"] = observation["status"]
            job["is_blockchain"] = observation.get("is_blockchain", False)
            job["externally_verified"] = observation.get("externally_verified", False)
            self._save(job)
        except Exception as exc:
            job.update(status="FAILED", error=exc.code if isinstance(exc, Rejected) else "ANCHOR_ADAPTER_UNAVAILABLE")
            self._save(job)
        return job

    def retry(self, checkpoint_id):
        with self.claims.hold('anchor-job:'+checkpoint_id,recover_metadata=True):
            return self._retry(checkpoint_id)

    def _retry(self, checkpoint_id):
        job = self.status(checkpoint_id)
        if job["status"] != "FAILED":
            raise Rejected("ANCHOR_NOT_FAILED")
        job.update(status="PENDING", error=None)
        self._save(job)

    def run_once(self, max_events=256, max_batches=4):
        if type(max_batches) is not int or not 1 <= max_batches <= 16:
            raise Rejected("INVALID_WORKER_BATCH_COUNT")
        generated = []
        for _ in range(max_batches):
            checkpoint = self.checkpoints.generate_checkpoint(max_events)
            if checkpoint is None:
                break
            generated.append(checkpoint["checkpoint_id"])
        rows = self.db.rows("SELECT checkpoint_id FROM audit_anchor_jobs WHERE status IN ('PENDING','SUBMITTED') ORDER BY rowid LIMIT ?", (max_batches,))
        jobs=[]
        for row in rows:
            try:jobs.append(self.process_one(row['checkpoint_id']))
            except Rejected as exc:
                if exc.code!='GRAPH_WORKER_BUSY':raise
        return {"generated_checkpoints": generated, "jobs": jobs, "adapter": self.adapter.name}
