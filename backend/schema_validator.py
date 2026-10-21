import math
import time
from backend.domain import BankActionIR, Rejected


class SchemaValidator:
    def __init__(self, gateway):
        self.gateway = gateway

    def validate(self, action):
        if action.get("action_type") != "TRANSFER":
            from backend.banking.actions import validate_action
            return validate_action(action, self.gateway)
        def require(condition):
            if not condition:
                raise Rejected("REJECTED_SCHEMA")

        try:
            BankActionIR(**action)
            require(action["action_type"] == "TRANSFER" and action["currency"] in getattr(self.gateway._adapter, "supported_currencies", {"CNY"}))
            require(type(action["amount"]) is int and 0 < action["amount"] <= 10**12)
            require(action["requested_capability"] == "banking.transfer")
            require(action["initiating_agent"] == "transaction")
            require(type(action["created_at"]) in (int, float) and type(action["expires_at"]) in (int, float))
            require(math.isfinite(action["created_at"]) and math.isfinite(action["expires_at"]))
            require(action["created_at"] <= time.time() < action["expires_at"])
            for name in ("action_id", "task_id", "customer_id", "source_account_id", "recipient_id", "idempotency_key", "reason"):
                require(isinstance(action[name], str) and bool(action[name]))
            require(isinstance(action["evidence_refs"], list) and bool(action["evidence_refs"]))
            account = self.gateway.read("schema", action["customer_id"], "account.get", account_id=action["source_account_id"])
            require(account["currency"] == action["currency"])
            pre = action["preconditions"]
            require(set(pre) == {"balance", "account_version"})
            require(type(pre["balance"]) is int and type(pre["account_version"]) is int)
            require(pre["balance"] >= 0 and pre["account_version"] >= 0)
            post = action["expected_postconditions"]
            require(type(post["after_balance"]) is int)
            require(post == {"after_balance": pre["balance"] - action["amount"], "status": "SETTLED"})
            # Payee identity must be an authoritative, customer-scoped bank result.
            candidates = self.gateway.read("schema", action["customer_id"], "payee.resolve", recipient_id=action["recipient_id"])
            require(any(p["id"] == action["recipient_id"] for p in candidates))
        except (AssertionError, KeyError, TypeError, ValueError, Rejected):
            raise Rejected("REJECTED_SCHEMA")
