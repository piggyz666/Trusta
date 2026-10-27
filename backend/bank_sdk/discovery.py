"""What the connected bank actually offers this customer — always asked through the bank interface.

App-layer pages (总览, 账单与订阅, 理财, the chat catalogue) must not read the local sandbox tables directly: with a real
bank those tables are empty or stale, and the bank is the only authority. These helpers are the single place that knows
the difference between the local sandbox bank and an external one.

  customer_accounts  the customer's accounts (id, currency, balance, frozen, name, label) from the bank
  supports           whether the connected bank offers a canonical tool (e.g. "card.list")
  optional_read      a gateway read that returns ``default`` instead of failing when the bank does not offer the tool
  is_external        True for any bank other than the bundled local sandbox
"""
from backend.bank_sdk.contracts import BankRequest


def _adapter(service):
    return service.gateway._adapter


def is_external(service):
    """Real or third-party sandbox bank (Revolut, Starling, a partner bank …) as opposed to the bundled sandbox."""
    return bool(getattr(_adapter(service), "external", False) or getattr(service.config, "bank_config_path", None))


def supports(service, tool):
    return tool in _adapter(service).manifest


def optional_read(service, actor, owner, tool, default=None, **params):
    if not supports(service, tool):
        return [] if default is None else default
    return service.gateway.read(actor, owner, tool, **params)


def customer_accounts(service, owner):
    adapter = _adapter(service)
    provider = getattr(adapter, "provider", None)
    if not is_external(service) or provider is None:
        ids = [row["id"] for row in service.db.rows("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,))]
        rows = [service.gateway.read("verifier", owner, "account.get", account_id=value) for value in ids]
    else:
        # Account discovery is infrastructure, not an Agent grant: the provider binds the caller to the
        # bank-authorised customer and returns only that customer's accounts.
        rows = provider.request(BankRequest("accounts.read", owner)).data or []
        rows = [r if {"balance", "frozen"} <= set(r) else service.gateway.read("verifier", owner, "account.get", account_id=r["id"])
                for r in rows]
    return [{"id": r["id"], "currency": r.get("currency", "CNY"), "balance": r.get("balance", 0), "frozen": r.get("frozen", False),
             "name": r.get("name"), "label": r.get("name") or ("储蓄账户" if "saving" in r["id"] else "活期账户")} for r in rows]


def primary_account(service, owner):
    accounts = customer_accounts(service, owner)
    return accounts[0] if accounts else None
