"""Read models for the 账单与订阅 and 理财 pages. Nothing here writes to the bank: every action the pages offer
(cancel, purchase, redeem) is sent back into a conversation, where it goes through the normal confirmation card."""
import time
from statistics import median

DAY = 86400
FREQUENCY = {"MONTHLY": "每月", "WEEKLY": "每周", "YEARLY": "每年", "QUARTERLY": "每季度"}
PER_MONTH = {"MONTHLY": 1, "WEEKLY": 52 / 12, "YEARLY": 1 / 12, "QUARTERLY": 1 / 3}
MARK_STATES = {"NEW", "CONFIRMED", "DISMISSED"}


def ensure_tables(db):
    with db.transaction() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS recurring_marks(owner TEXT NOT NULL, key TEXT NOT NULL, state TEXT NOT NULL, "
                     "at REAL NOT NULL, PRIMARY KEY(owner, key))")


def recurring_key(row):
    return f"{row['merchant']}|{row['amount']}|{row['currency']}"


class BillsPage:
    def __init__(self, chat):
        self.chat, self.s = chat, chat.s
        ensure_tables(self.s.db)

    def _account(self, owner):
        row = self.s.db.one("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,))
        return row and row["id"]

    def marks(self, owner):
        return {r["key"]: r["state"] for r in self.s.db.rows("SELECT key, state FROM recurring_marks WHERE owner=?", (owner,))}

    def mark(self, owner, key, state):
        from backend.domain import Rejected
        if state not in MARK_STATES or not isinstance(key, str) or not key or len(key) > 200:
            raise Rejected("INVALID_INPUT")
        if key not in {d["key"] for d in self.detected(owner)}:
            raise Rejected("NOT_FOUND")
        # also used from conversation (SubscriptionTalk): the same record either way
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO recurring_marks VALUES(?,?,?,?) ON CONFLICT(owner, key) DO UPDATE SET state=excluded.state, at=excluded.at",
                         (owner, key, state, time.time()))
        return self.view(owner)

    def detected(self, owner):
        """Charges the bank has not registered as a subscription but that repeat: same merchant and amount, at least three
        times, 25–35 days apart. Found by the finance agent; the customer decides what they are."""
        account = self._account(owner)
        if not account:
            return []
        known = {r["merchant"] for kind in ("subscription", "direct_debit") for r in self.s.service_agent.resources(owner, kind)}
        history = {}
        for row in self.s.gateway.read("finance", owner, "transaction.recurring_history", account_id=account):
            if row["status"] == "SETTLED":
                history.setdefault(recurring_key(row), []).append(row["occurred_at"])
        marks, out = self.marks(owner), []
        for candidate in self.s.finance.recurring(owner, account):
            if candidate["merchant"] in known:
                continue
            key = recurring_key(candidate)
            times = sorted(history.get(key, []))
            gap = median(b - a for a, b in zip(times, times[1:])) if len(times) > 1 else 30 * DAY
            upcoming = times[-1] + gap if times else None
            while upcoming is not None and upcoming < time.time() - DAY:  # history older than one cycle: next one is later
                upcoming += gap
            out.append({"key": key, "merchant": candidate["merchant"], "amount_minor": candidate["amount"],
                        "currency": candidate["currency"], "count": len(times), "first_at": times[0] if times else None,
                        "last_at": times[-1] if times else None, "next_estimate": upcoming,
                        "interval_days": round(gap / DAY), "state": marks.get(key, "NEW"),
                        "evidence_refs": candidate["evidence_refs"]})
        return out

    def view(self, owner):
        now = time.time()
        items = []
        for kind, label in (("subscription", "订阅"), ("direct_debit", "代扣")):
            for r in self.s.service_agent.resources(owner, kind):
                items.append({"id": r["id"], "kind": kind, "kind_label": label, "merchant": r["merchant"], "amount_minor": r["amount"],
                              "currency": r["currency"], "frequency": r["frequency"], "frequency_label": FREQUENCY.get(r["frequency"], r["frequency"]),
                              "next_charge_at": r["next_charge_at"], "status": r["status"], "cancelable": bool(r["cancelable"]),
                              "monthly_minor": round(r["amount"] * PER_MONTH.get(r["frequency"], 1)) if r["status"] == "ACTIVE" else 0})
        from backend.chat.subscriptions_chat import user_subscriptions
        for u in user_subscriptions(self.s.db, owner):
            items.append({"id": u["id"], "kind": "user", "kind_label": "你登记的", "merchant": u["merchant"], "amount_minor": u["amount"],
                          "currency": "CNY", "frequency": u["frequency"], "frequency_label": FREQUENCY.get(u["frequency"], u["frequency"]),
                          "next_charge_at": None, "status": "ACTIVE", "cancelable": False, "removable": True,
                          "monthly_minor": round((u["amount"] or 0) * PER_MONTH.get(u["frequency"], 1))})
        detected = self.detected(owner)
        upcoming = [{"merchant": i["merchant"], "amount_minor": i["amount_minor"], "at": i["next_charge_at"], "kind_label": i["kind_label"],
                     "estimated": False} for i in items if i["status"] == "ACTIVE" and i["next_charge_at"] and now - DAY < i["next_charge_at"] <= now + 30 * DAY]
        for d in detected:
            if d["state"] == "CONFIRMED" and d["next_estimate"]:
                at = d["next_estimate"]
                if at <= now + 30 * DAY:
                    upcoming.append({"merchant": d["merchant"], "amount_minor": d["amount_minor"], "at": at, "kind_label": "识别的订阅",
                                     "estimated": True})
        upcoming.sort(key=lambda u: u["at"])
        monthly = sum(i["monthly_minor"] for i in items) + sum(d["amount_minor"] for d in detected if d["state"] == "CONFIRMED")
        recent = [i for i in self._ledger(owner) if i["kind"] == "subscription"][:10]
        return {"items": items, "detected": detected, "upcoming": upcoming, "monthly_minor": monthly, "yearly_minor": monthly * 12,
                "history": recent, "generated_at": now,
                "rule": "同一商户、同一金额，间隔 25–35 天扣费满 3 次，即识别为疑似订阅，由你确认是否计入；也可以在对话里直接说“QQ音乐是我每月续订的”。"}

    def _ledger(self, owner):
        from backend.chat.activity import ActivityFeed
        return ActivityFeed(self.chat).list(owner, "subscription", 20)


class WealthPage:
    def __init__(self, chat, market):
        self.chat, self.s, self.market = chat, chat.s, market

    def info(self):
        try:
            return {r["product_id"]: r for r in self.s.db.rows("SELECT * FROM wealth_product_info")}
        except Exception:
            return {}

    def view(self, owner):
        from backend.chat.risk import LEVELS
        from backend.domain import Rejected
        try:
            profile = self.s.wealth.risk_profile(owner)
            level = profile["risk_level"]
            risk = {"level": level, "label": LEVELS[level], "expires_at": profile["expires_at"]}
        except Rejected as error:
            level, risk = 0, {"level": None, "label": None, "reason": error.code}
        info = self.info()
        account = self.s.db.one("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,))
        products, holdings = [], []
        for p in self.s.wealth.products(owner):
            extra = info.get(p["id"], {})
            restrictions = p.get("restrictions") or {}
            suitable = p["active"] == 1 and p["currency"] == "CNY" and 0 < p["risk_level"] <= level
            why = ("已下架" if p["active"] != 1 else "需要先完成风险测评" if not level else
                   f"高于你的风险等级 R{level}，不能申购" if p["risk_level"] > level else None)
            products.append({"id": p["id"], "name": p["name"], "risk_level": p["risk_level"], "minimum_minor": p["minimum_amount"],
                             "issuer": extra.get("issuer", "本行"), "group": extra.get("product_group", extra.get("category", "理财")),
                             "term_days": p["term_days"], "currency": p["currency"], "active": p["active"] == 1,
                             "risk_note": p["risk_note"], "category": extra.get("category", "理财"),
                             "benchmark": extra.get("benchmark"), "liquidity": extra.get("liquidity"),
                             "description": extra.get("description"), "suitable": suitable, "blocked_reason": why,
                             "purchase_enabled": bool(restrictions.get("purchase_enabled", 1))})
            if account:
                held = self.s.gateway.read("wealth", owner, "portfolio.get", account_id=account["id"], product_id=p["id"])
                if held["amount_cents"] > 0:
                    holdings.append({"product_id": p["id"], "name": p["name"], "risk_level": p["risk_level"], "issuer": extra.get("issuer", "本行"),
                                     "category": extra.get("category", "理财"), "amount_minor": held["amount_cents"],
                                     "redeemable_at": held.get("redeemable_at"),
                                     "redeemable": (held.get("redeemable_at") or 0) <= time.time()})
        products = [p for p in products if p["active"]]
        products.sort(key=lambda p: (not p["suitable"], p["risk_level"], p["term_days"]))
        return {"risk": risk, "products": products, "holdings": holdings, "plan": self.plan(owner, level, products),
                "holdings_minor": sum(h["amount_minor"] for h in holdings), "market": self.market.view() if self.market else None,
                "policy": "只推荐本行在售、且风险等级不高于你测评结果的产品；不推荐个股，不承诺收益。"}

    # ---- personalised allocation -------------------------------------------------------------------
    # Share of the money left after the cash reserve, by risk level. Fixed rules, shown to the customer as-is.
    SPLIT = {1: {"稳健": 1.0}, 2: {"稳健": 1.0}, 3: {"稳健": 0.7, "增强": 0.3},
             4: {"稳健": 0.5, "增强": 0.3, "进取": 0.2}, 5: {"稳健": 0.4, "增强": 0.3, "进取": 0.3}}
    BUCKETS = {"备用金": "随时要用的钱", "稳健": "一年内不急用的钱", "增强": "能接受中等波动的部分", "进取": "长期不用、能承受较大波动的部分"}

    def _spending(self, owner, account):
        from datetime import datetime
        from backend.business import CHINA
        today = datetime.now(CHINA)
        months = []
        for back in (3, 2, 1):
            y, m = today.year, today.month - back
            while m <= 0:
                y, m = y - 1, m + 12
            months.append(f"{y}-{m:02d}")
        try:
            result = self.s.finance.query(owner, account, months, None, None, False)
        except Exception:
            return None, months
        used = [m for m in result.get("by_month", []) if m.get("settled_count")]
        if not used:
            return None, months
        return sum(m["net_spend"] for m in used) // len(used), [m["month"] for m in used]

    @staticmethod
    def _floor100(minor):
        return max(0, minor // 10000 * 10000)

    def plan(self, owner, level, products):
        """Allocation reference from the customer's own numbers: keep three months of spending and fixed charges
        within reach, split the rest by risk level, and pick products (different issuers where possible) that
        fit each part. Deterministic; every figure comes from the bank records listed in `basis`."""
        account = self.s.db.one("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,))
        if not account or not level:
            return None
        balance = self.s.transaction.balance(owner, account["id"])["balance"]
        spend, months = self._spending(owner, account["id"])
        fixed = BillsPage(self.chat).view(owner)["monthly_minor"]
        monthly = (spend or 0) + fixed
        reserve = min(balance, -(-monthly * 3 // 10000) * 10000) if monthly else min(balance, 500000)
        rest = self._floor100(balance - reserve)
        suitable = [p for p in products if p["suitable"] and p["purchase_enabled"]]
        tiers = {"备用金": [p for p in suitable if p["risk_level"] == 1 and p["term_days"] == 0],
                 "稳健": [p for p in suitable if p["risk_level"] <= 2 and 7 <= p["term_days"] <= 365],
                 "增强": [p for p in suitable if p["risk_level"] == 3],
                 "进取": [p for p in suitable if p["risk_level"] >= 4]}
        amounts = {"备用金": reserve} | {k: self._floor100(int(rest * v)) for k, v in self.SPLIT[level].items()}
        buckets = []
        for name, amount in amounts.items():
            if amount <= 0:
                continue
            picks, issuers = [], set()
            for p in sorted(tiers[name], key=lambda p: (p["minimum_minor"] > amount, -p["risk_level"] if name != "备用金" else 0, p["term_days"])):
                if p["minimum_minor"] <= amount and p["issuer"] not in issuers:
                    picks.append(p)
                    issuers.add(p["issuer"])
                if len(picks) == 2:
                    break
            if not picks:
                continue
            each = self._floor100(amount // len(picks))
            if any(each < p["minimum_minor"] for p in picks):
                picks, each = picks[:1], amount
            shares = [each] * (len(picks) - 1) + [amount - each * (len(picks) - 1)]  # the last one takes the remainder
            buckets.append({"name": name, "label": self.BUCKETS[name], "amount_minor": amount,
                            "items": [{"product_id": p["id"], "name": p["name"], "issuer": p["issuer"], "risk_level": p["risk_level"],
                                       "category": p["category"], "term_days": p["term_days"], "amount_minor": share}
                                      for p, share in zip(picks, shares)]})
        reasons = []
        if spend is not None:
            reasons.append(f"{months[0]} 至 {months[-1]} 平均每月消费 {spend / 100:,.2f} 元")
        if fixed:
            reasons.append(f"订阅和代扣每月固定扣 {fixed / 100:,.2f} 元")
        reasons.append(f"建议留 {reserve / 100:,.2f} 元（约 3 个月开销）放在随时可取的产品里" if monthly
                       else f"暂无消费记录，先按 {reserve / 100:,.2f} 元留作备用金")
        if rest > 0:
            split = self.SPLIT[level]
            share = "全部放稳健部分" if len(split) == 1 else "、".join(f"{k} {int(v * 100)}%" for k, v in split.items())
            reasons.append(f"其余 {rest / 100:,.2f} 元按你的风险等级 R{level}：{share}")
        return {"balance_minor": balance, "monthly_minor": monthly, "reserve_minor": reserve, "investable_minor": rest,
                "buckets": buckets, "reasons": reasons,
                "basis": ["活期余额", "近 3 个月已入账消费", "订阅与代扣", f"风险测评 R{level}"],
                "note": "这是按你的余额、消费和风险等级算出的配置参考，不是收益承诺；每笔申购仍需你在确认卡上核对。"}
