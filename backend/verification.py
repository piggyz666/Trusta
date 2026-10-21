from backend.domain import digest


class HardVerifier:
    def __init__(self, gateway):
        self.gateway = gateway

    def verify(self, action, transaction_id):
        if getattr(self.gateway._adapter, "external", False):
            from dataclasses import asdict
            try:
                result=asdict(self.gateway.verify_external(action,transaction_id))
            except Exception:
                return {"status":"MANUAL_REVIEW","reason":"EXTERNAL_STATUS_UNCERTAIN","provider_status":"UNKNOWN"}
            result.update(pre_state_hash=digest(action["preconditions"]),post_state_hash=result["evidence_hash"])
            return result
        if action["action_type"] != "TRANSFER":
            try:
                from backend.banking.actions import snapshot_from_gateway
                transaction = self.gateway.read("verifier", action["customer_id"], "operation.get", operation_id=transaction_id)
                actual = snapshot_from_gateway(self.gateway, "verifier", action["customer_id"], action["source_account_id"], action["action_type"], action["target"]["id"])
                matches = (transaction and transaction["action_hash"] == digest(action) and transaction["status"] == "SETTLED"
                           and transaction["action_id"] == action["action_id"] and transaction["action_type"] == action["action_type"]
                           and transaction["parameters"] == action["parameters"] and transaction["target"] == action["target"]
                           and transaction["before"] == action["preconditions"] and actual == action["expected_postconditions"]
                           and transaction["after"] == actual)
                if action["target"]["kind"]=="product":
                    evidence=self.gateway.read("verifier",action["customer_id"],"operation.evidence",operation_id=transaction_id)
                    investment=evidence["investment_transaction"]
                    amount=action["parameters"]["amount"]
                    purchase=action["action_type"]=="INVESTMENT_PURCHASE"
                    expected_ledger={(action["source_account_id"],"DEBIT" if purchase else "CREDIT",amount),
                                     ("investment-clearing","CREDIT" if purchase else "DEBIT",amount)}
                    matches=matches and bool(investment) and investment["amount"]==amount and investment["account_id"]==action["source_account_id"] and investment["product_id"]==action["target"]["id"] and investment["status"]=="SETTLED" and investment["direction"]==("PURCHASE" if purchase else "REDEEM") and investment["currency"]=="CNY"
                    matches=matches and len(evidence["ledger_entries"])==2 and {(row["account_id"],row["side"],row["amount"]) for row in evidence["ledger_entries"]}==expected_ledger
                return {"status": "VERIFIED" if matches else "MANUAL_REVIEW", "reason": "MATCH" if matches else "AUTHORITATIVE_STATE_MISMATCH",
                        "pre_state_hash": digest(action["preconditions"]), "post_state_hash": digest(actual)}
            except Exception:
                return {"status": "MANUAL_REVIEW", "reason": "AUTHORITATIVE_READ_UNCERTAIN"}
        try:
            transaction = self.gateway.read("verifier", action["customer_id"], "transaction.get", transaction_id=transaction_id)
            balance = self.gateway.read("verifier", action["customer_id"], "account.get_balance", account_id=action["source_account_id"])
            if not transaction:
                return {"status": "MANUAL_REVIEW", "reason": "TRANSACTION_NOT_FOUND"}
            matches = (transaction["amount"] == action["amount"] and transaction["recipient_id"] == action["recipient_id"]
                       and transaction["source_account_id"] == action["source_account_id"] and transaction["status"] == "SETTLED"
                       and transaction["currency"] == action["currency"]
                       and transaction["after_balance"] == action["expected_postconditions"]["after_balance"]
                       and balance["balance"] == action["expected_postconditions"]["after_balance"]
                       and balance["version"] == transaction["account_version"])
            return {"status": "VERIFIED" if matches else "MANUAL_REVIEW", "reason": "MATCH" if matches else "AUTHORITATIVE_STATE_MISMATCH",
                    "pre_state_hash": digest({"balance": transaction["before_balance"], "account_id": transaction["source_account_id"]}),
                    "post_state_hash": digest({"balance": balance, "transaction": transaction})}
        except Exception:
            return {"status": "MANUAL_REVIEW", "reason": "AUTHORITATIVE_READ_UNCERTAIN"}
