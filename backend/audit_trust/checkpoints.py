import json
import time
from backend.domain import Rejected, canonical, digest, uid
from backend.security.crypto import ALGORITHM, PROFILE, HEX, fingerprint, verify_object, sign_object
from backend.security.crypto_registry import AUDIT_SIGNER
from backend.audit_trust import merkle

CHECKPOINT_DOMAIN = "TRUSTA-AUDIT-CHECKPOINT-V1"
CHECKPOINT_FIELDS = {"schema_version", "canonicalization", "tree_algorithm", "checkpoint_id", "first_event_sequence",
                     "last_event_sequence", "leaf_count", "merkle_root", "created_at", "previous_checkpoint_hash",
                     "signer_identity", "signer_key_id", "public_key_fingerprint", "signature_algorithm", "checkpoint_signature"}


def verify_checkpoint(checkpoint, trusted_public_key):
    """Offline verification requires an externally trusted/pinned key, never a proof-supplied key."""
    try:
        if (not isinstance(checkpoint, dict) or set(checkpoint) != CHECKPOINT_FIELDS
                or type(checkpoint["schema_version"]) is not int or checkpoint["schema_version"] != 1
                or checkpoint["canonicalization"] != PROFILE or checkpoint["tree_algorithm"] != "RFC6962-SHA256-event-hash-v1"
                or checkpoint["signature_algorithm"] != ALGORITHM
                or any(type(checkpoint[k]) is not int or checkpoint[k] < 0 for k in
                       {"first_event_sequence", "last_event_sequence", "leaf_count", "created_at"})
                or not 1 <= checkpoint["leaf_count"] <= 4096
                or checkpoint["first_event_sequence"] < 1
                or checkpoint["last_event_sequence"] - checkpoint["first_event_sequence"] + 1 != checkpoint["leaf_count"]
                or any(not isinstance(checkpoint[k], str) or not HEX.fullmatch(checkpoint[k])
                       for k in {"merkle_root", "previous_checkpoint_hash", "public_key_fingerprint"})
                or fingerprint(trusted_public_key) != checkpoint["public_key_fingerprint"]):
            return False
        verify_object(trusted_public_key, CHECKPOINT_DOMAIN,
                      {k: v for k, v in checkpoint.items() if k != "checkpoint_signature"}, checkpoint["checkpoint_signature"])
        return True
    except (Rejected, TypeError, ValueError, KeyError):
        return False


class MerkleAudit:
    def __init__(self, db, audit, identities, lock):
        self.db, self.audit, self.identities, self.lock = db, audit, identities, lock
        self.verify_storage()

    def public_key(self, checkpoint):
        with self.db.transaction() as conn:
            identity, key = self.identities.resolve_in(AUDIT_SIGNER, checkpoint["signer_key_id"], conn, historical=True)
        if checkpoint["signer_identity"] != identity["identity_uri"]:
            raise Rejected("INVALID_CHECKPOINT_IDENTITY")
        return key["public_key"], key["status"]

    def verify_checkpoint(self, checkpoint):
        try:
            pem, _ = self.public_key(checkpoint)
            return verify_checkpoint(checkpoint, pem)
        except (Rejected, KeyError, TypeError):
            return False

    def generate_checkpoint(self, max_events=256):
        if type(max_events) is not int or not 1 <= max_events <= 4096:
            raise Rejected("INVALID_CHECKPOINT_BATCH")
        with self.lock, self.db.transaction() as conn:
            self._verify_storage_in(conn)
            # Freeze the actual SQLite snapshot. Never checkpoint a corrupted chain.
            rows = conn.execute("SELECT seq,body FROM audit_events ORDER BY seq").fetchall()
            previous = "0" * 64
            for row in rows:
                event = json.loads(row["body"])
                if event["previous_hash"] != previous or digest({k: v for k, v in event.items() if k != "event_hash"}) != event["event_hash"]:
                    raise Rejected("AUDIT_CHAIN_INVALID")
                previous = event["event_hash"]
            prior = conn.execute("SELECT body,last_seq FROM audit_checkpoints ORDER BY last_seq DESC LIMIT 1").fetchone()
            last = prior["last_seq"] if prior else 0
            batch = [row for row in rows if row["seq"] > last][:max_events]
            if not batch:
                return None
            if batch[0]["seq"] != last + 1 or batch[-1]["seq"] != last + len(batch):
                raise Rejected("AUDIT_SEQUENCE_GAP")
            events = [json.loads(row["body"]) for row in batch]
            identity = self.identities._identity(AUDIT_SIGNER, conn)
            identity, key = self.identities.resolve_in(AUDIT_SIGNER, identity["key_id"], conn)
            checkpoint = {"schema_version": 1, "canonicalization": PROFILE,
                          "tree_algorithm": "RFC6962-SHA256-event-hash-v1", "checkpoint_id": uid(),
                          "first_event_sequence": batch[0]["seq"], "last_event_sequence": batch[-1]["seq"],
                          "leaf_count": len(batch), "merkle_root": merkle.root([event["event_hash"] for event in events]),
                          "created_at": int(time.time() * 1000), "previous_checkpoint_hash": digest(json.loads(prior["body"])) if prior else "0" * 64,
                          "signer_identity": identity["identity_uri"], "signer_key_id": key["key_id"],
                          "public_key_fingerprint": key["public_key_fingerprint"], "signature_algorithm": ALGORITHM}
            checkpoint["checkpoint_signature"] = sign_object(self.identities.store.load(key), CHECKPOINT_DOMAIN, checkpoint)
            conn.execute("INSERT INTO audit_checkpoints VALUES(?,?,?,?)", (checkpoint["checkpoint_id"], batch[0]["seq"], batch[-1]["seq"], canonical(checkpoint)))
            for i, (row, event) in enumerate(zip(batch, events)):
                conn.execute("INSERT INTO audit_checkpoint_leaves VALUES(?,?,?,?,?)", (event["audit_event_id"], checkpoint["checkpoint_id"], row["seq"], i, event["event_hash"]))
            # Mutable delivery state is outside the signed, immutable checkpoint body.
            job = {"schema_version": 1, "checkpoint_id": checkpoint["checkpoint_id"], "root": checkpoint["merkle_root"],
                   "checkpoint_hash": digest(checkpoint), "status": "PENDING", "anchor_ref": None,
                   "adapter": None, "attempts": 0, "updated_at": int(time.time() * 1000), "error": None}
            conn.execute("INSERT INTO audit_anchor_jobs VALUES(?,'PENDING',?)", (checkpoint["checkpoint_id"], canonical(job)))
        return checkpoint

    def generate_inclusion_proof(self, event_id):
        with self.lock, self.db.transaction() as conn:
            entry = conn.execute("SELECT * FROM audit_checkpoint_leaves WHERE event_id=?", (event_id,)).fetchone()
            if not entry:
                raise Rejected("AUDIT_CHECKPOINT_PENDING")
            checkpoint = json.loads(conn.execute("SELECT body FROM audit_checkpoints WHERE id=?", (entry["checkpoint_id"],)).fetchone()["body"])
            rows = conn.execute("SELECT * FROM audit_checkpoint_leaves WHERE checkpoint_id=? ORDER BY leaf_index", (entry["checkpoint_id"],)).fetchall()
            if len(rows) != checkpoint["leaf_count"] or merkle.root([row["event_hash"] for row in rows]) != checkpoint["merkle_root"]:
                raise Rejected("CHECKPOINT_STORAGE_TAMPERED")
            event_row = conn.execute("SELECT body FROM audit_events WHERE seq=?", (entry["sequence"],)).fetchone()
            event = json.loads(event_row["body"]) if event_row else {}
            if event.get("event_hash") != entry["event_hash"] or event.get("audit_event_id") != event_id or digest({k: v for k, v in event.items() if k != "event_hash"}) != entry["event_hash"]:
                raise Rejected("AUDIT_EVENT_TAMPERED")
            identity, key = self.identities.resolve_in(AUDIT_SIGNER, checkpoint["signer_key_id"], conn, historical=True)
            if checkpoint["signer_identity"] != identity["identity_uri"] or not verify_checkpoint(checkpoint, key["public_key"]):
                raise Rejected("INVALID_CHECKPOINT_SIGNATURE")
            job = conn.execute("SELECT body FROM audit_anchor_jobs WHERE checkpoint_id=?", (entry["checkpoint_id"],)).fetchone()
            anchor = json.loads(job["body"]) if job else None
            return {"schema_version": 1, "audit_event_id": event_id, "event": event, "event_hash": entry["event_hash"],
                    "event_sequence": entry["sequence"], "leaf_index": entry["leaf_index"],
                    "inclusion_proof": merkle.proof([row["event_hash"] for row in rows], entry["leaf_index"]),
                    "merkle_root": checkpoint["merkle_root"], "checkpoint": checkpoint,
                    "signature_status": "VALID", "signer_key_status": key["status"],
                    "signer_public_key": key["public_key"], "anchor": anchor, "anchor_status": anchor["status"] if anchor else "PENDING"}

    def verify_storage(self):
        with self.lock, self.db.transaction() as conn:
            return self._verify_storage_in(conn)

    def _verify_storage_in(self, conn):
        previous_hash, last = "0" * 64, 0
        for row in conn.execute("SELECT body,first_seq,last_seq FROM audit_checkpoints ORDER BY last_seq").fetchall():
            checkpoint = json.loads(row["body"])
            leaves = conn.execute("SELECT * FROM audit_checkpoint_leaves WHERE checkpoint_id=? ORDER BY leaf_index", (checkpoint["checkpoint_id"],)).fetchall()
            identity, key = self.identities.resolve_in(AUDIT_SIGNER, checkpoint["signer_key_id"], conn, historical=True)
            if (row["first_seq"] != last + 1 or checkpoint["first_event_sequence"] != row["first_seq"]
                    or checkpoint["last_event_sequence"] != row["last_seq"] or checkpoint["previous_checkpoint_hash"] != previous_hash
                    or len(leaves) != checkpoint["leaf_count"] or checkpoint["signer_identity"] != identity["identity_uri"]
                    or not verify_checkpoint(checkpoint, key["public_key"])
                    or merkle.root([item["event_hash"] for item in leaves]) != checkpoint["merkle_root"]):
                raise Rejected("CHECKPOINT_STORAGE_TAMPERED")
            for index, item in enumerate(leaves):
                event_row = conn.execute("SELECT body FROM audit_events WHERE seq=?", (item["sequence"],)).fetchone()
                event = json.loads(event_row["body"]) if event_row else {}
                if (item["leaf_index"] != index or item["sequence"] != row["first_seq"] + index
                        or event.get("audit_event_id") != item["event_id"] or event.get("event_hash") != item["event_hash"]
                        or digest({k: v for k, v in event.items() if k != "event_hash"}) != item["event_hash"]):
                    raise Rejected("AUDIT_EVENT_TAMPERED")
            last, previous_hash = row["last_seq"], digest(checkpoint)
        return True
