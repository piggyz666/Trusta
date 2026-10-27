from backend.domain import Rejected, canonical, digest, uid
import json


class ReceiptService:
    def __init__(self, db, audit):
        self.db, self.audit = db, audit

    @staticmethod
    def _remember(db, action):
        """TRUSTA's own record of what it executed, so its safety checks and plan budgets never depend on reading the
        bank's internal tables (which a real bank does not expose)."""
        kind = action.get("action_type")
        if kind == "ADD_PAYEE":
            payee = (action.get("expected_postconditions") or {}).get("target") or {}
            payee_id = payee.get("id") or action["target"]["id"]
            db.execute("CREATE TABLE IF NOT EXISTS payee_additions(payee_id TEXT NOT NULL, customer_id TEXT NOT NULL, created_at REAL NOT NULL, "
                       "PRIMARY KEY(payee_id, customer_id))")
            db.execute("INSERT OR IGNORE INTO payee_additions VALUES(?,?,?)", (payee_id, action["customer_id"], action.get("created_at") or 0))
        elif kind == "WISH_ORDER":
            binding = db.execute("SELECT plan_id FROM life_plan_bindings WHERE fund_id=? AND owner=?", (action["target"]["id"], action["customer_id"])).fetchone() \
                if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='life_plan_bindings'").fetchone() else None
            if binding:
                db.execute("CREATE TABLE IF NOT EXISTS plan_spending(action_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, owner TEXT NOT NULL, amount INTEGER NOT NULL)")
                db.execute("INSERT OR IGNORE INTO plan_spending VALUES(?,?,?,?)", (action["action_id"], binding["plan_id"], action["customer_id"], action["parameters"]["amount"]))

    def create(self, action, contract, transaction, verification):
        if verification["status"] != "VERIFIED":
            raise Rejected("UNVERIFIED_RECEIPT")
        receipt = {"receipt_id": uid(), "task_id": action["task_id"], "action_id": action["action_id"],
                   "bank_transaction_id": transaction["id"], "approved_action_hash": digest(action),
                   "policy_decision_id": contract["policy_decision_id"], "authorization_id": contract["authorization_id"],
                   "pre_state_hash": verification["pre_state_hash"], "post_state_hash": verification["post_state_hash"],
                   "verification_status": verification["status"], "executed_at": verification.get("executed_at") or transaction.get("executed_at")}
        if receipt["executed_at"] is None:
            raise Rejected("UNVERIFIED_SETTLEMENT_TIME")
        with self.db.transaction() as db:
            prior=db.execute("SELECT body FROM receipts WHERE json_extract(body,'$.action_id')=? AND json_extract(body,'$.bank_transaction_id')=?",
                             (action['action_id'],transaction['id'])).fetchall()
            if prior:
                if len(prior)!=1:raise Rejected('RECEIPT_BINDING_MISMATCH')
                existing=json.loads(prior[0]['body'])
                if any(existing.get(k)!=v for k,v in receipt.items() if k!='receipt_id'):
                    raise Rejected('RECEIPT_BINDING_MISMATCH')
                return existing
            receipt['audit_ref']=self.audit.append_in(db,action['task_id'],action['action_id'],'RECEIPT_GENERATED','receipts',receipt)
            db.execute("INSERT INTO receipts VALUES(?,?)", (receipt["receipt_id"], canonical(receipt)))
            self._remember(db, action)
        return receipt
