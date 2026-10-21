from backend.domain import Rejected, canonical, digest, uid
import json


class ReceiptService:
    def __init__(self, db, audit):
        self.db, self.audit = db, audit

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
        return receipt
