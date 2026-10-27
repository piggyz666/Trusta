"""Incoming credits for any bank that offers its transaction list (transactions.read).

The bundled sandbox bank pushes credits itself (SandboxFeed). A real bank usually does not push to TRUSTA, but its
ordinary transaction list contains every credit: this feed reads that list through the bank interface and presents the
incoming ones in the same shape, so 入账明细 and the 工资到账 automation trigger work with any bank.

Payer names and memos are written by other people. They are classified with the same deterministic guard as the
sandbox feed; anything that reads like an instruction is shown only as a placeholder and is recorded for audit.
"""
import time
from datetime import datetime
from backend.business import CHINA
from backend.domain import digest
from backend.banking.sandbox_feed import SCHEMA, classify

WINDOW_MONTHS = 3
CACHE_SECONDS = 60


def _months(since, now):
    start = datetime.fromtimestamp(max(since, now - 100 * 86400), CHINA)
    end = datetime.fromtimestamp(now, CHINA)
    y, m, out = start.year, start.month, []
    while (y, m) <= (end.year, end.month) and len(out) < WINDOW_MONTHS + 1:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def is_credit(row):
    return row.get("direction") == "IN" or row.get("kind") == "CREDIT"


class BankTransactionFeed:
    def __init__(self, service):
        self.s = service
        self._cache = {}
        with service.db.transaction() as conn:
            conn.executescript(SCHEMA)

    def _quarantine(self, owner, record_id, *texts):
        for text in texts:
            category = classify(text) if text else None
            if category:
                with self.s.db.transaction() as conn:
                    if not conn.execute("SELECT 1 FROM quarantined_texts WHERE record_id=? AND customer_id=?", (record_id, owner)).fetchone():
                        conn.execute("INSERT INTO quarantined_texts VALUES(?,?,?,?,?,?,?)",
                                     ("q-" + digest([owner, record_id])[:12], owner, "incoming_credit", record_id, category, digest(text), time.time()))
                        self.s.audit.append_in(conn, None, None, "UNTRUSTED_TEXT_QUARANTINED", "security",
                                               {"owner": owner, "source": "incoming_credit", "record_id": record_id, "category": category,
                                                "text_hash": digest(text)})
                return category
        return None

    def _credits(self, owner, since):
        from backend.bank_sdk.discovery import customer_accounts
        now = time.time()
        key = (owner, int(since // 86400))
        hit = self._cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        rows = []
        for account in customer_accounts(self.s, owner):
            for month in _months(since, now):
                for r in self.s.gateway.read("finance", owner, "transaction.list", account_id=account["id"], month=month):
                    if not is_credit(r) or r.get("status", "SETTLED") != "SETTLED":
                        continue
                    payer = str(r.get("payer") or r.get("counterparty") or r.get("merchant") or "银行入账")[:64]
                    memo = str(r.get("memo") or r.get("reference") or "")[:200]
                    rows.append({"id": str(r["id"]), "customer_id": owner, "account_id": account["id"], "payer": payer, "memo": memo,
                                 "amount": int(r["amount"]), "currency": r.get("currency", account["currency"]),
                                 "occurred_at": float(r["occurred_at"]),
                                 "quarantine": self._quarantine(owner, str(r["id"]), payer, memo)})
        rows.sort(key=lambda r: (r["occurred_at"], r["id"]))
        self._cache[key] = (now, rows)
        return rows

    def incoming(self, owner, since=0.0, limit=50):
        return [r for r in self._credits(owner, since) if r["occurred_at"] > since][:limit]

    def quarantined_ids(self, owner):
        return {r["record_id"]: r["category"] for r in self.s.db.rows(
            "SELECT record_id, category FROM quarantined_texts WHERE customer_id=?", (owner,))}
