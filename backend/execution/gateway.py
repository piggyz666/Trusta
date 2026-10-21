from backend.domain import Rejected, digest


class ToolGateway:
    def __init__(self, adapter, capabilities):
        self._adapter, self.capabilities = adapter, capabilities
        self.contracts = None
        self.schema = None

    def read(self, actor, customer_id, capability, **params):
        if capability not in self._adapter.manifest or capability not in self.capabilities.READ_MAP:
            raise Rejected("CAPABILITY_VIOLATION")
        self.capabilities.check(actor, self.capabilities.READ_MAP[capability])
        if capability in {"card.list", "card.get", "card_application.get", "subscription.list", "subscription.get", "direct_debit.list", "direct_debit.get", "portfolio.get", "operation.get", "operation.evidence", "transaction.recurring_history"}:
            required = {"card.get": {"resource_id"}, "subscription.get": {"resource_id"}, "direct_debit.get": {"resource_id"},
                        "card_application.get":{"resource_id"},
                        "portfolio.get": {"account_id", "product_id"}, "operation.get": {"operation_id"}, "operation.evidence": {"operation_id"}, "transaction.recurring_history": {"account_id"}}
            if set(params) != required.get(capability, set()) or any(not isinstance(value, str) or not value for value in params.values()):
                raise Rejected("INVALID_READ_PARAMETERS")
            return self._adapter.read_domain(customer_id, capability, **params)
        if capability in {"account.get", "account.get_balance"}:
            account = self._adapter.get_account(params["account_id"])
            if account["customer_id"] != customer_id:
                raise Rejected("ACCOUNT_ACCESS_DENIED")
            return account if capability == "account.get" else self._adapter.get_balance(params["account_id"])
        if capability == "payee.resolve":
            return self._adapter.resolve_payee(customer_id, **params)
        if capability == "transaction.list":
            if set(params) != {"account_id", "month"}:
                raise Rejected("INVALID_READ_PARAMETERS")
            account = self._adapter.get_account(params["account_id"])
            if account["customer_id"] != customer_id:
                raise Rejected("ACCOUNT_ACCESS_DENIED")
            return self._adapter.list_consumption(customer_id, params["account_id"], params["month"])
        if capability == "customer.get_risk_profile":
            if params:
                raise Rejected("INVALID_READ_PARAMETERS")
            return self._adapter.get_risk_profile(customer_id)
        if capability == "wealth.list_products":
            if params:
                raise Rejected("INVALID_READ_PARAMETERS")
            return self._adapter.list_products()
        transaction = self._adapter.get_transaction(params["transaction_id"])
        if transaction:
            account = self._adapter.get_account(transaction["source_account_id"])
            if account["customer_id"] != customer_id:
                raise Rejected("TRANSACTION_ACCESS_DENIED")
        return transaction

    def execute(self, actor, action, contract):
        if getattr(self._adapter,"external",False) and self.contracts:
            row=self.contracts.db.one("SELECT body FROM tasks WHERE id=?",(action["task_id"],))
            if row:
                import json
                if json.loads(row["body"])["task_type"]=="SCHEDULED_TRANSFER" and not self._adapter.provider.safe_mode.scheduled_enabled:
                    raise Rejected("EXTERNAL_SCHEDULE_DISABLED")
        capability = action.get("requested_capability", "banking.transfer")
        self.capabilities.check(actor, capability)
        if self.capabilities.registry.banking_version == 5:
            owner = action.get("initiating_agent")
            proposal = "transfer.propose" if action.get("action_type")=="TRANSFER" else "wealth.propose" if owner=="wealth" else "service.propose"
            self.capabilities.check(owner,proposal)
        if action.get("action_type") != "TRANSFER":
            from backend.banking.actions import ACTION_SPECS
            spec = ACTION_SPECS.get(action.get("action_type"))
            if not spec or spec["tool"] not in self._adapter.manifest or not self.contracts or not self.schema:
                raise Rejected("GATEWAY_NOT_READY")
            self.schema.validate(action)
            self.contracts.validate(contract, action)
            return self._guarded_write(action, contract, lambda: self._adapter.apply_action(action, digest(action), contract["expires_at"]))
        if "transfer.create" not in self._adapter.manifest or not self.contracts or not self.schema:
            raise Rejected("GATEWAY_NOT_READY")
        self.schema.validate(action)
        self.contracts.validate(contract, action)
        deadline=contract["expires_at"]
        if getattr(self,"payment_guard",None):
            limit=self.payment_guard(action)
            if limit is not None: deadline=min(deadline,limit)
        return self._guarded_write(action, contract, lambda: self._adapter.create_transfer(action, digest(action), deadline))

    def _guarded_write(self, action, contract, operation):
        safety = getattr(self, "safety", None)
        if safety:
            safety.before_execution(action, self.contracts.authorizations.get(contract["authorization_id"]))
        try:
            result = operation()
            if safety:
                safety.execution_succeeded(action["customer_id"])
            return result
        except Exception as exc:
            if safety:
                safety.execution_failed(action["customer_id"], getattr(exc, "code", "BANK_WRITE_UNCERTAIN"))
            raise

    def verify_external(self, action, transaction_id):
        self.capabilities.check("verifier","transaction.read")
        self.read("verifier",action["customer_id"],"account.get",account_id=action["source_account_id"])
        return self._adapter.verify_external(action,transaction_id)
