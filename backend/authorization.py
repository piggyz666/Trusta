import hmac
import json
import time
from backend.domain import Rejected, canonical, digest, uid


class AuthorizationEngine:
    def __init__(self, db, config, safety=None):
        self.db, self.config, self.safety = db, config, safety

    def strong_valid(self, auth):
        return self.safety.strong_valid(auth) if self.safety else auth.get("auth_method") == "sandbox_secret"

    def request(self, action, policy):
        if policy["decision"] == "BLOCK":
            raise Rejected("POLICY_BLOCKED")
        record = {"authorization_id": uid(), "user_id": action["customer_id"], "action_hash": digest(action),
                  "auth_method": ("totp" if self.safety and self.safety.enabled else "sandbox_secret") if policy["decision"] == "STRONG_AUTH" else "explicit_confirmation",
                  "status": "PENDING_STRONG_AUTH" if policy["decision"] == "STRONG_AUTH" else "PENDING_CONFIRMATION",
                  "created_at": time.time(), "expires_at": min(time.time() + self.config.authorization_ttl, action["expires_at"])}
        with self.db.transaction() as db:
            db.execute("INSERT INTO authorizations VALUES(?,?)", (record["authorization_id"], canonical(record)))
        return record

    def get(self, auth_id):
        row = self.db.one("SELECT body FROM authorizations WHERE id=?", (auth_id,))
        if not row:
            raise Rejected("MISSING_AUTHORIZATION")
        return json.loads(row["body"])

    def decide(self, auth_id, user_id, action, approve, strong_secret=""):
        if type(approve) is not bool:
            raise Rejected("INVALID_INPUT")
        error = None
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM authorizations WHERE id=?", (auth_id,)).fetchone()
            if not row:
                raise Rejected("MISSING_AUTHORIZATION")
            auth = json.loads(row["body"])
            if auth["user_id"] != user_id or auth["action_hash"] != digest(action):
                raise Rejected("AUTHORIZATION_HASH_MISMATCH")
            if time.time() >= auth["expires_at"] or time.time() >= action["expires_at"]:
                auth["status"] = "EXPIRED"
            else:
                if auth["status"] not in {"PENDING_CONFIRMATION", "PENDING_STRONG_AUTH"}:
                    raise Rejected("INVALID_AUTHORIZATION_STATE")
                if approve and self.safety:
                    self.safety.assert_unlocked(user_id, db)
                if approve and auth["status"] == "PENDING_STRONG_AUTH":
                    if self.safety and self.safety.enabled:
                        proof, error = self.safety.verify_authorization(db, auth, action, strong_secret)
                        if not error:
                            auth["strong_auth_proof"] = proof
                    elif not self.config.strong_auth_secret or not hmac.compare_digest(self.config.strong_auth_secret, strong_secret):
                        raise Rejected("STRONG_AUTH_FAILED")
                if not error:
                    auth["status"] = "AUTHORIZED" if approve else "DENIED"
            db.execute("UPDATE authorizations SET body=? WHERE id=?", (canonical(auth), auth_id))
        if error:
            raise Rejected(error)
        return auth

    def validate(self, auth_id, action):
        auth = self.get(auth_id)
        if auth["status"] != "AUTHORIZED":
            raise Rejected("INVALID_AUTHORIZATION")
        if time.time() >= auth["expires_at"]:
            raise Rejected("AUTHORIZATION_EXPIRED")
        if auth["action_hash"] != digest(action) or auth["user_id"] != action["customer_id"]:
            raise Rejected("AUTHORIZATION_HASH_MISMATCH")
        if self.safety and self.safety.enabled and auth["auth_method"] == "totp" and not self.strong_valid(auth):
            raise Rejected("STRONG_AUTH_REQUIRED")
        return auth
