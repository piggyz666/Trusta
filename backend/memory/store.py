import json
from functools import wraps
import threading
import time
from backend.domain import Rejected, canonical, uid
from backend.security.registry import CapabilityRegistry


PREFERENCES = {"explanation_style": {"brief", "detailed"},
               "finance_focus": {"monthly_summary", "anomaly_summary"}}


def synchronized(fn):
    @wraps(fn)
    def guarded(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return guarded


class MemoryStore:
    DEFAULT_TTL = 7 * 86400
    MAX_TTL = 30 * 86400

    def __init__(self, db, audit, lock=None):
        self.db, self.audit = db, audit
        self.lock = lock or threading.RLock()
        self.purge_expired()

    def _validate_consent(self, consent, ttl_seconds):
        if consent is not True:
            raise Rejected("MEMORY_CONSENT_REQUIRED")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= self.MAX_TTL:
            raise Rejected("MEMORY_INVALID_TTL")

    def _put_in(self, db, owner, kind, key, value, source_ref, expires_at):
        self._purge_in(db)
        row = db.execute("SELECT body FROM agent_memory WHERE owner=? AND kind=? AND memory_key=?", (owner, kind, key)).fetchone()
        old = json.loads(row["body"]) if row else {}
        now = time.time()
        entry = {"memory_id": old.get("memory_id", uid()), "user_id": owner, "kind": kind, "key": key, "value": value,
                 "source_ref": source_ref, "trust": "USER_PREFERENCE" if kind == "PREFERENCE" else "REFERENCE_ONLY",
                 "schema_version": 1, "revision": old.get("revision", 0) + 1,
                 "created_at": old.get("created_at", now), "updated_at": now, "expires_at": expires_at,
                 "consent_at": now, "consent_ref": uid()}
        db.execute("INSERT INTO agent_memory VALUES(?,?,?,?,?,?) ON CONFLICT(owner,kind,memory_key) DO UPDATE SET expires_at=excluded.expires_at,body=excluded.body",
                   (entry["memory_id"], owner, kind, key, expires_at, canonical(entry)))
        self.audit.append_in(db, None, None, "MEMORY_SAVED", "runtime",
                             {k: entry[k] for k in ("memory_id", "kind", "revision", "expires_at", "consent_ref")})
        return entry

    @synchronized
    def preference(self, owner, key, value, consent, ttl_seconds=DEFAULT_TTL):
        self._validate_consent(consent, ttl_seconds)
        if (not isinstance(key, str) or key not in PREFERENCES or not isinstance(value, str) or value not in PREFERENCES[key]):
            raise Rejected("MEMORY_INVALID_PREFERENCE")
        with self.db.transaction() as db:
            return self._put_in(db, owner, "PREFERENCE", key, value, "authenticated-user:explicit-preference", time.time() + ttl_seconds)

    @synchronized
    def conversation_reference(self, owner, conversation_id, consent, ttl_seconds=DEFAULT_TTL):
        self._validate_consent(consent, ttl_seconds)
        if not isinstance(conversation_id, str):
            raise Rejected("INVALID_INPUT")
        with self.db.transaction() as db:
            row = db.execute("SELECT owner,body FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            body = json.loads(row["body"]) if row else {}
            if not row or row["owner"] != owner:
                raise Rejected("CONVERSATION_ACCESS_DENIED")
            if body["status"] != "ACTIVE" or body["expires_at"] <= time.time():
                raise Rejected("CONVERSATION_EXPIRED")
            return self._put_in(db, owner, "CONVERSATION_REFERENCE", "conversation", None,
                                "conversation:" + conversation_id, min(body["expires_at"], time.time() + ttl_seconds))

    @synchronized
    def risk_reference(self, owner, profile, consent, ttl_seconds=DEFAULT_TTL):
        self._validate_consent(consent, ttl_seconds)
        # Caller is the trusted service after a fresh WealthAgent bank read. No client profile input.
        if profile["customer_id"] != owner or not profile["assessed_at"] <= time.time() < profile["expires_at"]:
            raise Rejected("RISK_PROFILE_EXPIRED")
        with self.db.transaction() as db:
            return self._put_in(db, owner, "RISK_REFERENCE", "risk_profile", None,
                                f"bank:risk_profiles:{owner}", min(profile["expires_at"], time.time() + ttl_seconds))

    def _purge_in(self, db):
        cutoff = time.time()
        rows = db.execute("SELECT id FROM agent_memory WHERE expires_at<=?", (cutoff,)).fetchall()
        if rows:
            db.execute("DELETE FROM agent_memory WHERE expires_at<=?", (cutoff,))
            self.audit.append_in(db, None, None, "MEMORY_EXPIRED", "runtime", {"memory_ids": [r["id"] for r in rows]})
        return len(rows)

    @synchronized
    def purge_expired(self):
        with self.db.transaction() as db:
            return self._purge_in(db)

    def _entries(self, owner, kind=None):
        with self.db.transaction() as db:
            self._purge_in(db)
            rows = db.execute("SELECT body FROM agent_memory WHERE owner=? ORDER BY id", (owner,)).fetchall()
            entries = [json.loads(r["body"]) for r in rows]
            live = []
            for entry in entries:
                if entry["kind"] == "CONVERSATION_REFERENCE":
                    ref = entry["source_ref"].removeprefix("conversation:")
                    row = db.execute("SELECT owner,body FROM conversations WHERE id=?", (ref,)).fetchone()
                    body = json.loads(row["body"]) if row else {}
                    if not row or row["owner"] != owner or body.get("status") != "ACTIVE" or body.get("expires_at", 0) <= time.time():
                        db.execute("DELETE FROM agent_memory WHERE id=?", (entry["memory_id"],))
                        self.audit.append_in(db, None, None, "MEMORY_SOURCE_INVALIDATED", "runtime", {"memory_id": entry["memory_id"]})
                        continue
                if kind is None or entry["kind"] == kind:
                    live.append(entry)
            return live

    @synchronized
    def list(self, owner):
        return self._entries(owner)

    @synchronized
    def read(self, actor, owner, kind):
        CapabilityRegistry().check_memory(actor, kind)
        if getattr(self, "identity_guard", None):
            self.identity_guard(actor)
        entries = self._entries(owner, kind)
        if entries:
            self.audit.append(None, None, "MEMORY_READ", actor, {"memory_ids": [e["memory_id"] for e in entries]})
        return entries

    @synchronized
    def forget(self, owner, memory_id):
        with self.db.transaction() as db:
            row = db.execute("SELECT owner FROM agent_memory WHERE id=?", (memory_id,)).fetchone()
            if not row or row["owner"] != owner:
                raise Rejected("MEMORY_ACCESS_DENIED")
            db.execute("DELETE FROM agent_memory WHERE id=?", (memory_id,))
            self.audit.append_in(db, None, None, "MEMORY_FORGOTTEN", "runtime", {"memory_id": memory_id})
        return {"forgotten": True}

    @synchronized
    def clear(self, owner):
        with self.db.transaction() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM agent_memory WHERE owner=?", (owner,))]
            db.execute("DELETE FROM agent_memory WHERE owner=?", (owner,))
            self.audit.append_in(db, None, None, "MEMORY_CLEARED", "runtime", {"memory_ids": ids})
        return {"forgotten_count": len(ids)}
