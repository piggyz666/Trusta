"""Authoritative sandbox domain transactions, called only by the bank adapter."""
import json
import time
from datetime import datetime
from backend.business import CHINA
from backend.domain import Rejected, canonical, digest, uid
from backend.banking.actions import domain_errors, expected_state, risk_target

RESOURCE_TABLES = {"card": "cards", "card_application":"card_applications", "subscription": "subscriptions", "direct_debit": "direct_debits"}
WISH_POOL = "wish-pool"  # internal ledger account holding earmarked (locked) customer funds


def seed_extended(conn):
    conn.execute("INSERT OR IGNORE INTO accounts VALUES('investment-clearing',NULL,'CNY',0,0,0)")
    conn.execute("INSERT OR IGNORE INTO accounts VALUES('acct-savings','demo','CNY',500000,0,0)")
    conn.execute("INSERT OR IGNORE INTO payee_contacts VALUES('payee-zhang','13800000001','房东','ACTIVE')")
    conn.execute("INSERT OR IGNORE INTO payee_contacts VALUES('payee-li','13800000002','同事','ACTIVE')")
    conn.execute("INSERT OR IGNORE INTO cards VALUES('card-demo','demo','acct-demo','DEBIT','ACTIVE',500000,1,0,1,?,0)", (time.time(),))
    for table, rows in (("subscriptions", [("sub-stream", "演示视频", 1999), ("sub-cloud", "演示云盘", 990)]),
                        ("direct_debits", [("dd-electric", "演示电力", 12000)])):
        for resource_id, merchant, amount in rows:
            conn.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?,'demo','acct-demo',?,'CNY','MONTHLY',?,'ACTIVE',1,'BANK_SANDBOX',0)",
                         (resource_id, merchant, amount, datetime(2026, 11, 1, tzinfo=CHINA).timestamp()))
    # Independent historic bank charge feed, not new deductions from today's balance.
    for merchant, amount in (("演示视频", 1999), ("演示云盘", 990), ("未登记商户", 1500)):
        for month in (7, 8, 9):
            conn.execute("INSERT OR IGNORE INTO recurring_charges VALUES(?,'demo','acct-demo',?,?,'CNY',?,'SETTLED')",
                         (f"rec-{merchant}-{month}", merchant, amount, datetime(2026, month, 1, tzinfo=CHINA).timestamp()))
    conn.execute("INSERT OR IGNORE INTO product_restrictions SELECT id,1,1 FROM wealth_products")
    conn.execute(f"INSERT OR IGNORE INTO accounts VALUES('{WISH_POOL}',NULL,'CNY',0,0,0)")
    for row in (("m-flower", "演示花店", "FLOWERS", "生日花束", 29900), ("m-cake", "演示蛋糕店", "CAKE", "生日蛋糕（6寸）", 19900),
                ("m-gift", "演示礼品店", "GIFT", "礼品卡", 50000)):
        conn.execute("INSERT OR IGNORE INTO merchants VALUES(?,?,?,?,?,'ACTIVE')", row)
    conn.execute("INSERT OR IGNORE INTO life_events VALUES('le-spouse-birthday','demo','王芳','爱人','BIRTHDAY',11,15)")
    conn.execute("INSERT OR IGNORE INTO life_events VALUES('le-mother-birthday','demo','李秀英','妈妈','BIRTHDAY',3,8)")


class ExtendedBank:
    def __init__(self, db):
        self.db = db

    def resource(self, customer, kind, resource_id=None):
        if kind=='card_application' and resource_id:
            with self.db.transaction() as conn:
                return self.application(conn,customer,resource_id)
        if kind == 'wish_fund':
            with self.db.transaction() as conn:
                if resource_id:
                    return self.wish_fund(conn, customer, resource_id)
                return [dict(r) for r in conn.execute("SELECT * FROM wish_funds WHERE customer_id=? ORDER BY id", (customer,)).fetchall()]
        if kind in {'payee_entry', 'payee_contact'}:
            with self.db.transaction() as conn:
                return self.payee_entry(conn, customer, resource_id) if kind == 'payee_entry' else self.payee_contacts(conn, customer)
        if kind == 'merchant':
            return self.db.rows("SELECT * FROM merchants WHERE status='ACTIVE' ORDER BY id")
        if kind == 'life_event':
            return self.db.rows("SELECT * FROM life_events WHERE customer_id=? ORDER BY month,day,id", (customer,))
        table = RESOURCE_TABLES[kind]
        rows = self.db.rows(f"SELECT * FROM {table} WHERE customer_id=?" + (" AND id=?" if resource_id else "") + " ORDER BY id",
                            (customer, resource_id) if resource_id else (customer,))
        if resource_id:
            if not rows:
                raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
            return rows[0]
        return rows

    @staticmethod
    def application(conn,customer,account_id):
        if not conn.execute('SELECT id FROM accounts WHERE id=? AND customer_id=?',(account_id,customer)).fetchone():
            raise Rejected('ACCOUNT_ACCESS_DENIED')
        row=conn.execute('SELECT * FROM card_applications WHERE id=? AND customer_id=?',(account_id,customer)).fetchone()
        return dict(row) if row else {'id':account_id,'customer_id':customer,'account_id':account_id,
                                     'card_type':'VIRTUAL_DEBIT','status':'AVAILABLE','version':0}

    @staticmethod
    def wish_fund(conn, customer, fund_id, account_id=None):
        """A not-yet-created fund has a deterministic AVAILABLE placeholder (same pattern as card applications)."""
        if not isinstance(fund_id, str) or not fund_id.startswith("wish-") or not 6 <= len(fund_id) <= 40:
            raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
        row = conn.execute("SELECT * FROM wish_funds WHERE id=?", (fund_id,)).fetchone()
        if row:
            if row["customer_id"] != customer:
                raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
            return dict(row)
        if account_id is None:
            owned = conn.execute("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (customer,)).fetchall()
            account_id = "acct-demo" if any(r["id"] == "acct-demo" for r in owned) else (owned[0]["id"] if owned else "")
        return {"id": fund_id, "customer_id": customer, "account_id": account_id, "name": "", "balance": 0,
                "target_amount": 0, "release_at": 0.0, "status": "AVAILABLE", "version": 0}

    @staticmethod
    def payee_entry(conn, customer, payee_id):
        if not isinstance(payee_id, str) or not payee_id.startswith("payee-") or not 7 <= len(payee_id) <= 40:
            raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
        row = conn.execute("SELECT p.id,p.customer_id,p.name,p.is_new,c.phone,c.alias,c.status FROM payees p "
                           "LEFT JOIN payee_contacts c ON c.payee_id=p.id WHERE p.id=?", (payee_id,)).fetchone()
        if row:
            if row["customer_id"] != customer:
                raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
            return {"id": row["id"], "customer_id": customer, "name": row["name"], "is_new": row["is_new"],
                    "phone": row["phone"] or "", "alias": row["alias"] or "", "status": row["status"] or "ACTIVE", "version": 1}
        return {"id": payee_id, "customer_id": customer, "name": "", "is_new": 1, "phone": "", "alias": "", "status": "AVAILABLE", "version": 0}

    @staticmethod
    def payee_contacts(conn, customer):
        return [{"payee_id": r["payee_id"], "phone": r["phone"], "alias": r["alias"]} for r in conn.execute(
            "SELECT c.payee_id,c.phone,c.alias FROM payee_contacts c JOIN payees p ON p.id=c.payee_id WHERE p.customer_id=? "
            "ORDER BY c.payee_id", (customer,)).fetchall()]

    @staticmethod
    def _position(conn, customer, account_id, product_id):
        row = conn.execute("SELECT * FROM portfolio WHERE customer_id=? AND account_id=? AND product_id=?",
                           (customer, account_id, product_id)).fetchone()
        return dict(row) if row else {"customer_id": customer, "account_id": account_id, "product_id": product_id,
                                     "amount_cents": 0, "redeemable_at": 0.0, "version": 0}

    def portfolio(self, customer, account_id, product_id):
        with self.db.transaction() as conn:
            account = conn.execute("SELECT customer_id FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not account or account["customer_id"] != customer:
                raise Rejected("ACCOUNT_ACCESS_DENIED")
            return self._position(conn, customer, account_id, product_id)

    @staticmethod
    def snapshot(conn, action):
        customer, account_id, target_id = action["customer_id"], action["source_account_id"], action["target"]["id"]
        account = conn.execute("SELECT * FROM accounts WHERE id=? AND customer_id=?", (account_id, customer)).fetchone()
        if not account:
            raise Rejected("ACCOUNT_ACCESS_DENIED")
        kind = action["target"]["kind"]
        if kind == 'wish_fund':
            target = ExtendedBank.wish_fund(conn, customer, target_id, account_id)
            if target["account_id"] != account_id:
                raise Rejected("RESOURCE_ACCOUNT_MISMATCH")
            merchants = [dict(r) for r in conn.execute("SELECT * FROM merchants WHERE status='ACTIVE' ORDER BY id").fetchall()]
            return {"account": dict(account), "target": target, "merchants": merchants}
        if kind == 'payee_entry':
            return {"account": dict(account), "target": ExtendedBank.payee_entry(conn, customer, target_id),
                    "contacts": ExtendedBank.payee_contacts(conn, customer)}
        if kind == 'risk_profile':
            if target_id != customer:
                raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
            row = conn.execute("SELECT * FROM risk_profiles WHERE customer_id=?", (customer,)).fetchone()
            return {"account": dict(account), "target": risk_target(dict(row) if row else None, customer)}
        if kind=='card_application':
            if target_id!=account_id: raise Rejected('RESOURCE_ACCOUNT_MISMATCH')
            return {'account':dict(account),'target':ExtendedBank.application(conn,customer,target_id)}
        if kind in RESOURCE_TABLES:
            row = conn.execute(f"SELECT * FROM {RESOURCE_TABLES[kind]} WHERE id=? AND customer_id=? AND account_id=?",
                               (target_id, customer, account_id)).fetchone()
            if not row:
                raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
            return {"account": dict(account), "target": dict(row)}
        product = conn.execute("SELECT * FROM wealth_products WHERE id=?", (target_id,)).fetchone()
        risk = conn.execute("SELECT * FROM risk_profiles WHERE customer_id=?", (customer,)).fetchone()
        restriction = conn.execute("SELECT purchase_enabled,redemption_enabled FROM product_restrictions WHERE product_id=?", (target_id,)).fetchone()
        return {"account": dict(account), "target": dict(product) if product else None,
                "risk_profile": dict(risk) if risk else None, "portfolio": ExtendedBank._position(conn, customer, account_id, target_id),
                "restrictions": dict(restriction) if restriction else {"purchase_enabled": 0, "redemption_enabled": 0}}

    def operation(self, customer, operation_id):
        row = self.db.one("SELECT body FROM bank_operations WHERE id=? AND customer_id=?", (operation_id, customer))
        return json.loads(row["body"]) if row else None

    def apply_action(self, action, action_hash, deadline):
        with self.db.transaction() as conn:
            if time.time() >= min(deadline, action["expires_at"]):
                raise Rejected("BANK_EXECUTION_EXPIRED")
            previous = conn.execute("SELECT action_hash,body FROM bank_operations WHERE idempotency_key=?", (action["idempotency_key"],)).fetchone()
            if previous:
                if previous["action_hash"] != action_hash:
                    raise Rejected("IDEMPOTENCY_CONFLICT")
                return json.loads(previous["body"])
            before = self.snapshot(conn, action)
            if digest(before) != digest(action["preconditions"]):
                raise Rejected("BANK_PRECONDITION_CHANGED")
            errors = domain_errors(action["action_type"], before, action["parameters"])
            if errors:
                raise Rejected(errors[0])
            after = expected_state(action["action_type"], before, action["parameters"], action["expires_at"])
            if digest(after) != digest(action["expected_postconditions"]):
                raise Rejected("BANK_POSTCONDITION_INVALID")
            operation_id, now = uid(), time.time()
            kind = action["target"]["kind"]
            if kind == "wish_fund":
                self._apply_wish_fund(conn, action, before, after, operation_id)
            elif kind == "payee_entry":
                t = after["target"]
                conn.execute("INSERT INTO payees VALUES(?,?,?,1)", (t["id"], t["customer_id"], t["name"]))
                conn.execute("INSERT INTO payee_contacts VALUES(?,?,?,'ACTIVE')", (t["id"], t["phone"], t["alias"]))
                conn.execute("INSERT INTO payee_registrations VALUES(?,?,?)", (t["id"], t["customer_id"], now))
            elif kind == "risk_profile":
                t = after["target"]
                conn.execute("INSERT INTO risk_profiles VALUES(?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET risk_level=excluded.risk_level,"
                             "assessed_at=excluded.assessed_at,expires_at=excluded.expires_at",
                             (t["customer_id"], t["risk_level"], t["assessed_at"], t["expires_at"]))
            elif kind in RESOURCE_TABLES:
                target = after["target"]
                if kind=='card_application':
                    conn.execute('INSERT INTO card_applications VALUES(?,?,?,?,?,?)',tuple(target[key] for key in
                                 ('id','customer_id','account_id','card_type','status','version')))
                elif kind == "card":
                    conn.execute("UPDATE cards SET status=?,spending_limit=?,online_enabled=?,international_enabled=?,contactless_enabled=?,version=? WHERE id=?",
                                 tuple(target[key] for key in ("status", "spending_limit", "online_enabled", "international_enabled", "contactless_enabled", "version", "id")))
                    if action["action_type"] == "CARD_PIN_RESET":
                        # A one-time out-of-band reset link; the PIN itself never enters TRUSTA or the model.
                        conn.execute("INSERT INTO card_pin_resets VALUES(?,?,?,?,?,?)",
                                     (operation_id, target["id"], action["customer_id"], "SMS_LINK", now + 900, "SENT"))
                else:
                    conn.execute(f"UPDATE {RESOURCE_TABLES[kind]} SET status=?,version=? WHERE id=?", (target["status"], target["version"], target["id"]))
            else:
                account, position = after["account"], after["portfolio"]
                amount = action["parameters"]["amount"]
                purchase = action["action_type"] == "INVESTMENT_PURCHASE"
                conn.execute("UPDATE accounts SET balance=?,version=? WHERE id=?", (account["balance"], account["version"], account["id"]))
                clearing = conn.execute("SELECT balance FROM accounts WHERE id='investment-clearing'").fetchone()["balance"]
                if not purchase and clearing < amount:
                    raise Rejected("INVESTMENT_CLEARING_INSUFFICIENT")
                conn.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id='investment-clearing'", (amount if purchase else -amount,))
                conn.execute("INSERT INTO portfolio VALUES(?,?,?,?,?,?) ON CONFLICT(customer_id,account_id,product_id) DO UPDATE SET amount_cents=excluded.amount_cents,redeemable_at=excluded.redeemable_at,version=excluded.version",
                             tuple(position[key] for key in ("customer_id", "account_id", "product_id", "amount_cents", "redeemable_at", "version")))
                for account_id, side in ((account["id"], "DEBIT" if purchase else "CREDIT"), ("investment-clearing", "CREDIT" if purchase else "DEBIT")):
                    conn.execute("INSERT INTO ledger_entries VALUES(?,?,?,?,?)", (uid(), operation_id, account_id, side, amount))
            record = {"id": operation_id, "action_id": action["action_id"], "action_type": action["action_type"],
                      "customer_id": action["customer_id"], "source_account_id": action["source_account_id"],
                      "target": action["target"], "parameters": action["parameters"], "action_hash": action_hash,
                      "before": before, "after": after, "status": "SETTLED", "executed_at": now}
            conn.execute("INSERT INTO bank_operations VALUES(?,?,?,?,?,?)", (operation_id, action["customer_id"], action["action_type"], action_hash, action["idempotency_key"], canonical(record)))
            if kind == "product":
                conn.execute("INSERT INTO investment_transactions VALUES(?,?,?,?,?,?,?,?,?)", (operation_id, action["customer_id"], action["source_account_id"],
                             action["target"]["id"], "PURCHASE" if purchase else "REDEEM", amount, "CNY", "SETTLED", now))
            return record

    @staticmethod
    def _apply_wish_fund(conn, action, before, after, operation_id):
        fund, account = after["target"], after["account"]
        kind, amount = action["action_type"], action["parameters"].get("amount")
        if kind == "FUND_LOCK":
            conn.execute("INSERT INTO wish_funds VALUES(?,?,?,?,?,?,?,?,?)", tuple(fund[k] for k in (
                "id", "customer_id", "account_id", "name", "balance", "target_amount", "release_at", "status", "version")))
            moves = ((account["id"], "DEBIT"), (WISH_POOL, "CREDIT"))
        else:
            conn.execute("UPDATE wish_funds SET balance=?,status=?,version=? WHERE id=? AND version=?",
                         (fund["balance"], fund["status"], fund["version"], fund["id"], before["target"]["version"]))
            if kind == "FUND_RELEASE":
                amount = before["target"]["balance"]
                moves = ((WISH_POOL, "DEBIT"), (account["id"], "CREDIT"))
            else:  # WISH_ORDER pays the merchant's settlement account from the earmarked pool
                moves = ((WISH_POOL, "DEBIT"), ("settlement", "CREDIT"))
        if kind != "WISH_ORDER":
            conn.execute("UPDATE accounts SET balance=?,version=? WHERE id=?", (account["balance"], account["version"], account["id"]))
        if amount:
            pool = conn.execute("SELECT balance FROM accounts WHERE id=?", (WISH_POOL,)).fetchone()["balance"]
            delta = amount if kind == "FUND_LOCK" else -amount
            if pool + delta < 0:
                raise Rejected("WISH_POOL_INCONSISTENT")
            conn.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id=?", (delta, WISH_POOL))
            if kind == "WISH_ORDER":
                conn.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id='settlement'", (amount,))
            for account_id, side in moves:
                conn.execute("INSERT INTO ledger_entries VALUES(?,?,?,?,?)", (uid(), operation_id, account_id, side, amount))
