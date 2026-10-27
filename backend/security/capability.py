from backend.security.registry import BANK_GRANTS, CapabilityRegistry


class CapabilityManager:
    def __init__(self, registry=None):
        self.registry = registry or CapabilityRegistry()
        self.GRANTS = self.registry.bank_grants
        self.identity_guard = None

    identity_guard = None
    READ_MAP = {"finance.get":"account.read", "retail.get":"account.read", "account.get": "account.read", "account.get_balance": "balance.read",
                "payee.resolve": "payee.read", "transaction.get": "transaction.read",
                "transaction.list": "transaction.read", "customer.get_risk_profile": "risk_profile.read",
                "wealth.list_products": "wealth_product.read",
                "card.list": "card.read", "card.get": "card.read",
                "card_application.get":"card.read",
                "subscription.list": "subscription.read", "subscription.get": "subscription.read",
                "direct_debit.list": "direct_debit.read", "direct_debit.get": "direct_debit.read",
                "portfolio.get": "portfolio.read", "operation.get": "operation.read",
                "transaction.recurring_history": "transaction.read", "operation.evidence": "operation.read",
                # B8: earmarked funds, merchant catalogue and life events are account-scoped reads.
                "wish_fund.get": "account.read", "wish_fund.list": "account.read",
                "merchant.list": "account.read", "life_event.list": "account.read",
                "payee_entry.get": "account.read", "payee_contact.list": "account.read"}
    GRANTS = BANK_GRANTS  # Compatibility view; the registry is the single definition.

    def check(self, actor, capability):
        from backend.security.registry import SYSTEM_PRINCIPALS
        if self.identity_guard and actor not in SYSTEM_PRINCIPALS:
            self.identity_guard(actor)
        self.registry.check_bank(actor, capability)
