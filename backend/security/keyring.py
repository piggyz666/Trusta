import hashlib
import hmac
import json
import time
from backend.domain import Rejected, canonical


class MessageKeyRing:
    """Persisted messaging epochs. Root-derived keys never enter SQLite or telemetry."""
    MAX_GRACE = 300

    def __init__(self, db, audit, root_key):
        if not isinstance(root_key, bytes) or not root_key:
            raise Rejected("MESSAGE_ROOT_KEY_REQUIRED")
        self.db, self.audit, self.root_key = db, audit, root_key
        fingerprint = hmac.new(root_key, b"trusta-message-root-binding-v2", hashlib.sha256).hexdigest()
        with db.transaction() as conn:
            row = conn.execute("SELECT body FROM message_security_state WHERE id=1").fetchone()
            if row:
                state = json.loads(row["body"])
                if not hmac.compare_digest(state["root_binding"], fingerprint):
                    raise Rejected("MESSAGE_ROOT_KEY_MISMATCH")
            else:
                now = time.time()
                state = {"current_version": 1, "root_binding": fingerprint, "legacy_until": now + self.MAX_GRACE}
                conn.execute("INSERT INTO message_security_state VALUES(1,?)", (canonical(state),))
                conn.execute("INSERT INTO message_key_versions VALUES(1,?)", (canonical({"status": "ACTIVE", "created_at": now, "verify_until": None}),))
                audit.append_in(conn, None, None, "MESSAGE_KEYRING_INITIALIZED", "runtime", {"version": 1})

    def _state(self, conn):
        return json.loads(conn.execute("SELECT body FROM message_security_state WHERE id=1").fetchone()["body"])

    def current_version(self):
        with self.db.transaction() as conn:
            return self._state(conn)["current_version"]

    def signing_material(self, agent):
        with self.db.transaction() as conn:
            version = self._state(conn)["current_version"]
            row = conn.execute("SELECT body FROM message_key_versions WHERE version=?", (version,)).fetchone()
            if not row or json.loads(row["body"])["status"] != "ACTIVE":
                raise Rejected("MESSAGE_KEY_REVOKED")
            key = hmac.new(self.root_key, f"trusta-message-v2:epoch:{version}:agent:{agent}".encode(), hashlib.sha256).digest()
        return version, key

    def key(self, agent, version, signing=False):
        if type(version) is not int or version < 1:
            raise Rejected("MESSAGE_KEY_UNKNOWN")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT body FROM message_key_versions WHERE version=?", (version,)).fetchone()
            body = json.loads(row["body"]) if row else {}
            if not body:
                raise Rejected("MESSAGE_KEY_UNKNOWN")
            if body["status"] == "REVOKED":
                raise Rejected("MESSAGE_KEY_REVOKED")
            if body["status"] != "ACTIVE" and (signing or body["verify_until"] <= time.time()):
                raise Rejected("MESSAGE_KEY_EXPIRED")
        return hmac.new(self.root_key, f"trusta-message-v2:epoch:{version}:agent:{agent}".encode(), hashlib.sha256).digest()

    def legacy_allowed(self):
        with self.db.transaction() as conn:
            return time.time() < self._state(conn)["legacy_until"]

    def rotate(self, grace_seconds=MAX_GRACE):
        if type(grace_seconds) is not int or not 0 <= grace_seconds <= self.MAX_GRACE:
            raise Rejected("MESSAGE_INVALID_GRACE")
        with self.db.transaction() as conn:
            state = self._state(conn)
            current, now = state["current_version"], time.time()
            old = json.loads(conn.execute("SELECT body FROM message_key_versions WHERE version=?", (current,)).fetchone()["body"])
            old.update(status="VERIFY_ONLY", verify_until=now + grace_seconds)
            conn.execute("UPDATE message_key_versions SET body=? WHERE version=?", (canonical(old), current))
            version = current + 1
            conn.execute("INSERT INTO message_key_versions VALUES(?,?)", (version, canonical({"status": "ACTIVE", "created_at": now, "verify_until": None})))
            state["current_version"] = version
            # Rotation never reopens the persisted legacy migration window.
            conn.execute("UPDATE message_security_state SET body=? WHERE id=1", (canonical(state),))
            self.audit.append_in(conn, None, None, "MESSAGE_KEY_ROTATED", "runtime",
                                 {"version": version, "previous_version": current, "grace_seconds": grace_seconds})
        return {"current_version": version, "previous_version": current, "grace_seconds": grace_seconds}

    def revoke(self, version):
        if type(version) is not int or version < 1:
            raise Rejected("MESSAGE_KEY_UNKNOWN")
        with self.db.transaction() as conn:
            if self._state(conn)["current_version"] == version:
                raise Rejected("MESSAGE_CURRENT_KEY_REVOCATION_DENIED")
            row = conn.execute("SELECT body FROM message_key_versions WHERE version=?", (version,)).fetchone()
            if not row:
                raise Rejected("MESSAGE_KEY_UNKNOWN")
            body = json.loads(row["body"])
            body.update(status="REVOKED", verify_until=time.time())
            conn.execute("UPDATE message_key_versions SET body=? WHERE version=?", (canonical(body), version))
            self.audit.append_in(conn, None, None, "MESSAGE_KEY_REVOKED", "runtime", {"version": version})
        return {"revoked_version": version}

    def status(self):
        with self.db.transaction() as conn:
            state = self._state(conn)
            versions = [{"version": row["version"], **json.loads(row["body"])} for row in conn.execute("SELECT version,body FROM message_key_versions ORDER BY version")]
        return {"algorithm": "HMAC-SHA256", "schema_version": 2, "current_version": state["current_version"],
                "legacy_until": state["legacy_until"], "versions": versions, "key_material_exposed": False}
