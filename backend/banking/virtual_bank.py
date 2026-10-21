import time
from backend.domain import Rejected, uid
from backend.business import month_window
from backend.banking.scenario_seed import seed_scenarios


class VirtualBank:
    """Authoritative bank; create_transfer is called by VirtualBankAdapter only."""
    def __init__(self, db, banking_version=4):
        self.db = db
        self.banking_version = banking_version
        from backend.banking.extended_bank import ExtendedBank
        self.extended = ExtendedBank(db)

    def seed(self):
        with self.db.transaction() as db:
            db.execute("INSERT OR IGNORE INTO customers VALUES('demo','演示用户')")
            db.execute("INSERT OR IGNORE INTO accounts VALUES('acct-demo','demo','CNY',1000000,0,0)")
            db.execute("INSERT OR IGNORE INTO accounts VALUES('settlement',NULL,'CNY',0,0,0)")
            db.execute("INSERT OR IGNORE INTO payees VALUES('payee-zhang','demo','张三',0)")
            db.execute("INSERT OR IGNORE INTO payees VALUES('payee-li','demo','李四',1)")
            seed_scenarios(db)
            if self.banking_version == 5:
                from backend.banking.extended_bank import seed_extended
                seed_extended(db)

    def get_account(self, account_id):
        account = self.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not account:
            raise Rejected("ACCOUNT_NOT_FOUND")
        return account

    def get_balance(self, account_id):
        a = self.get_account(account_id)
        return {k: a[k] for k in ("id", "balance", "currency", "version")}

    def resolve_payee(self, customer_id, name=None, recipient_id=None):
        if recipient_id:
            if self.banking_version==5:
                return self.db.rows("SELECT p.* FROM payees p LEFT JOIN payee_contacts c ON p.id=c.payee_id WHERE p.customer_id=? AND p.id=? AND (c.status IS NULL OR c.status='ACTIVE')",(customer_id,recipient_id))
            return self.db.rows("SELECT * FROM payees WHERE customer_id=? AND id=?", (customer_id, recipient_id))
        if self.banking_version == 5:
            return self.db.rows("SELECT p.* FROM payees p LEFT JOIN payee_contacts c ON p.id=c.payee_id "
                                "WHERE p.customer_id=? AND (c.status IS NULL OR c.status='ACTIVE') "
                                "AND (p.name=? OR c.phone=? OR c.alias=?)", (customer_id, name, name, name))
        return self.db.rows("SELECT * FROM payees WHERE customer_id=? AND name=?", (customer_id, name))

    def get_transaction(self, transaction_id):
        return self.db.one("SELECT * FROM transactions WHERE id=?", (transaction_id,))

    def list_consumption(self, customer_id, account_id, month):
        start, end, _ = month_window(month)
        return self.db.rows("SELECT c.* FROM consumption_records c JOIN accounts a ON a.id=c.account_id "
                            "WHERE a.customer_id=? AND c.account_id=? AND c.occurred_at>=? AND c.occurred_at<? "
                            "ORDER BY c.occurred_at,c.id", (customer_id, account_id, start, end))

    def get_risk_profile(self, customer_id):
        return self.db.one("SELECT * FROM risk_profiles WHERE customer_id=?", (customer_id,))

    def list_products(self):
        rows = self.db.rows("SELECT * FROM wealth_products ORDER BY id")
        if self.banking_version == 5:
            for row in rows:
                limits = self.db.one("SELECT purchase_enabled,redemption_enabled FROM product_restrictions WHERE product_id=?", (row["id"],))
                row["restrictions"] = limits or {"purchase_enabled": 0, "redemption_enabled": 0}
        return rows

    def create_transfer(self, action, action_hash, deadline):
        # Atomic bank-side check, journal, balance update and idempotency publication.
        with self.db.transaction() as db:
            if time.time() >= min(deadline, action["expires_at"]):
                raise Rejected("BANK_EXECUTION_EXPIRED")
            prior = db.execute("SELECT * FROM processed_idempotency_keys WHERE key=?", (action["idempotency_key"],)).fetchone()
            if prior:
                if prior["action_hash"] != action_hash:
                    raise Rejected("IDEMPOTENCY_CONFLICT")
                return dict(db.execute("SELECT * FROM transactions WHERE id=?", (prior["transaction_id"],)).fetchone())
            account = db.execute("SELECT * FROM accounts WHERE id=?", (action["source_account_id"],)).fetchone()
            payee = db.execute("SELECT * FROM payees WHERE id=? AND customer_id=?",
                               (action["recipient_id"], action["customer_id"])).fetchone()
            if not account or account["customer_id"] != action["customer_id"] or not payee:
                raise Rejected("BANK_INVALID_PARTIES")
            if self.banking_version == 5:
                contact = db.execute("SELECT status FROM payee_contacts WHERE payee_id=?", (payee["id"],)).fetchone()
                if contact and contact["status"] != "ACTIVE":
                    raise Rejected("BANK_PAYEE_INACTIVE")
            if account["frozen"]:
                raise Rejected("BANK_ACCOUNT_FROZEN")
            if account["currency"] != action["currency"] or account["balance"] < action["amount"]:
                raise Rejected("BANK_INSUFFICIENT_BALANCE")
            if account["version"] != action["preconditions"]["account_version"] or account["balance"] != action["preconditions"]["balance"]:
                raise Rejected("BANK_PRECONDITION_CHANGED")
            transfer_id, transaction_id = uid(), uid()
            db.execute("INSERT INTO transfers VALUES(?,?,?,'CREATED')", (transfer_id, action["idempotency_key"], action_hash))
            db.execute("UPDATE transfers SET status='AUTHORIZED' WHERE id=?", (transfer_id,))
            db.execute("UPDATE transfers SET status='PROCESSING' WHERE id=?", (transfer_id,))
            after = account["balance"] - action["amount"]
            route=db.execute("SELECT account_id FROM split_payee_routes WHERE payee_id=?",(payee["id"],)).fetchone()
            destination=route["account_id"] if route else "settlement"
            if route:
                receiver=db.execute("SELECT * FROM accounts WHERE id=?",(destination,)).fetchone()
                if not receiver or receiver["currency"]!=action["currency"] or receiver["frozen"] or destination==account["id"]:
                    raise Rejected("BANK_INVALID_RECEIVING_ACCOUNT")
            now = time.time()
            db.execute("UPDATE accounts SET balance=?,version=version+1 WHERE id=?", (after, account["id"]))
            db.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id=?", (action["amount"],destination))
            db.execute("INSERT INTO transactions VALUES(?,?,?,?,?,?,'SETTLED',?,?,?,?)",
                       (transaction_id, transfer_id, account["id"], payee["id"], action["amount"], action["currency"],
                        account["balance"], after, account["version"] + 1, now))
            db.execute("INSERT INTO ledger_entries VALUES(?,?,?,'DEBIT',?)", (uid(), transaction_id, account["id"], action["amount"]))
            db.execute("INSERT INTO ledger_entries VALUES(?,?,?,'CREDIT',?)", (uid(), transaction_id,destination, action["amount"]))
            db.execute("UPDATE transfers SET status='SETTLED' WHERE id=?", (transfer_id,))
            db.execute("INSERT INTO processed_idempotency_keys VALUES(?,?,?)", (action["idempotency_key"], action_hash, transaction_id))
            return dict(db.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone())
