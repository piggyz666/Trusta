"""Real-bank readiness: every customer-facing page and the chat must work when the bank is *not* the bundled sandbox.

The simulated bank below keeps its data in its own database, which TRUSTA cannot read; the only way in is the canonical
bank interface (BankRequest → BankResponse). Two profiles are checked:

  full      everything the SDK defines except the sandbox-only extension businesses
  readonly  accounts / balance / transactions only (what the Revolut sandbox profile offers)

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
    capabilities = READONLY if raw["profile"] == "readonly" else {c for c in inner.manifest.capabilities
                                                                  if not c.startswith(("finance.", "retail.", "transactions.recurring"))}
    return ExternalBank(inner, capabilities)


def build(profile):
    folder = tempfile.mkdtemp()
    config = os.path.join(folder, "bank.partner.local.toml")
    with open(config, "w", encoding="utf-8") as handle:
        handle.write(f'provider = "module"\nfactory = "tests.test_real_bank_readiness:create"\n'
                     f'bank_db = "{(folder + "/bank.db").replace(chr(92), "/")}"\nprofile = "{profile}"\n')
    service = TrustaService(Config(db_path=folder + "/trusta.db", signing_key=b"k", communication_version=3, banking_version=5,
                                   strong_auth_secret="x", bank_config_path=config))
    # Make sure nothing can lean on the local sandbox tables: they are emptied.
    with service.db.transaction() as conn:
        for table in ("cards", "subscriptions", "direct_debits", "payees"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
    return service, ChatService(service, ScriptedToolClient({}))


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
        self.assertFalse(overview["finance"]["sandbox_modules"])
        self.assertTrue(bills["items"], "subscriptions come from the bank")
        self.assertIn("QQ音乐", {d["merchant"] for d in bills["detected"]}, "recurring charges found from the plain transaction list")
        self.assertTrue(wealth["products"])
        self.assertIsNotNone(wealth["plan"], "allocation plan uses the bank's account balance")
        features = {f["id"]: f["status"] for f in caps["features"]}
        self.assertEqual(features["transfer"], "AVAILABLE")
        self.assertEqual(features["sandbox_extensions"], "UNAVAILABLE")

    def test_full_bank_chat(self):
        service, chat = build("full")
        session = chat.create_session("demo")
        sid, rev = session["session_id"], session["revision"]
        reply = chat.send(sid, "demo", "m1", rev, "查一下余额", None)
        self.assertIn("10,000.00", reply["reply"]["text"])
        reply = chat.send(sid, "demo", "m2", reply["revision"], "给张三转500元", None)
        self.assertIn("confirm", [c.get("type") for c in reply["reply"]["cards"]])
        reply = chat.send(sid, "demo", "m3", reply["revision"], "存入100元定期存款，90天", None)
        self.assertIn("仅内置沙箱银行", reply["reply"]["text"])

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
