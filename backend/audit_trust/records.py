import json
from backend.domain import Rejected, digest
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.audit_trust.merkle import verify_inclusion_proof


def verify_record(bundle, trusted_public_key, expected_checkpoint_hash=None):
    """A proof-carried key/status is never a trust anchor. Anchor confirmation is separate."""
    try:
        checkpoint, event = bundle["checkpoint"], bundle["event"]
        if type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
            return False
        if expected_checkpoint_hash is not None and digest(checkpoint) != expected_checkpoint_hash:
            return False
        if (not verify_checkpoint(checkpoint, trusted_public_key)
                or bundle["event_hash"] != event["event_hash"]
                or digest({k: v for k, v in event.items() if k != "event_hash"}) != event["event_hash"]
                or bundle["audit_event_id"] != event["audit_event_id"]
                or type(bundle["event_sequence"]) is not int or type(bundle["leaf_index"]) is not int
                or bundle["event_sequence"] != checkpoint["first_event_sequence"] + bundle["leaf_index"]
                or bundle["merkle_root"] != checkpoint["merkle_root"]):
            return False
        if not verify_inclusion_proof(event["event_hash"], bundle["leaf_index"], checkpoint["leaf_count"],
                                      bundle["inclusion_proof"], checkpoint["merkle_root"]):
            return False
        receipt = bundle.get("receipt")
        if receipt is not None:
            if (event["event_type"] != "RECEIPT_GENERATED" or receipt["audit_ref"] != event["audit_event_id"]
                    or receipt["task_id"] != event["task_id"] or receipt["action_id"] != event["action_id"]
                    or receipt["verification_status"] != "VERIFIED"
                    or digest({k: v for k, v in receipt.items() if k != "audit_ref"}) != event["payload_hash"]):
                return False
        return True
    except (Rejected, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        return False


class RecordProofService:
    def __init__(self, db, checkpoints, owned_task):
        self.db, self.checkpoints, self.owned_task = db, checkpoints, owned_task

    def query(self, user_id, *, audit_event_id=None, receipt_id=None, transaction_id=None):
        identifiers = [value for value in (audit_event_id, receipt_id, transaction_id) if value is not None]
        if len(identifiers) != 1 or not isinstance(identifiers[0], str) or not 0 < len(identifiers[0]) <= 128:
            raise Rejected("INVALID_RECORD_QUERY")
        receipt = None
        if receipt_id or transaction_id:
            if receipt_id:
                row = self.db.one("SELECT body FROM receipts WHERE id=?", (receipt_id,))
            else:
                row = self.db.one("SELECT body FROM receipts WHERE json_extract(body,'$.bank_transaction_id')=?", (transaction_id,))
            if not row:
                raise Rejected("RECEIPT_NOT_FOUND")
            receipt = json.loads(row["body"])
            self.owned_task(receipt["task_id"], user_id)
            audit_event_id = receipt["audit_ref"]
        row = self.db.one("SELECT body FROM audit_events WHERE json_extract(body,'$.audit_event_id')=?", (audit_event_id,))
        if not row:
            raise Rejected("AUDIT_EVENT_NOT_FOUND")
        event = json.loads(row["body"])
        # Global registry/maintenance events are not customer queryable.
        if not event.get("task_id"):
            raise Rejected("AUDIT_ACCESS_DENIED")
        self.owned_task(event["task_id"], user_id)
        bundle = self.checkpoints.generate_inclusion_proof(audit_event_id)
        if receipt:
            bundle["receipt"] = receipt
            bundle["receipt_schema_version"] = receipt.get("schema_version", 1)
        pem, _ = self.checkpoints.public_key(bundle["checkpoint"])
        bundle["record_verified"] = verify_record(bundle, pem)
        if not bundle["record_verified"]:
            raise Rejected("INVALID_RECORD_PROOF")
        return bundle
