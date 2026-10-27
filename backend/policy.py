import json
import time
from backend.domain import Rejected, canonical, digest, uid


class PolicyEngine:
    def __init__(self, db, gateway, config, safety=None):
        self.db, self.gateway, self.config, self.safety = db, gateway, config, safety

    def evaluate(self, action):
        if self.safety:
            self.safety.assert_unlocked(action["customer_id"])
        protected = bool(self.safety and action["action_type"] in self.safety.PROTECTED_ACTIONS
                         and self.safety.protection(action["customer_id"]))
        if action["action_type"] != "TRANSFER":
            from backend.banking.actions import snapshot_from_gateway, domain_errors
            current = snapshot_from_gateway(self.gateway, "policy", action["customer_id"], action["source_account_id"], action["action_type"], action["target"]["id"])
            rules = domain_errors(action["action_type"], current, action["parameters"])
            if digest(current) != digest(action["preconditions"]):
                rules.append("STALE_PRECONDITION")
            if protected:
                rules.append("ACCOUNT_PROTECTION_ACTIVE")
            decision = "BLOCK" if rules else "CONFIRM"
            if not rules:
                rules = ["CONFIRM_EACH_ACTION"]
                values = action["parameters"]
                increased = action["action_type"] == "CARD_LIMIT_CHANGE" and values["spending_limit"] > current["target"]["spending_limit"]
                enabled = action["action_type"] == "CARD_TRANSACTION_CONTROL" and any(values[key] and not current["target"][key] for key in values)
                if action["action_type"] in {"CARD_UNFREEZE","CARD_REPORT_LOST","CARD_CANCEL_LOST","CARD_PIN_RESET","ADD_PAYEE"} or increased or enabled or values.get("amount", 0) >= self.config.high_risk_threshold:
                    decision = "STRONG_AUTH"
                    rules.append("HIGH_RISK_CHANGE")
                if self.safety and self.safety.enabled and action["action_type"] in {"FUND_SUBSCRIBE", "FUND_CONFIRM", "FUND_CANCEL", "FUND_REDEEM", "INVESTMENT_PURCHASE", "INVESTMENT_REDEEM", "GOLD_BUY", "GOLD_SELL", "FX_BUY", "FX_SELL"}:
                    decision = "STRONG_AUTH"
                    rules.append("INVESTMENT_STRONG_AUTH")
            record = {"policy_decision_id": uid(), "decision": decision, "rule_ids": rules, "reason_codes": rules,
                      "evaluated_at": time.time(), "action_hash": digest(action),
                      "risk_level": "RED" if decision == "STRONG_AUTH" else "YELLOW"}
            with self.db.transaction() as db:
                db.execute("INSERT INTO policy_decisions VALUES(?,?)", (record["policy_decision_id"], canonical(record)))
            return record
        account = self.gateway.read("policy", action["customer_id"], "account.get", account_id=action["source_account_id"])
        payee = self.gateway.read("policy", action["customer_id"], "payee.resolve", recipient_id=action["recipient_id"])
        rules = []
        decision = "ALLOW"
        if not payee:
            rules.append("UNKNOWN_PAYEE")
            decision = "BLOCK"
        if account["frozen"]:
            rules.append("FROZEN_SOURCE")
            decision = "BLOCK"
        if protected:
            rules.append("ACCOUNT_PROTECTION_ACTIVE")
            decision = "BLOCK"
        if account["balance"] < action["amount"]:
            rules.append("INSUFFICIENT_BALANCE")
            decision = "BLOCK"
        if (account["balance"] != action["preconditions"]["balance"] or
                account["version"] != action["preconditions"]["account_version"]):
            rules.append("STALE_PRECONDITION")
            decision = "BLOCK"
        if decision != "BLOCK":
            decision = "CONFIRM"  # V1 local-bank manifest requires user authorization for every write.
            rules.append("V1_CONFIRM_ALL")
            if payee[0].get("is_new"):  # optional: banks that do not flag new payees rely on TRUSTA's own record (safety.risk)
                rules.append("NEW_RECIPIENT")
            if action["amount"] >= self.config.high_risk_threshold:
                decision = "STRONG_AUTH"
                rules.append("HIGH_RISK_AMOUNT")
        risk = self.safety.risk(action) if self.safety else None
        if risk and risk["requires_strong_auth"] and decision != "BLOCK":
            decision = "STRONG_AUTH"
            rules.extend(risk.get("reasons") or ["DAILY_TRANSFER_STRONG_AUTH" if action["currency"] == "CNY" else "FOREIGN_CURRENCY_STRONG_AUTH"])
        record = {"policy_decision_id": uid(), "decision": decision, "rule_ids": rules,
                  "reason_codes": rules, "evaluated_at": time.time(), "action_hash": digest(action),
                  "risk_level": "RED" if decision == "STRONG_AUTH" else "YELLOW", "daily_risk": risk}
        with self.db.transaction() as db:
            db.execute("INSERT INTO policy_decisions VALUES(?,?)", (record["policy_decision_id"], canonical(record)))
        return record

    def get(self, policy_id):
        row = self.db.one("SELECT body FROM policy_decisions WHERE id=?", (policy_id,))
        if not row:
            raise Rejected("INVALID_POLICY")
        return json.loads(row["body"])
