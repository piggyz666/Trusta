"""Real-bank readiness: every customer-facing page and the chat must work when the bank is *not* the bundled sandbox.

The simulated bank below keeps its data in its own database, which TRUSTA cannot read; the only way in is the canonical
bank interface (BankRequest → BankResponse). Three profiles are checked:

  full      every capability the SDK defines (no recurring-charge feed, no legacy retail orders)
  partial   full, minus gold and funds — a bank that only offers some businesses
  readonly  accounts / balance / transactions only (what the Revolut sandbox profile offers)

Every business — transfers, cards, subscriptions, wealth, deposits, FX, gold, loans, the credit account and funds — is
carried out end to end against the separate bank, and its effect is checked in the bank's own books.

A page that quietly reads the local sandbox tables, or that fails instead of degrading when the bank lacks a capability,
fails here.
"""
import dataclasses
import os
import tempfile
import time
import unittest

os.environ.setdefault("TRUSTA_DEMO_MODE", "true")

from backend.bank_sdk.contracts import BankError, BankResponse
from backend.bank_sdk.virtual import VirtualBankProvider
from backend.banking.adapter import VirtualBankAdapter
from backend.banking.virtual_bank import VirtualBank
from backend.config import Config
from backend.persistence import Database
from backend.service import TrustaService
from backend.chat.service import ChatService
from tests.test_chat_b8 import ScriptedToolClient

READONLY = {"accounts.read", "balance.read", "transactions.read"}


class ExternalBank:
    """A bank TRUSTA reaches only through the SDK. Account ids are the bank's own."""

    def __init__(self, inner, capabilities):
        self.inner, self.adapter = inner, inner.adapter
        self.manifest = dataclasses.replace(inner.manifest, provider="partner-bank", environment="sandbox",
                                            capabilities=frozenset(capabilities))
        self.customer_id = "demo"

    def request(self, req):
        if req.capability not in self.manifest.capabilities:
            raise BankError("BANK_CAPABILITY_UNAVAILABLE")
        if req.capability == "accounts.read" and "_tool" not in req.parameters:
            rows = self.adapter.bank.db.rows("SELECT id, currency, balance, frozen FROM accounts WHERE customer_id=?", (req.customer_id,))
            return BankResponse(rows, "partner-bank", "sandbox")
        response = self.inner.request(req)
        return BankResponse(response.data, "partner-bank", "sandbox", response.status)

    def verify(self, action, transaction_id):
        return self.inner.verify(action, transaction_id)


def create(raw, db):
    """Provider factory, loaded by TRUSTA from the bank configuration file (provider = "module")."""
    bank = VirtualBank(Database(raw["bank_db"]), 5)
    bank.seed()
    # A real bank has no "recurring charges" feed: monthly charges only appear in the ordinary transaction list.
    now = time.time()
    with bank.db.transaction() as conn:
        for i in range(4):
            conn.execute("INSERT INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)",
                         (f"music-{i}", "acct-demo", "QQ音乐", "娱乐", 1500, "CNY", now - (i * 30 + 3) * 86400, "PURCHASE", "SETTLED"))
    inner = VirtualBankProvider(VirtualBankAdapter(bank))
    full = {c for c in inner.manifest.capabilities if not c.startswith(("retail.", "transactions.recurring"))}
    capabilities = {"readonly": READONLY, "full": full,
                    "partial": {c for c in full if c not in {"finance.gold_buy", "finance.gold_sell", "finance.fund_subscribe",
                                                             "finance.fund_confirm", "finance.fund_cancel", "finance.fund_redeem"}}}[raw["profile"]]
    return ExternalBank(inner, capabilities)


def build(profile):
    folder = tempfile.mkdtemp()
    config = os.path.join(folder, "bank.partner.local.toml")
    with open(config, "w", encoding="utf-8") as handle:
        handle.write(f'provider = "module"\nfactory = "tests.test_real_bank_readiness:create"\n'
                     f'bank_db = "{(folder + "/bank.db").replace(chr(92), "/")}"\nprofile = "{profile}"\n')
    service = TrustaService(Config(db_path=folder + "/trusta.db", signing_key=b"k" * 32, communication_version=3, banking_version=5,
                                   safety_profile="competition", bank_config_path=config))
    # Make sure nothing can lean on the local sandbox tables: they are emptied.
    with service.db.transaction() as conn:
        for table in ("cards", "subscriptions", "direct_debits", "payees", "finance_contracts", "term_deposits", "fx_wallets",
                      "gold_positions", "advanced_positions"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
    chat = ChatService(service, ScriptedToolClient({}))
    # The partner bank prices gold from this fixture feed (its own market data in real life).
    chat.market = type("Market", (), {"view": lambda self: {"quotes": [{"symbol": "XAU", "price": 800.01, "snapshot": False, "stale": False,
                                                                        "source": "TEST FIXTURE", "as_of": "test"}]}})()
    return service, chat


def bank_db(service):
    return service.gateway._adapter.provider.adapter.bank.db


class RealBankReadiness(unittest.TestCase):
    def pages(self, chat):
        from backend.bank_sdk.presentation import bank_overview
        from backend.chat.activity import ActivityFeed
        from backend.chat.pages import BillsPage, WealthPage
        return (bank_overview(chat.s, "demo"), ActivityFeed(chat).overview("demo"), BillsPage(chat).view("demo"),
                WealthPage(chat, None).view("demo"), chat.capabilities.public("demo"))

    def test_full_bank_pages_use_the_bank(self):
        service, chat = build("full")
        bank, overview, bills, wealth, caps = self.pages(chat)
        self.assertEqual(bank["provider"], "partner-bank")
        self.assertEqual({a["id"] for a in overview["accounts"]}, {"acct-demo", "acct-savings"})
        self.assertTrue(overview["cards"], "cards come from the bank's card list")
        self.assertTrue(all(c["balance"] is not None for c in overview["cards"]))
        self.assertTrue(overview["finance"]["sandbox_modules"])
        self.assertTrue(bills["items"], "subscriptions come from the bank")
        self.assertIn("QQ音乐", {d["merchant"] for d in bills["detected"]}, "recurring charges found from the plain transaction list")
        self.assertTrue(wealth["products"])
        self.assertIsNotNone(wealth["plan"], "allocation plan uses the bank's account balance")
        features = {f["id"]: f["status"] for f in caps["features"]}
        self.assertEqual(features["transfer"], "AVAILABLE")
        for business in ("deposits", "fx", "gold", "funds", "loans", "credit_account"):
            self.assertEqual(features[business], "AVAILABLE", business)

    def test_full_bank_chat(self):
        service, chat = build("full")
        session = chat.create_session("demo")
        sid, rev = session["session_id"], session["revision"]
        reply = chat.send(sid, "demo", "m1", rev, "查一下余额", None)
        self.assertIn("10,000.00", reply["reply"]["text"])
        reply = chat.send(sid, "demo", "m2", reply["revision"], "给张三转500元", None)
        self.assertIn("confirm", [c.get("type") for c in reply["reply"]["cards"]])


    def test_readonly_bank_degrades(self):
        service, chat = build("readonly")
        bank, overview, bills, wealth, caps = self.pages(chat)
        self.assertEqual(len(overview["accounts"]), 2)
        self.assertEqual(overview["cards"], [])
        self.assertIn("cards", overview["unavailable"])
        self.assertEqual(bills["items"], [])
        self.assertIn("subscriptions", bills["unavailable"])
        self.assertIn("QQ音乐", {d["merchant"] for d in bills["detected"]})
        self.assertEqual(wealth["products"], [])
        self.assertIn("products", wealth["unavailable"])
        features = {f["id"]: f["status"] for f in caps["features"]}
        self.assertEqual(features["transfer"], "UNAVAILABLE")
        self.assertEqual(features["balance"], "AVAILABLE")
        session = chat.create_session("demo")
        reply = chat.send(session["session_id"], "demo", "m1", session["revision"], "给张三转500元", None)
        self.assertIn("不可用", reply["reply"]["text"])
        self.assertNotIn("confirm", [c.get("type") for c in reply["reply"]["cards"]])
        self.assertIn("finance", overview["unavailable"])
        for business in ("deposits", "fx", "gold", "funds", "loans", "credit_account"):
            self.assertEqual(features[business], "UNAVAILABLE", business)
        from backend.banking.finance import Finance
        from backend.domain import Rejected
        with self.assertRaises(Rejected):
            Finance(chat)

    # ---- every extended business, end to end, against the separate bank ----------------------------------------
    def complete(self, service, chat, contract, operation):
        from unittest.mock import patch
        from backend.banking.finance import Finance
        f = Finance(chat)
        proposal = f.propose("demo", {"contract_id": contract["id"], "operation": operation})
        self.assertEqual(proposal["status"], "WAITING_USER", proposal)
        card = next(c for c in proposal["cards"] if c["type"] == "confirm")
        body = {k: card[k] for k in ("task_id", "action_id", "action_hash")} | {"approve": True}
        if card["risk_level"] == "RED":
            with patch.dict(os.environ, {"TRUSTA_DEMO_MODE": "true"}):
                sms = f.sms("demo", {k: card[k] for k in ("task_id", "action_id")})
            body["verification"] = {"method": "SMS", "code": sms["dev_code"]}
        result = f.decide("demo", body)
        self.assertEqual(result["status"], "COMPLETED", result)

    def test_extended_businesses_run_on_the_bank(self):
        from backend.banking.finance import Finance
        from backend.banking.extensions import Extensions
        from backend.banking.advanced import Advanced
        service, chat = build("full")
        bank = bank_db(service)
        with bank.transaction() as conn:
            conn.execute("UPDATE risk_profiles SET risk_level=3 WHERE customer_id='demo'")
        balance = lambda: bank.one("SELECT balance FROM accounts WHERE id='acct-demo'")["balance"]
        start = balance()
        f = Finance(chat)
        deposit = Extensions(chat).create("demo", {"type": "deposit_open", "card_id": "card-demo", "amount": 10000, "term": 90})["contract"]
        self.complete(service, chat, deposit, "DEPOSIT_OPEN")
        fx = Extensions(chat).create("demo", {"type": "fx_buy", "card_id": "card-demo", "currency": "USD", "units": 1000, "quote_mode": "practice"})["contract"]
        self.complete(service, chat, fx, "FX_BUY")
        gold = f.create("demo", {"type": "gold_buy", "card_id": "card-demo", "units": 3})["contract"]
        self.complete(service, chat, gold, "GOLD_BUY")
        loan = f.create("demo", {"type": "loan", "card_id": "card-demo", "amount": 10001, "months": 3})["contract"]
        self.complete(service, chat, loan, "LOAN_DRAW")
        fund = Advanced(chat).create("demo", {"type": "fund_subscribe", "card_id": "card-demo", "product_id": "practice-bond", "amount": 10000})["contract"]
        self.complete(service, chat, fund, "FUND_SUBSCRIBE")
        charge = Advanced(chat).create("demo", {"type": "card_charge", "card_id": "card-demo", "amount": 5000, "merchant": "测试商户"})["contract"]
        self.complete(service, chat, charge, "CARD_CHARGE")
        # The bank's own books moved; TRUSTA's database holds none of it.
        self.assertEqual(balance(), start - 10000 - 7000 - 2400 + 10001 - 10000)
        self.assertEqual(len(bank.rows("SELECT id FROM term_deposits")), 1)
        self.assertEqual(bank.one("SELECT units FROM fx_wallets")["units"], 1000)
        self.assertEqual(bank.one("SELECT units FROM gold_positions")["units"], 3)
        for table in ("finance_contracts", "term_deposits", "fx_wallets", "gold_positions", "advanced_positions"):
            self.assertEqual(service.db.rows(f"SELECT * FROM {table}"), [], table)
        # Pages show the bank's state.
        self.assertEqual(Extensions(chat).view("demo")["deposits"][0]["principal"], 10000)
        self.assertEqual(f.view("demo")["outstanding"], 10001)
        states = Advanced(chat).view("demo")["states"]
        self.assertEqual(sum(s["debt"] for s in states), 5000)
        overview = __import__("backend.chat.activity", fromlist=["ActivityFeed"]).ActivityFeed(chat).overview("demo")
        self.assertEqual(overview["finance"]["deposit_principal_minor"], 10000)
        self.assertEqual(overview["finance"]["gold_units"], 3)

    def test_partial_bank_offers_only_some_businesses(self):
        from backend.banking.finance import Finance
        from backend.domain import Rejected
        service, chat = build("partial")
        features = {f["id"]: f["status"] for f in chat.capabilities.public("demo")["features"]}
        self.assertEqual(features["deposits"], "AVAILABLE")
        self.assertEqual(features["loans"], "AVAILABLE")
        self.assertEqual(features["gold"], "UNAVAILABLE")
        self.assertEqual(features["funds"], "UNAVAILABLE")
        f = Finance(chat)
        with self.assertRaises(Rejected) as caught:
            f.create("demo", {"type": "gold_buy", "card_id": "card-demo", "units": 3})
        self.assertEqual(caught.exception.code, "BANK_CAPABILITY_UNAVAILABLE")
        self.assertTrue(f.create("demo", {"type": "loan", "card_id": "card-demo", "amount": 10001, "months": 3})["contract"]["id"])

    def test_no_app_layer_reads_of_bank_tables(self):
        """Guard: customer-facing read models must ask the bank, not the sandbox tables."""
        import re
        root = os.path.join(os.path.dirname(__file__), "..", "backend", "chat")
        pattern = re.compile(r"FROM\s+(accounts|cards|subscriptions|direct_debits|wealth_products)\b|JOIN\s+accounts\b")
        offenders = []
        for name in os.listdir(root):
            if name.endswith(".py"):
                for number, line in enumerate(open(os.path.join(root, name), encoding="utf-8"), 1):
                    if pattern.search(line):
                        offenders.append(f"{name}:{number}")
        self.assertEqual(offenders, [], "use backend.bank_sdk.discovery instead")


if __name__ == "__main__":
    unittest.main()
