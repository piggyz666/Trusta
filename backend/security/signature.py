import hashlib
import hmac
import math
import re
import time
from backend.domain import Rejected, canonical, digest, uid


def finite_number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


class MessageSignature:
    def __init__(self, identities, replay, revision_resolver=None):
        self.identities, self.replay, self.revision_resolver = identities, replay, revision_resolver

    def sign(self, sender, receiver, task_id, step_id, kind, payload_ref, payload, payload_version=None):
        from backend.agents.registry import AgentRegistry
        AgentRegistry().require_active(sender)
        AgentRegistry().require_active(receiver)
        if payload_version is None:
            payload_version = self.revision_resolver(task_id) if self.revision_resolver else 0
        if type(payload_version) is not int or payload_version < 0 or not self.identities.keyring:
            raise Rejected("INVALID_MESSAGE_VERSION")
        now = time.time()
        key_version, signing_key = self.identities.keyring.signing_material(sender)
        message = {"message_id": uid(), "task_id": task_id, "step_id": step_id, "sender_agent": sender,
                   "receiver_agent": receiver, "message_type": kind, "payload_ref": payload_ref,
                   "payload_hash": digest(payload), "timestamp": now, "nonce": uid(), "schema_version": 2,
                   "algorithm": "HMAC-SHA256", "key_id": f"message-v{key_version}",
                   "payload_version": payload_version, "expires_at": now + 300}
        message["signature"] = hmac.new(signing_key, canonical(message).encode(), hashlib.sha256).hexdigest()
        return message

    def _mac(self, message, signing=False):
        body = {k: v for k, v in message.items() if k != "signature"}
        version = int(body["key_id"].removeprefix("message-v")) if body.get("schema_version") == 2 else None
        return hmac.new(self.identities.key(body["sender_agent"], version, signing), canonical(body).encode(), hashlib.sha256).hexdigest()

    def verify(self, message, payload, receiver, consume=True):
        required = {"message_id", "task_id", "step_id", "sender_agent", "receiver_agent", "message_type",
                    "payload_ref", "payload_hash", "timestamp", "nonce", "signature"}
        if not isinstance(message, dict):
            raise Rejected("INVALID_SIGNATURE")
        version = message.get("schema_version")
        if version is not None:
            if type(version) is not int or version != 2:
                raise Rejected("INVALID_MESSAGE_VERSION")
            required |= {"schema_version", "algorithm", "key_id", "payload_version", "expires_at"}
            if (message.get("algorithm") != "HMAC-SHA256"
                    or not isinstance(message.get("key_id"), str) or not re.fullmatch(r"message-v[1-9][0-9]{0,9}", message["key_id"])
                    or type(message.get("payload_version")) is not int or message["payload_version"] < 0
                    or not finite_number(message.get("expires_at"))):
                raise Rejected("INVALID_SIGNATURE")
        elif not self.identities.keyring or not self.identities.keyring.legacy_allowed():
            raise Rejected("LEGACY_MESSAGE_DISABLED")
        if (set(message) != required
                or any(not isinstance(message[k], str) or not 0 < len(message[k]) <= 512 for k in required - {"timestamp", "schema_version", "payload_version", "expires_at"})
                or not finite_number(message["timestamp"])
                or any(not re.fullmatch(r"[0-9a-f]{64}", message[k]) for k in ("signature", "payload_hash"))):
            raise Rejected("INVALID_SIGNATURE")
        if not hmac.compare_digest(self._mac(message), str(message["signature"])):
            raise Rejected("INVALID_SIGNATURE")
        if message["receiver_agent"] != receiver:
            raise Rejected("INVALID_RECEIVER")
        from backend.agents.registry import AgentRegistry
        AgentRegistry().require_active(receiver)
        if digest(payload) != message["payload_hash"]:
            raise Rejected("PAYLOAD_TAMPERED")
        if abs(time.time() - message["timestamp"]) > 300:
            raise Rejected("MESSAGE_EXPIRED")
        if version == 2 and message["timestamp"] > time.time() + 30:
            raise Rejected("MESSAGE_EXPIRED")
        if version == 2 and (not message["timestamp"] < message["expires_at"] <= message["timestamp"] + 300 or message["expires_at"] <= time.time()):
            raise Rejected("MESSAGE_EXPIRED")
        if consume:
            self.replay.consume(message["sender_agent"], message["nonce"], message["timestamp"])
