class VirtualBankAdapter:
    manifest = {
        "account.get": {"risk_class": "READ"},
        "account.get_balance": {"risk_class": "READ"},
        "payee.resolve": {"risk_class": "READ"},
        "transfer.create": {"risk_class": "WRITE", "requires_auth": True, "supports_idempotency": True, "reversible": False},
        "transaction.get": {"risk_class": "READ"},
        "transaction.list": {"risk_class": "READ"},
        "customer.get_risk_profile": {"risk_class": "READ"},
        "wealth.list_products": {"risk_class": "READ"},
    }

    def __init__(self, bank):
        self.bank = bank
        self.manifest = dict(type(self).manifest)
        if bank.banking_version == 5:
            from backend.banking.actions import ACTION_SPECS
            for spec in ACTION_SPECS.values():
                self.manifest[spec["tool"]] = {"risk_class": "WRITE", "requires_auth": True, "supports_idempotency": True, "reversible": False}
            for tool in ("finance.get", "finance.list", "finance.quote", "finance.archive", "retail.get", "card.list", "card.get", "card_application.get", "subscription.list", "subscription.get", "direct_debit.list", "direct_debit.get", "portfolio.get", "operation.get", "operation.evidence", "transaction.recurring_history",
                         "wish_fund.get", "wish_fund.list", "merchant.list", "life_event.list", "payee_entry.get", "payee_contact.list"):
                self.manifest[tool] = {"risk_class": "READ"}

    def read_domain(self, customer_id, capability, **params):
        if capability == "finance.list":
            return self.bank.extended.finance_state(customer_id)
        if capability == "finance.quote":
            return self.bank.extended.finance_quote(customer_id, params["request"])
        if capability == "finance.archive":
            return self.bank.extended.finance_archive(customer_id, params["contract_id"])
        if capability == "operation.evidence":
            from backend.domain import Rejected
            operation_id=params["operation_id"]
            if not self.bank.extended.operation(customer_id,operation_id):
                raise Rejected("TRANSACTION_ACCESS_DENIED")
            return {"investment_transaction":self.bank.db.one("SELECT * FROM investment_transactions WHERE id=? AND customer_id=?",(operation_id,customer_id)),
                    "ledger_entries":self.bank.db.rows("SELECT * FROM ledger_entries WHERE transaction_id=? ORDER BY side",(operation_id,))}
        if capability == "operation.get":
            return self.bank.extended.operation(customer_id, params["operation_id"])
        if capability == "portfolio.get":
            return self.bank.extended.portfolio(customer_id, params["account_id"], params["product_id"])
        if capability == "transaction.recurring_history":
            account = self.bank.get_account(params["account_id"])
            if account["customer_id"] != customer_id:
                from backend.domain import Rejected
                raise Rejected("ACCOUNT_ACCESS_DENIED")
            return self.bank.db.rows("SELECT * FROM recurring_charges WHERE customer_id=? AND account_id=? ORDER BY occurred_at,id", (customer_id, params["account_id"]))
        kind, operation = capability.split(".")
        return self.bank.extended.resource(customer_id, kind, params.get("resource_id") if operation == "get" else None)

    def apply_action(self, action, action_hash, deadline):
        return self.bank.extended.apply_action(action, action_hash, deadline)

    def get_account(self, account_id):
        return self.bank.get_account(account_id)

    def get_balance(self, account_id):
        return self.bank.get_balance(account_id)

    def resolve_payee(self, customer_id, name=None, recipient_id=None):
        return self.bank.resolve_payee(customer_id, name, recipient_id)

    def create_transfer(self, action, action_hash, deadline):
        return self.bank.create_transfer(action, action_hash, deadline)

    def get_transaction(self, transaction_id):
        return self.bank.get_transaction(transaction_id)

    def list_consumption(self, customer_id, account_id, month):
        return self.bank.list_consumption(customer_id, account_id, month)

    def get_risk_profile(self, customer_id):
        return self.bank.get_risk_profile(customer_id)

    def list_products(self):
        return self.bank.list_products()
