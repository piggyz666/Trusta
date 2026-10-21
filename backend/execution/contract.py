import hashlib
import hmac
import json
import time
from backend.domain import Rejected, canonical, digest, uid


class ExecutionContractService:
    def __init__(self, db, policies, authorizations, capabilities, config):
        self.db, self.policies, self.authorizations = db, policies, authorizations
        self.capabilities, self.config = capabilities, config

    def _seal(self, record):
        body = {k: v for k, v in record.items() if k != "contract_hash"}
        key = hmac.new(self.config.signing_key, b"execution-contract-v1", hashlib.sha256).digest()
        return hmac.new(key, canonical(body).encode(), hashlib.sha256).hexdigest()

    def issue(self, action, policy_id, authorization_id):
        self.capabilities.check("executor", action["requested_capability"])
        auth = self.authorizations.validate(authorization_id, action)
        policy = self.policies.get(policy_id)
        if policy["decision"] == "BLOCK" or policy["action_hash"] != digest(action):
            raise Rejected("INVALID_POLICY")
        if policy["decision"] == "STRONG_AUTH" and not self.authorizations.strong_valid(auth):
            raise Rejected("STRONG_AUTH_REQUIRED")
        if time.time() >= action["expires_at"]:
            raise Rejected("ACTION_EXPIRED")
        contract = {"contract_id": uid(), "action_id": action["action_id"], "approved_action_hash": digest(action),
                    "policy_decision_id": policy_id, "authorization_id": authorization_id,
                    "capability": action["requested_capability"], "preconditions": action["preconditions"],
                    "issued_at": time.time(), "expires_at": min(time.time() + self.config.contract_ttl, auth["expires_at"], action["expires_at"]),
                    "idempotency_key": action["idempotency_key"]}
        contract["contract_hash"] = self._seal(contract)
        with self.db.transaction() as db:
            db.execute("INSERT INTO execution_contracts VALUES(?,?)", (contract["contract_id"], canonical(contract)))
        return contract

    def validate(self, contract, action):
        if not isinstance(contract, dict) or "contract_id" not in contract:
            raise Rejected("MISSING_CONTRACT")
        row = self.db.one("SELECT body FROM execution_contracts WHERE id=?", (contract["contract_id"],))
        if not row or json.loads(row["body"]) != contract or not hmac.compare_digest(self._seal(contract), contract.get("contract_hash", "")):
            raise Rejected("INVALID_CONTRACT")
        if time.time() >= contract["expires_at"] or time.time() >= action["expires_at"]:
            raise Rejected("CONTRACT_EXPIRED")
        if contract["approved_action_hash"] != digest(action) or contract["action_id"] != action["action_id"]:
            raise Rejected("ACTION_HASH_MISMATCH")
        if (contract["idempotency_key"] != action["idempotency_key"] or contract["preconditions"] != action["preconditions"]
                or contract["capability"] != action["requested_capability"]):
            raise Rejected("CONTRACT_MISMATCH")
        self.capabilities.check("executor", contract["capability"])
        self.authorizations.validate(contract["authorization_id"], action)
        policy = self.policies.get(contract["policy_decision_id"])
        if policy["decision"] == "BLOCK" or policy["action_hash"] != digest(action):
            raise Rejected("INVALID_POLICY")
        if policy["decision"] == "STRONG_AUTH" and not self.authorizations.strong_valid(self.authorizations.get(contract["authorization_id"])):
            raise Rejected("STRONG_AUTH_REQUIRED")
