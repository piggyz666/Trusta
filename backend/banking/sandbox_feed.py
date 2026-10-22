"""Sandbox bank feed: incoming credits (salary, refunds from friends...) and card purchases, as a real bank would
push them. Only the local virtual bank has this feed; external providers declare their own capabilities.

Untrusted-text quarantine (“带毒账单”): payer names, transfer memos and merchant names are written by *other people*.
They are classified at ingestion by the same deterministic guard used for user input; anything that reads like an
instruction is stored but flagged, audited, and only ever shown to the user as a placeholder. None of these fields is
ever sent to the language model (it receives no transaction rows at all), so the text has nothing to hijack.
"""
import time
from backend.chat import guard
from backend.domain import Rejected, digest, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS incoming_credits(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
 payer TEXT NOT NULL, memo TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0), currency TEXT NOT NULL,
 occurred_at REAL NOT NULL, quarantine TEXT);
CREATE INDEX IF NOT EXISTS incoming_owner_time ON incoming_credits(customer_id, occurred_at);
CREATE TABLE IF NOT EXISTS quarantined_texts(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, source TEXT NOT NULL,
 record_id TEXT NOT NULL, category TEXT NOT NULL, text_hash TEXT NOT NULL, created_at REAL NOT NULL);
"""
CATEGORIES = {"FOOD", "GROCERIES", "TRANSPORT", "SHOPPING", "OTHER"}


def classify(text):
    """None for ordinary text, else the guard category (PROMPT_INJECTION / FRAUD_SUSPECTED / INFO_PROBING)."""
    hit = guard.inspect(text)
    return hit["category"] if hit else None


class SandboxFeed:
    def __init__(self, service):
        self.s = service
        with service.db.transaction() as conn:
            conn.executescript(SCHEMA)

    def _account(self, conn, owner, account_id):
        row = conn.execute("SELECT * FROM accounts WHERE id=? AND customer_id=?", (account_id, owner)).fetchone()
        if not row:
            raise Rejected("ACCOUNT_NOT_FOUND")
        return row

    def _quarantine(self, conn, owner, source, record_id, *texts):
        for text in texts:
            category = classify(text)
            if category:
                conn.execute("INSERT INTO quarantined_texts VALUES(?,?,?,?,?,?,?)",
                             ("q-" + uid()[:12], owner, source, record_id, category, digest(text), time.time()))
                self.s.audit.append_in(conn, None, None, "UNTRUSTED_TEXT_QUARANTINED", "security",
                                       {"owner": owner, "source": source, "record_id": record_id, "category": category,
                                        "text_hash": digest(text)})
                return category
        return None

    def credit(self, owner, account_id, amount, payer, memo=""):
        """An external inflow (e.g. salary). Single CREDIT ledger entry: the money comes from outside this bank."""
        if type(amount) is not int or not 0 < amount <= 100_000_000 or not isinstance(payer, str) or not isinstance(memo, str) \
                or not 0 < len(payer) <= 64 or len(memo) > 200:
            raise Rejected("INVALID_INPUT")
        credit_id = "in-" + uid().replace("-", "")[:14]
        now = time.time()
        with self.s.db.transaction() as conn:
            account = self._account(conn, owner, account_id)
            category = self._quarantine(conn, owner, "incoming_credit", credit_id, payer, memo)
            conn.execute("UPDATE accounts SET balance=balance+?, version=version+1 WHERE id=?", (amount, account_id))
            conn.execute("INSERT INTO ledger_entries VALUES(?,?,?,?,?)", ("le-" + uid()[:12], credit_id, account_id, "CREDIT", amount))
            conn.execute("INSERT INTO incoming_credits VALUES(?,?,?,?,?,?,?,?,?)",
                         (credit_id, owner, account_id, payer, memo, amount, account["currency"], now, category))
            self.s.audit.append_in(conn, None, None, "SANDBOX_INCOMING_CREDIT", "bank",
                                   {"credit_id": credit_id, "account_id": account_id, "amount": amount, "quarantined": bool(category)})
        return {"credit_id": credit_id, "amount": amount, "quarantined": category}

    def purchase(self, owner, account_id, merchant, category, amount):
        """A settled card purchase: debits the account and appears in consumption records (bills, budgets, anomalies)."""
        if (type(amount) is not int or not 0 < amount <= 100_000_000 or category not in CATEGORIES
                or not isinstance(merchant, str) or not 0 < len(merchant) <= 64):
            raise Rejected("INVALID_INPUT")
        record_id = "cr-" + uid().replace("-", "")[:14]
        with self.s.db.transaction() as conn:
            account = self._account(conn, owner, account_id)
            if account["balance"] < amount or account["frozen"]:
                raise Rejected("INSUFFICIENT_BALANCE" if not account["frozen"] else "FROZEN_SOURCE")
            card = conn.execute("SELECT status FROM cards WHERE account_id=? ORDER BY id LIMIT 1", (account_id,)).fetchone()
            if card and card["status"] != "ACTIVE":
                raise Rejected("CARD_NOT_ACTIVE")  # a frozen / lost card really stops purchases
            flagged = self._quarantine(conn, owner, "merchant_name", record_id, merchant)
            conn.execute("UPDATE accounts SET balance=balance-?, version=version+1 WHERE id=?", (amount, account_id))
            conn.execute("INSERT INTO ledger_entries VALUES(?,?,?,?,?)", ("le-" + uid()[:12], record_id, account_id, "DEBIT", amount))
            conn.execute("INSERT INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)",
                         (record_id, account_id, merchant, category, amount, account["currency"], time.time(), "PURCHASE", "SETTLED"))
            self.s.audit.append_in(conn, None, None, "SANDBOX_CARD_PURCHASE", "bank",
                                   {"record_id": record_id, "amount": amount, "category": category, "quarantined": bool(flagged)})
        return {"record_id": record_id, "amount": amount, "quarantined": flagged}

    def incoming(self, owner, since=0.0, limit=50):
        rows = self.s.db.rows("SELECT * FROM incoming_credits WHERE customer_id=? AND occurred_at>? ORDER BY occurred_at, id LIMIT ?",
                              (owner, since, limit))
        return [dict(r) for r in rows]

    def quarantined_ids(self, owner):
        return {r["record_id"]: r["category"] for r in self.s.db.rows(
            "SELECT record_id, category FROM quarantined_texts WHERE customer_id=?", (owner,))}
