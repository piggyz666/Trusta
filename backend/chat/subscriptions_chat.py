"""Subscriptions in conversation.

* Recurring charges the bank has not registered are found by the finance agent (≥3 times, 25–35 days apart). They are
  never announced before the customer has said anything: the question "is this something you renew every month?" is
  asked once, after a reply about spending, bills or subscriptions.
* A plain "是" answers that question and records it as the customer's subscription; "不是/不认识" gets stop-loss advice.
* The customer can also simply say "QQ音乐是我每月续订的" / "把网易云音乐加到订阅，每月15": it is recorded straight away.

Recording a subscription is bookkeeping inside TRUSTA (reminders, monthly totals). It moves no money and is not a bank
registration, so it needs no confirmation card; cancelling a third-party renewal still has to happen in that app.
"""
import re
import time
import uuid

TOPIC = re.compile(r"花|消费|支出|开销|账单|订阅|会员|续费|续订|扣费|扣了|扣款|代扣|流水")
TOPIC_TOOLS = {"analyze_spending", "query_spending", "list_subscriptions", "list_direct_debits", "renewal_reminders",
               "review_subscriptions"}
YES = re.compile(r"^(是|是的|对|对的|嗯|嗯嗯|没错|是我的|是我订的|是订阅|是会员|对，?是的?)[。.!！~～]*$")
NO = re.compile(r"^(不是|不是的|没有|不对|不认识|不知道|不是我的|没订过)[。.!！~～]*$")
DECLARE = re.compile(r"(每月|每个月|按月|包月|连续包月|每年|按年|年费)\s*(都|会)?\s*(自动)?(续订|续费|订阅|扣费|扣钱|扣|会员)"
                     r"|(加到|加进|放进|放到|记到|记进|记成|登记到?|算进|算作)\s*(我的)?订阅"
                     r"|是(我的)?(每月|按月)?(订阅|会员)")
NOT_DECLARE = re.compile(r"取消|退订|关闭|关掉|停掉|不要了|不想要|多少|哪些|有什么|吗|？|\?")
AMOUNT = re.compile(r"(?:¥|￥)\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|块钱?|rmb|RMB)")
FILLER = re.compile(r"我的|我们|我|把|帮我|帮忙|麻烦|请|这个|那个|就是|一下|都|会|自动|连续|也|的|了|吧|呢|啊|是|它|这|那"
                    r"|每个月|每月|按月|包月|每年|按年|年费|续订|续费|订阅|扣费|扣钱|扣|会员费|会员|加到|加进|放进|放到|记到|记进|记成"
                    r"|登记到?|算进|算作|里面|里|一个|服务|费用|金额|大概|大约|左右")


def ensure_table(db):
    with db.transaction() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS user_subscriptions(id TEXT PRIMARY KEY, owner TEXT NOT NULL, merchant TEXT NOT NULL, "
                     "amount INTEGER, frequency TEXT NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL)")


def is_declaration(text):
    return bool(DECLARE.search(text)) and not NOT_DECLARE.search(text)


def parse_declaration(text, known=()):
    """-> {merchant, amount_minor|None, frequency} or None. `known` are merchant names we already have; a match on one
    of them wins over free-text extraction."""
    if not is_declaration(text):
        return None
    low = text.lower()
    merchant = next((k for k in sorted(known, key=len, reverse=True) if k and k.lower() in low), None)
    m = AMOUNT.search(text)
    amount = round(float(m.group(1) or m.group(2)) * 100) if m else None
    if not merchant:
        rest = AMOUNT.sub(" ", text)
        rest = FILLER.sub(" ", rest)
        rest = re.sub(r"[，。,.!！~～、：:；;\s]+", " ", rest).strip()
        merchant = max(rest.split(" "), key=len) if rest else ""
        if not 2 <= len(merchant) <= 20:
            return None
    frequency = "YEARLY" if re.search(r"每年|按年|年费", text) else "MONTHLY"
    return {"merchant": merchant, "amount_minor": amount, "frequency": frequency}


def user_subscriptions(db, owner):
    ensure_table(db)
    return db.rows("SELECT * FROM user_subscriptions WHERE owner=? AND status='ACTIVE' ORDER BY created_at", (owner,))


def add_user_subscription(db, owner, merchant, amount, frequency):
    ensure_table(db)
    existing = db.one("SELECT * FROM user_subscriptions WHERE owner=? AND status='ACTIVE' AND lower(merchant)=lower(?)", (owner, merchant))
    with db.transaction() as conn:
        if existing:
            conn.execute("UPDATE user_subscriptions SET amount=COALESCE(?, amount), frequency=? WHERE id=?",
                         (amount, frequency, existing["id"]))
            return existing["id"], False
        sub_id = "us_" + uuid.uuid4().hex[:12]
        conn.execute("INSERT INTO user_subscriptions VALUES(?,?,?,?,?,?,?)", (sub_id, owner, merchant, amount, frequency, time.time(), "ACTIVE"))
        return sub_id, True


def remove_user_subscription(db, owner, sub_id):
    ensure_table(db)
    with db.transaction() as conn:
        return conn.execute("UPDATE user_subscriptions SET status='REMOVED' WHERE id=? AND owner=? AND status='ACTIVE'",
                            (sub_id, owner)).rowcount == 1


def fmt(minor):
    from backend.chat.amounts import format_cents
    return format_cents(minor)


class SubscriptionTalk:
    """Used by ChatService; `chat` is the ChatService."""
    QUESTION_TTL = 1800

    def __init__(self, chat):
        from backend.chat.pages import BillsPage
        self.chat, self.s, self.bills = chat, chat.s, BillsPage(chat)

    # -- the customer declares a subscription ---------------------------------------------------------------------
    def declare(self, owner, text):
        detected = self.bills.detected(owner)
        registered = [r for kind in ("subscription", "direct_debit") for r in self.s.service_agent.resources(owner, kind)]
        mine = user_subscriptions(self.s.db, owner)
        known = [d["merchant"] for d in detected] + [r["merchant"] for r in registered] + [u["merchant"] for u in mine]
        parsed = parse_declaration(text, known)
        if not parsed:
            return None
        name = parsed["merchant"]
        bank = next((r for r in registered if r["merchant"].lower() == name.lower()), None)
        if bank:
            return (f"{bank['merchant']} 已经是银行登记的{'订阅' if bank in self.s.service_agent.resources(owner, 'subscription') else '代扣'}"
                    f"（{fmt(bank['amount'])}），不用重复添加。")
        hit = next((d for d in detected if d["merchant"].lower() == name.lower()), None)
        if hit:
            self.bills.mark(owner, hit["key"], "CONFIRMED")
            return (f"好的，已把 {hit['merchant']}（约每 {hit['interval_days']} 天 {fmt(hit['amount_minor'])}）记为你的订阅。"
                    f"之后每次扣费前三天我会提醒你；不想续了，需要到 {hit['merchant']} 的 App 或官网关闭自动续费。")
        _, created = add_user_subscription(self.s.db, owner, name, parsed["amount_minor"], parsed["frequency"])
        cycle = "每年" if parsed["frequency"] == "YEARLY" else "每月"
        money = f"{cycle} {fmt(parsed['amount_minor'])}" if parsed["amount_minor"] else f"{cycle}续费（金额还没告诉我）"
        return (f"{'已记下' if created else '已更新'}：{name}，{money}。它会出现在“账单与订阅”里并计入每月固定支出。"
                + ("" if parsed["amount_minor"] else "告诉我金额（例如“每月15元”）可以算得更准。")
                + f"这只是 TRUSTA 里的记录，不会扣款；要关闭自动续费，需要在 {name} 的 App 里操作。")

    # -- "取消QQ音乐" for something only TRUSTA knows about ------------------------------------------------------------
    def cancel_hint(self, owner, text):
        if not re.search(r"取消|退订|关闭|关掉|停掉|不要了|不想要|不续了", text):
            return None
        registered = {r["merchant"].lower() for kind in ("subscription", "direct_debit") for r in self.s.service_agent.resources(owner, kind)}
        low = text.lower()
        if any(m in low for m in registered):
            return None  # the bank can cancel it: normal flow with a confirmation card
        names = [u["merchant"] for u in user_subscriptions(self.s.db, owner)] + \
                [d["merchant"] for d in self.bills.detected(owner) if d["state"] == "CONFIRMED"]
        name = next((n for n in sorted(names, key=len, reverse=True) if n.lower() in low), None)
        if not name:
            return None
        return (f"{name} 不是在本行登记的订阅，银行没法替你取消自动续费：请在 {name} 的 App 或官网里关闭自动续费。"
                f"如果扣款来源不明，可以先说“冻结我的卡”止损。想把它从你的订阅记录里移除，可以在“账单与订阅”页面操作。")

    # -- answer to our own question -------------------------------------------------------------------------------
    def answer(self, body, owner, text):
        question = body.get("sub_question")
        if not question or time.time() - question["at"] > self.QUESTION_TTL:
            return None
        if YES.match(text.strip()):
            body["sub_question"] = None
            try:
                self.bills.mark(owner, question["key"], "CONFIRMED")
            except Exception:
                return "这笔扣费的记录已经变化，请到“账单与订阅”里查看。"
            return (f"好的，已把 {question['merchant']} 记为你的订阅，计入每月固定支出；之后每次扣费前三天我会提醒你。"
                    f"想停掉的话，需要到 {question['merchant']} 的 App 或官网关闭自动续费。")
        if NO.match(text.strip()):
            body["sub_question"] = None
            from backend.chat import knowledge
            advice, _ = knowledge.answer("UNKNOWN_CHARGE", self.s.config)
            return f"明白，{question['merchant']} 不是你订的服务。" + advice
        return None

    # -- what to add after a reply --------------------------------------------------------------------------------
    def after_reply(self, body, owner, text, tools):
        from datetime import datetime
        from backend.business import CHINA
        notes = []
        try:
            reminders = self.s.events.reminders(owner, body["default_account_id"])["reminders"]
        except Exception:
            reminders = []
        for r in reminders:
            key = f"renewal:{r['id']}:{r['next_charge_at']}"
            if r["status"] in {"DUE", "OVERDUE"} and self.chat.store.claim_nudge(owner, key):
                when = datetime.fromtimestamp(r["next_charge_at"], CHINA).strftime("%m月%d日")
                notes.append(f"提醒：{r['merchant']} 将在 {when} 扣费 {fmt(r['amount'])}，不需要的话可以说“取消{r['merchant']}”。")
        try:
            detected = self.bills.detected(owner)
        except Exception:
            detected = []
        for d in detected:
            if d["state"] == "CONFIRMED" and d["next_estimate"] and 0 <= d["next_estimate"] - time.time() <= 3 * 86400:
                key = f"recurring-due:{d['key']}:{int(d['next_estimate'] // 86400)}"
                if self.chat.store.claim_nudge(owner, key):
                    when = datetime.fromtimestamp(d["next_estimate"], CHINA).strftime("%m月%d日")
                    notes.append(f"提醒：{d['merchant']} 预计在 {when} 左右再扣 {fmt(d['amount_minor'])}（按过去的扣费间隔估算）。")
        try:  # gold savings plans the customer set up: a due period waits for their confirmation, never auto-buys
            from backend.banking.extensions import GoldSavings
            for plan in GoldSavings(self.chat).view(owner)["plans"]:
                due = [r["due"] for r in plan["runs"] if r["state"] == "DUE"]
                if plan["status"] == "ACTIVE" and due and self.chat.store.claim_nudge(owner, "gold-due:" + plan["id"] + ":" + ",".join(due)):
                    notes.append(f"提醒：黄金定投「{plan['name']}」有 {len(due)} 期待确认，每期预算 {fmt(plan['amount'])}。"
                                 f"不会自动扣款，请到“理财 → 黄金定投”核对行情后逐笔确认。")
        except Exception:
            pass
        relevant = bool(TOPIC.search(text)) or bool(TOPIC_TOOLS & set(tools or []))
        # never while a confirmation card is waiting: a bare "是" must not be ambiguous
        if relevant and not body.get("sub_question") and not body.get("pending"):
            for d in detected:
                if d["state"] == "NEW" and self.chat.store.claim_nudge(owner, "recurring:" + d["key"]):
                    body["sub_question"] = {"key": d["key"], "merchant": d["merchant"], "at": time.time()}
                    notes.append(f"另外，我注意到 {d['merchant']} 每月扣 {fmt(d['amount_minor'])}，已经连续 {d['count']} 次，"
                                 f"但没有登记在你的订阅里。这是你每月续订的服务吗？回答“是”我就记进订阅，回答“不是”我教你怎么止损。")
                    break
        return notes
