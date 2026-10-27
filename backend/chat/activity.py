"""资金总览与记录: one owner-scoped timeline over everything that changed money or account state.

Sources (all already recorded elsewhere; nothing new is trusted here):
* six-agent tasks → every action record with its policy tier, final status and signed receipt,
  plus the provenance of the request (which conversation / message, or which automation rule);
* sandbox feed → incoming credits (payer and memo are third-party text; quarantined ones are masked);
* automation firings.
Pending, never-decided proposals are left out; declined ones are listed so the customer sees what was refused.
"""
import json
import time
from backend.chat.amounts import format_cents
from backend.chat.cards import CardRenderer, OPERATIONS

KINDS = {"CARD_CHARGE":"credit","CARD_REFUND":"credit","CARD_REPAY":"credit","CARD_SURPLUS":"credit","FUND_SUBSCRIBE":"wealth","FUND_CONFIRM":"wealth","FUND_CANCEL":"wealth","FUND_REDEEM":"wealth","DEPOSIT_":"wealth","FX_":"wealth","LOAN_DRAW":"credit","INSTALLMENT_START":"credit","CREDIT_":"credit","GOLD_":"wealth", "TRANSFER": "transfer", "CARD_": "card", "INVESTMENT_": "wealth", "RISK_PROFILE_UPDATE": "wealth", "FUND_": "wish",
         "RETAIL_PAY":"retail", "RETAIL_REFUND":"retail", "WISH_ORDER": "wish", "SUBSCRIPTION_CANCEL": "subscription", "DIRECT_DEBIT_DISABLE": "subscription", "ADD_PAYEE": "payee"}
FINAL = {"DENIED": "DECLINED", "COMPLETED": "SUCCEEDED", "SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "BLOCKED": "BLOCKED", "REJECTED": "DECLINED",
         "CANCELLED": "CANCELLED", "SCHEDULED": "SCHEDULED", "EXPIRED": "EXPIRED"}
STATUS_ZH = {"SUCCEEDED": "已完成", "FAILED": "失败", "BLOCKED": "被拦截", "DECLINED": "已拒绝", "CANCELLED": "已取消",
             "SCHEDULED": "已排期", "EXPIRED": "已过期", "RECEIVED": "已到账", "TRIGGERED": "已触发"}


def kind_of(action_type):
    return next((v for k, v in KINDS.items() if action_type == k or (k.endswith("_") and action_type.startswith(k))), "other")


class ActivityFeed:
    def __init__(self, chat):
        self.chat, self.s = chat, chat.s

    def _records(self, task):
        if "action_records" in task:
            for entry in task["action_records"]:
                yield entry["action"], entry.get("status"), entry.get("receipt"), (entry.get("policy") or {}).get("risk_level")
        else:
            for action in task.get("proposed_actions") or []:
                yield action, task.get("status"), task.get("receipt"), (task.get("policy_decision") or {}).get("risk_level")

    def list(self, owner, kind=None, limit=200, target=None):
        catalog = self.chat.catalog(owner)
        renderer = CardRenderer(self.s, owner, catalog)
        items = []
        for task in self.s.blackboard.list(owner):
            origin = (((task.get("context") or {}).get("normalized") or {}).get("origin")) or {}
            for action, status, receipt, tier in self._records(task):
                final = FINAL.get(status)
                if not final:
                    continue  # still waiting for the customer: shown in the conversation, not in the ledger
                if target and (action.get("target") or {}).get("id") != target:
                    continue
                item_kind = kind_of(action["action_type"])
                if kind and kind != item_kind:
                    continue
                try:
                    fields = renderer.action_fields(action)
                except Exception:
                    fields = []
                amount = next((f["amount_minor"] for f in fields if "amount_minor" in f), action.get("amount"))
                items.append({"id": action["action_id"], "kind": item_kind, "action_type": action["action_type"],
                              "title": OPERATIONS.get(action["action_type"], action["action_type"]),
                              "fields": fields, "amount_minor": amount,
                              "direction": "in" if action["action_type"] in {"FUND_CANCEL","FUND_REDEEM","CARD_SURPLUS","DEPOSIT_WITHDRAW","DEPOSIT_RENEW","FX_SELL","LOAN_DRAW","INSTALLMENT_START","GOLD_SELL", "INVESTMENT_REDEEM", "FUND_RELEASE", "RETAIL_REFUND"} else
                                           "out" if action["action_type"] in {"FUND_SUBSCRIBE","CARD_REPAY","DEPOSIT_OPEN","FX_BUY","CREDIT_REPAY","CREDIT_SETTLE","GOLD_BUY", "TRANSFER", "INVESTMENT_PURCHASE", "FUND_LOCK", "RETAIL_PAY"} else None,
                              "status": final, "status_label": STATUS_ZH[final], "risk_level": tier,
                              "at": (receipt or {}).get("executed_at") or action.get("created_at") or task.get("updated_at"),
                              "task_id": task["task_id"], "receipt_id": (receipt or {}).get("receipt_id"),
                              "verified": (receipt or {}).get("verification_status") == "VERIFIED" if receipt else None,
                              "target_id": (action.get("target") or {}).get("id"),
                              "source": {"session_id": origin.get("session_id"), "message_id": origin.get("message_id"),
                                         "channel": origin.get("channel"), "rule_id": origin.get("rule_id")}})
        if (not kind or kind == "income") and not target and getattr(self.s, "feed", None):
            for credit in self.s.feed.incoming(owner, 0, 500):
                bad = credit["quarantine"]
                items.append({"id": credit["id"], "kind": "income", "action_type": "INCOMING_CREDIT", "title": "入账",
                              "fields": [{"label": "付款方", "value": "[可疑内容已隔离]" if bad else credit["payer"]},
                                         {"label": "备注", "value": "[可疑内容已隔离]" if bad else (credit["memo"] or "—")}],
                              "amount_minor": credit["amount"], "direction": "in", "status": "RECEIVED", "status_label": "已到账",
                              "risk_level": None, "at": credit["occurred_at"], "quarantined": bad, "source": {}})
        if (not kind or kind == "automation") and not target:
            for row in self.s.db.rows("SELECT body FROM automation_firings WHERE owner=? ORDER BY created_at DESC LIMIT 200", (owner,)):
                firing = json.loads(row["body"])
                items.append({"id": firing["id"], "kind": "automation", "action_type": "AUTOMATION_FIRED",
                              "title": "联动规则触发：" + firing["rule_name"], "fields": [{"label": "条件", "value": firing["fact"]["detail"]}],
                              "amount_minor": None, "direction": None, "status": "TRIGGERED", "status_label": "已触发",
                              "risk_level": None, "at": firing["created_at"], "source": {"rule_id": firing["rule_id"]}})
        items.sort(key=lambda i: i["at"] or 0, reverse=True)
        return items[:limit]

    # ---- overview -------------------------------------------------------------------------------
    def overview(self, owner):
        from datetime import datetime
        from backend.business import CHINA
        s, now = self.s, time.time()
        accounts = []
        for row in s.db.rows("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,)):
            balance = s.transaction.balance(owner, row["id"])
            accounts.append({"id": row["id"], "name": "储蓄账户" if "saving" in row["id"] else "活期账户",
                             "balance_minor": balance["balance"], "currency": balance["currency"]})
        today = datetime.fromtimestamp(now, CHINA)
        month = today.strftime("%Y-%m")
        last = f"{today.year - (today.month == 1)}-{(today.month - 2) % 12 + 1:02d}"
        spend = {"month": month, "total_minor": 0, "count": 0, "categories": [], "previous_minor": None, "current": True}
        for candidate in (month, last):  # early in a month there may be nothing yet: show last month instead of an empty card
            try:
                spending = s.finance.query(owner, accounts[0]["id"], [candidate], None, None, True) if accounts else None
            except Exception:
                spending = None
            if spending and spending["summary"]["settled_count"]:
                spend = {"month": candidate, "total_minor": spending["summary"]["net_spend"], "count": spending["summary"]["settled_count"],
                         "categories": spending["categories"][:5], "current": candidate == month,
                         "previous_minor": (spending.get("comparison") or {}).get("previous_net_spend")}
                break
        positions = []
        for product in s.wealth.products(owner):
            if accounts:
                holdings=[s.gateway.read("wealth",owner,"portfolio.get",account_id=a["id"],product_id=product["id"]) for a in accounts]
                held={"amount_cents":sum(h["amount_cents"] for h in holdings),"redeemable_at":max((h.get("redeemable_at") or 0 for h in holdings),default=0)}
                if held["amount_cents"] > 0:
                    positions.append({"product_id": product["id"], "name": product["name"], "risk_level": product["risk_level"],
                                      "amount_minor": held["amount_cents"], "redeemable_at": held.get("redeemable_at")})
        funds = []
        for fund in s.service_agent.resources(owner, "wish_fund"):
            orders = [i for i in self.list(owner, "wish", 50, fund["id"])]
            funds.append({"id": fund["id"], "name": fund["name"], "balance_minor": fund["balance"], "target_minor": fund["target_amount"],
                          "release_at": fund["release_at"], "status": fund["status"], "events": orders})
        cards = [{"id": c["id"], "type": c["card_type"], "status": c["status"], "limit_minor": c["spending_limit"],
                  "online": bool(c["online_enabled"]), "international": bool(c["international_enabled"]),
                  "contactless": bool(c["contactless_enabled"])} for c in s.service_agent.resources(owner, "card")]
        for card in cards:
            row=s.db.one('SELECT c.account_id,a.balance FROM cards c JOIN accounts a ON a.id=c.account_id WHERE c.id=? AND c.customer_id=?',(card['id'],owner))
            card.update(account_id=row['account_id'],balance=row['balance'],design=s.db.one('SELECT theme,nickname FROM card_designs WHERE card_id=? AND customer_id=?',(card['id'],owner)) or {'theme':'midnight','nickname':''})
        engine = getattr(s, "automation", None)
        rules = [{"id": r["id"], "name": r["name"], "trigger": engine.describe_trigger(r["trigger"]), "runs": r["runs"]}
                 for r in engine.rules(owner)] if engine else []
        from backend.banking.finance import contracts
        with s.db.transaction() as conn: financing=contracts(conn,owner)
        gold=s.db.rows('SELECT units,cost FROM gold_positions WHERE customer_id=?',(owner,))
        active=[t for t in financing if t['status']=='ACTIVE' and t['type'] in {'loan','installment'}]
        pending=sorted((p for t in active for p in t['schedule'] if not p['paid']),key=lambda p:p['due'])
        finance={'debt_minor':sum(t['remaining'] for t in active), 'gold_units':sum(p['units'] for p in gold),
                 'gold_cost_minor':sum(p['cost'] for p in gold), 'next_due':pending[0]['due'] if pending else None,
                 'next_due_minor':sum(p['total'] for p in pending if p['due']==pending[0]['due']) if pending else 0}
        finance['deposit_principal_minor']=sum(json.loads(r['body'])['principal'] for r in s.db.rows("SELECT body FROM term_deposits WHERE customer_id=?",(owner,)) if json.loads(r['body'])['status']=='ACTIVE')
        finance['fx_cost_minor']=sum(r['cost'] for r in s.db.rows('SELECT cost FROM fx_wallets WHERE customer_id=?',(owner,)))
        finance['assets_at_cost_minor']=finance['deposit_principal_minor']+finance['fx_cost_minor']+sum(a['balance_minor'] for a in accounts)+sum(p['amount_minor'] for p in positions)+sum(f['balance_minor'] for f in funds)+finance['gold_cost_minor']
        from backend.banking.advanced import debt
        advanced=[json.loads(r['body']) for r in s.db.rows('SELECT body FROM advanced_positions WHERE customer_id=?',(owner,))]
        finance['credit_card_debt_minor']=sum(max(0,debt(v)) for v in advanced)
        finance['fund_cost_minor']=sum(sum(h['cost'] for h in v['holdings'].values())+sum(o['amount'] for o in v['orders'] if o['status']=='PENDING') for v in advanced)
        finance['assets_at_cost_minor']+=finance['fund_cost_minor']+sum(max(0,-debt(v)) for v in advanced)
        finance['debt_minor']+=finance['credit_card_debt_minor']
        finance['net_at_cost_minor']=finance['assets_at_cost_minor']-finance['debt_minor']
        protection = s.safety.protection(owner)
        return {"finance":finance, "accounts": accounts, "total_minor": sum(a["balance_minor"] for a in accounts), "spending": spend,
                "portfolio": positions, "portfolio_minor": sum(p["amount_minor"] for p in positions), "wish_funds": funds,
                "cards": cards, "rules": rules, "protection": protection and {"until": protection["until"], "reason": protection["reason"]},
                "recent": self.list(owner, limit=8), "generated_at": now}
