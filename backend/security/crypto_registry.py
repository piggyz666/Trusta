"""Local cryptographic identity registry. URNs here are explicitly NOT DIDs."""
import hashlib
import hmac
import json
from pathlib import Path
import time
from backend.domain import Rejected, canonical, uid
from backend.security.crypto import ALGORITHM, LocalKeyStore, fingerprint, sign_object

AUDIT_SIGNER = "audit-checkpoint"


class CryptoIdentityRegistry:
    MAX_GRACE = 300

    def __init__(self, db, audit, capabilities, config, lock, allow_capability_sync=False):
        self.db, self.audit, self.capabilities, self.lock = db, audit, capabilities, lock
        self.seal_key = hmac.new(config.signing_key, b"trusta-registry-seal-v1", hashlib.sha256).digest()
        directory = config.crypto_key_dir or str(Path(config.db_path).resolve().with_suffix(".keys"))
        self.store = LocalKeyStore(directory, config.signing_key)
        subjects = capabilities.agents.active() + [AUDIT_SIGNER]
        with lock, db.transaction() as conn:
            state = conn.execute("SELECT body FROM crypto_trust_state WHERE id=1").fetchone()
            if state:
                self.state = self._decode(state)
                if config.banking_version < self.state.get("minimum_banking_version", 4):
                    raise Rejected("BANKING_DOWNGRADE_DENIED")
                rows = conn.execute("SELECT agent_id FROM crypto_identities").fetchall()
                if config.banking_version == 5 and self.state.get("minimum_banking_version", 4) == 4:
                    self._upgrade_banking(conn, subjects, audit)
                    rows = conn.execute("SELECT agent_id FROM crypto_identities").fetchall()
                if {row["agent_id"] for row in rows} != set(subjects):
                    raise Rejected("IDENTITY_REGISTRY_INCOMPLETE")
            else:
                self.state = {"minimum_message_version": 3, "minimum_banking_version": config.banking_version,
                              "deployment_id": uid(), "created_at": self.now()}
                conn.execute("INSERT INTO crypto_trust_state VALUES(1,?)", (self._encode(self.state),))
                for subject in subjects:
                    now = self.now()
                    key = self._new_key(subject, 1, now)
                    conn.execute("INSERT INTO crypto_keys VALUES(?,?,?,?)", (key["key_id"], subject, 1, self._encode(key)))
                    record = {"agent_id": subject, "identity_uri": f"urn:trusta:{self.state['deployment_id']}:{subject}",
                              **{k: key[k] for k in ("public_key", "public_key_fingerprint", "key_id", "key_version")},
                              "capability_hash": self._capability(subject), "agent_version": "1",
                              "status": "ACTIVE", "created_at": now, "updated_at": now, "rotated_at": None}
                    conn.execute("INSERT INTO crypto_identities VALUES(?,?)", (subject, self._encode(record)))
                audit.append_in(conn, None, None, "CRYPTO_IDENTITIES_INITIALIZED", "identity_registry", {"algorithm": ALGORITHM})
            for subject in subjects:
                identity = self._identity(subject, conn)
                key = self._key(identity["key_id"], conn)
                self.store.load(key)
                if not allow_capability_sync and identity["capability_hash"] != self._capability(subject):
                    raise Rejected("CAPABILITY_COMMITMENT_STALE")

    def _upgrade_banking(self, conn, subjects, audit):
        from backend.security.registry import CapabilityRegistry
        old = CapabilityRegistry(4)
        expected = set(old.agents.active()) | {AUDIT_SIGNER}
        existing = {row["agent_id"] for row in conn.execute("SELECT agent_id FROM crypto_identities")}
        if existing != expected or set(subjects) != expected | {"service"}:
            raise Rejected("IDENTITY_REGISTRY_INCOMPLETE")
        for subject in existing:
            identity = self._identity(subject, conn)
            key = self._key(identity["key_id"], conn)
            self.store.load(key)
            old_hash = old.commitment(subject) if subject != AUDIT_SIGNER else self._capability(subject)
            if identity["capability_hash"] != old_hash:
                raise Rejected("CAPABILITY_COMMITMENT_STALE")
            identity.update(capability_hash=self._capability(subject), updated_at=self.now(), agent_version=str(int(identity["agent_version"])+1))
            conn.execute("UPDATE crypto_identities SET body=? WHERE agent_id=?", (self._encode(identity), subject))
        now = self.now()
        key = self._new_key("service", 1, now)
        conn.execute("INSERT INTO crypto_keys VALUES(?,?,?,?)", (key["key_id"], "service", 1, self._encode(key)))
        identity = {"agent_id": "service", "identity_uri": f"urn:trusta:{self.state['deployment_id']}:service",
                    **{k:key[k] for k in ("public_key", "public_key_fingerprint", "key_id", "key_version")},
                    "capability_hash": self._capability("service"), "agent_version": "1", "status": "ACTIVE",
                    "created_at": now, "updated_at": now, "rotated_at": None}
        conn.execute("INSERT INTO crypto_identities VALUES('service',?)", (self._encode(identity),))
        self.state["minimum_banking_version"] = 5
        conn.execute("UPDATE crypto_trust_state SET body=? WHERE id=1", (self._encode(self.state),))
        audit.append_in(conn, None, None, "BANKING_PROFILE_UPGRADED", "identity_registry", {"from":4,"to":5,"agent":"service"})

    @staticmethod
    def now():
        return int(time.time() * 1000)

    def _encode(self, body):
        seal = hmac.new(self.seal_key, canonical(body).encode(), hashlib.sha256).hexdigest()
        return canonical(body | {"registry_seal": seal})

    def _decode(self, row):
        try:
            body = json.loads(row["body"])
            seal = body.pop("registry_seal")
            expected = hmac.new(self.seal_key, canonical(body).encode(), hashlib.sha256).hexdigest()
            if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
                raise ValueError()
            return body
        except (KeyError, ValueError, TypeError):
            raise Rejected("IDENTITY_REGISTRY_TAMPERED") from None

    def _capability(self, subject):
        if subject == AUDIT_SIGNER:
            return hashlib.sha256(b"trusta-system:audit-checkpoint:sign-only:v1").hexdigest()
        return self.capabilities.commitment(subject)

    def _new_key(self, subject, version, now):
        key_id = "key-" + uid()
        pem = self.store.generate(key_id)
        return {"agent_id": subject, "key_id": key_id, "key_version": version, "algorithm": ALGORITHM,
                "public_key": pem, "public_key_fingerprint": fingerprint(pem), "status": "ACTIVE",
                "created_at": now, "verify_until": None, "revoked_at": None}

    def _identity(self, agent, conn):
        row = conn.execute("SELECT body FROM crypto_identities WHERE agent_id=?", (agent,)).fetchone()
        if not row:
            raise Rejected("UNKNOWN_CRYPTO_IDENTITY")
        return self._decode(row)

    def _key(self, key_id, conn):
        row = conn.execute("SELECT body FROM crypto_keys WHERE key_id=?", (key_id,)).fetchone()
        if not row:
            raise Rejected("MESSAGE_KEY_UNKNOWN")
        return self._decode(row)

    def resolve_in(self, agent, key_id, conn, historical=False):
        identity, key = self._identity(agent, conn), self._key(key_id, conn)
        if key["agent_id"] != agent or key["algorithm"] != ALGORITHM:
            raise Rejected("IDENTITY_KEY_MISMATCH")
        if not historical:
            if agent != AUDIT_SIGNER:
                self.capabilities.agents.require_active(agent)
            if identity["status"] != "ACTIVE":
                raise Rejected("AGENT_" + identity["status"])
            if identity["capability_hash"] != self._capability(agent):
                raise Rejected("CAPABILITY_COMMITMENT_STALE")
            if key["status"] == "REVOKED":
                raise Rejected("MESSAGE_KEY_REVOKED")
            if key["status"] != "ACTIVE" and (key["verify_until"] is None or self.now() >= key["verify_until"]):
                raise Rejected("MESSAGE_KEY_EXPIRED")
        return identity, key

    def require_active(self, agent):
        with self.lock, self.db.transaction() as conn:
            identity = self._identity(agent, conn)
            self.resolve_in(agent, identity["key_id"], conn)

    def resolve(self, identifier):
        with self.lock, self.db.transaction() as conn:
            for row in conn.execute("SELECT body FROM crypto_identities"):
                identity = self._decode(row)
                if identifier in {identity["agent_id"], identity["identity_uri"]}:
                    return identity
        raise Rejected("UNKNOWN_CRYPTO_IDENTITY")

    def sign(self, agent, domain, body_factory):
        with self.lock, self.db.transaction() as conn:
            identity = self._identity(agent, conn)
            identity, key = self.resolve_in(agent, identity["key_id"], conn)
            body = body_factory(identity)
            return body, sign_object(self.store.load(key), domain, body)

    def rotate(self, agent, grace_seconds=MAX_GRACE):
        if type(grace_seconds) is not int or not 0 <= grace_seconds <= self.MAX_GRACE:
            raise Rejected("MESSAGE_INVALID_GRACE")
        with self.lock, self.db.transaction() as conn:
            identity = self._identity(agent, conn)
            self.resolve_in(agent, identity["key_id"], conn)
            now = self.now()
            old = self._key(identity["key_id"], conn)
            old.update(status="VERIFY_ONLY", verify_until=now + grace_seconds * 1000)
            conn.execute("UPDATE crypto_keys SET body=? WHERE key_id=?", (self._encode(old), old["key_id"]))
            new = self._new_key(agent, old["key_version"] + 1, now)
            conn.execute("INSERT INTO crypto_keys VALUES(?,?,?,?)", (new["key_id"], agent, new["key_version"], self._encode(new)))
            identity.update({k: new[k] for k in ("public_key", "public_key_fingerprint", "key_id", "key_version")})
            identity.update(updated_at=now, rotated_at=now)
            conn.execute("UPDATE crypto_identities SET body=? WHERE agent_id=?", (self._encode(identity), agent))
            self.audit.append_in(conn, None, None, "CRYPTO_KEY_ROTATED", "identity_registry",
                                 {"agent_id": agent, "key_id": new["key_id"], "previous_key_id": old["key_id"], "grace_seconds": grace_seconds})
        return identity

    def revoke_key(self, key_id):
        with self.lock, self.db.transaction() as conn:
            key = self._key(key_id, conn)
            if self._identity(key["agent_id"], conn)["key_id"] == key_id:
                raise Rejected("MESSAGE_CURRENT_KEY_REVOCATION_DENIED")
            key.update(status="REVOKED", verify_until=self.now(), revoked_at=self.now())
            conn.execute("UPDATE crypto_keys SET body=? WHERE key_id=?", (self._encode(key), key_id))
            self.audit.append_in(conn, None, None, "CRYPTO_KEY_REVOKED", "identity_registry", {"key_id": key_id})

    def set_status(self, agent, status):
        if status not in {"ACTIVE", "SUSPENDED", "REVOKED"} or agent == AUDIT_SIGNER:
            raise Rejected("INVALID_IDENTITY_STATUS")
        with self.lock, self.db.transaction() as conn:
            identity = self._identity(agent, conn)
            if identity["status"] == "REVOKED":
                raise Rejected("AGENT_REVOKED")
            identity.update(status=status, updated_at=self.now())
            conn.execute("UPDATE crypto_identities SET body=? WHERE agent_id=?", (self._encode(identity), agent))
            self.audit.append_in(conn, None, None, "CRYPTO_IDENTITY_STATUS", "identity_registry", {"agent_id": agent, "status": status})
        return identity

    def sync_capabilities(self, agent):
        # Local administrator operation; grants themselves still come from reviewed code.
        self.capabilities.validate()
        with self.lock, self.db.transaction() as conn:
            identity = self._identity(agent, conn)
            old = identity["capability_hash"]
            identity.update(capability_hash=self._capability(agent), updated_at=self.now())
            identity["agent_version"] = str(int(identity["agent_version"]) + 1)
            conn.execute("UPDATE crypto_identities SET body=? WHERE agent_id=?", (self._encode(identity), agent))
            self.audit.append_in(conn, None, None, "CRYPTO_CAPABILITY_UPDATED", "identity_registry",
                                 {"agent_id": agent, "previous_hash": old, "current_hash": identity["capability_hash"]})
        return identity

    def snapshot(self):
        with self.lock, self.db.transaction() as conn:
            identities = [self._decode(row) for row in conn.execute("SELECT body FROM crypto_identities ORDER BY agent_id")]
            keys = [self._decode(row) for row in conn.execute("SELECT body FROM crypto_keys ORDER BY agent_id,key_version")]
        return {"schema_version": 3, "algorithm": ALGORITHM, "did_implemented": False,
                "agents": [i for i in identities if i["agent_id"] != AUDIT_SIGNER],
                "audit_signer": next(i for i in identities if i["agent_id"] == AUDIT_SIGNER),
                "keys": keys, "private_key_exposed": False}
