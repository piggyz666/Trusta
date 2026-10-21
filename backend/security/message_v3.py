import time
from backend.domain import Rejected, digest, uid
from backend.security.crypto import ALGORITHM, PROFILE, HEX, verify_object

MESSAGE_DOMAIN = "TRUSTA-AGENT-MESSAGE-V3"
KINDS = {"REQUEST", "PLAN", "ACTION_PROPOSED", "READ_ONLY_REPORT"}
FIELDS = {"schema_version", "canonicalization", "message_id", "task_id", "step_id", "sender_agent", "receiver_agent",
          "sender_key_id", "sender_identity", "message_type", "payload_ref", "payload_hash", "payload_version",
          "capability_snapshot_hash", "issued_at", "expires_at", "nonce", "signature_algorithm", "signature"}


class TrustedMessageV3:
    def __init__(self, identities, replay, revision_resolver, context_validator):
        self.identities, self.replay, self.revision_resolver = identities, replay, revision_resolver
        self.context_validator = context_validator

    def sign(self, sender, receiver, task_id, step_id, kind, payload_ref, payload, payload_version=None):
        self.identities.require_active(receiver)
        if kind not in KINDS:
            raise Rejected("UNPROTECTED_MESSAGE_TYPE")
        if payload_version is None:
            payload_version = self.revision_resolver(task_id)
        if type(payload_version) is not int or payload_version < 0:
            raise Rejected("INVALID_MESSAGE_VERSION")
        now = int(time.time() * 1000)
        def body(identity):
            return {"schema_version": 3, "canonicalization": PROFILE, "message_id": uid(), "task_id": task_id,
                    "step_id": step_id, "sender_agent": sender, "receiver_agent": receiver,
                    "sender_key_id": identity["key_id"], "sender_identity": identity["identity_uri"],
                    "message_type": kind, "payload_ref": payload_ref, "payload_hash": digest(payload),
                    "payload_version": payload_version, "capability_snapshot_hash": identity["capability_hash"],
                    "issued_at": now, "expires_at": now + 300000, "nonce": uid(), "signature_algorithm": ALGORITHM}
        message, signature = self.identities.sign(sender, MESSAGE_DOMAIN, body)
        return message | {"signature": signature}

    def verify_in(self, message, payload, receiver, conn):
        if not isinstance(message, dict) or type(message.get("schema_version")) is not int or message.get("schema_version") != 3:
            raise Rejected("MESSAGE_DOWNGRADE_DENIED")
        if set(message) != FIELDS or message.get("canonicalization") != PROFILE or message.get("signature_algorithm") != ALGORITHM:
            raise Rejected("INVALID_MESSAGE_ALGORITHM")
        integers = {"schema_version", "payload_version", "issued_at", "expires_at"}
        if (any(type(message[k]) is not int or not 0 <= message[k] <= 2**63 - 1 for k in integers)
                or any(not isinstance(message[k], str) or not 0 < len(message[k]) <= 512 for k in FIELDS - integers)
                or any(not HEX.fullmatch(message[k]) for k in {"payload_hash", "capability_snapshot_hash"})
                or message["message_type"] not in KINDS):
            raise Rejected("INVALID_MESSAGE_ENVELOPE")
        identity, key = self.identities.resolve_in(message["sender_agent"], message["sender_key_id"], conn)
        if message["sender_identity"] != identity["identity_uri"]:
            raise Rejected("FORGED_AGENT_IDENTITY")
        verify_object(key["public_key"], MESSAGE_DOMAIN, {k: v for k, v in message.items() if k != "signature"}, message["signature"])
        if message["capability_snapshot_hash"] != identity["capability_hash"]:
            raise Rejected("CAPABILITY_SNAPSHOT_MISMATCH")
        if message["receiver_agent"] != receiver:
            raise Rejected("INVALID_RECEIVER")
        target = self.identities._identity(receiver, conn)
        self.identities.resolve_in(receiver, target["key_id"], conn)
        try:
            matches = digest(payload) == message["payload_hash"]
        except (ValueError, TypeError, UnicodeError):
            matches = False
        if not matches:
            raise Rejected("PAYLOAD_TAMPERED")
        now = int(time.time() * 1000)
        if not now - 300000 <= message["issued_at"] <= now + 30000 or not message["issued_at"] < message["expires_at"] <= message["issued_at"] + 300000 or message["expires_at"] <= now:
            raise Rejected("MESSAGE_EXPIRED")

    def verify(self, message, payload, receiver, consume=True):
        with self.identities.lock, self.identities.db.transaction() as conn:
            self.verify_in(message, payload, receiver, conn)
            self.context_validator(message, conn)
            if consume:
                self.replay.consume_in(conn, message["sender_agent"], message["nonce"], message["issued_at"] / 1000)
