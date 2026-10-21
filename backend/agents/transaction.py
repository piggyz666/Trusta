import time
from backend.domain import BankActionIR, Rejected, uid


class TransactionAgent:
    def __init__(self, gateway, config):
        self.gateway, self.config = gateway, config

    def recipient(self, user_id, name):
        candidates = self.gateway.read("transaction", user_id, "payee.resolve", name=name)
        if len(candidates) != 1:
            raise Rejected("RECIPIENT_AMBIGUOUS", "收款人未唯一匹配，请核对收款人姓名")
        return candidates[0]

    def account(self, user_id, account_id):
        return self.gateway.read("transaction", user_id, "account.get", account_id=account_id)

    def balance(self, user_id, account_id):
        return self.gateway.read("transaction", user_id, "account.get_balance", account_id=account_id)

    def propose(self, task, recipient, account, balance):
        normalized = task["context"]["normalized"]
        return BankActionIR(action_id=uid(), task_id=task["task_id"], action_type="TRANSFER",
                            initiating_agent="transaction", customer_id=task["user_id"],
                            source_account_id=account["id"], recipient_id=recipient["id"],
                            amount=normalized["amount"], currency=normalized["currency"],
                            requested_capability="banking.transfer", reason=task["user_goal"],
                            evidence_refs=[f"task:{task['task_id']}:context:source_account", f"task:{task['task_id']}:context:recipient",
                                           f"task:{task['task_id']}:context:balance"],
                            preconditions={"balance": balance["balance"], "account_version": balance["version"]},
                            expected_postconditions={"after_balance": balance["balance"] - normalized["amount"], "status": "SETTLED"},
                            created_at=time.time(), expires_at=time.time() + self.config.action_ttl,
                            idempotency_key=uid()).to_dict()
