"""Unified chat entry: guard -> understand (LLM tools or rules) -> validate -> six-agent runtime -> cards.

This layer owns dialogue state only. Every proposal is created by the existing
runtime and every decision goes through the existing per-action authorization
(hash-bound, TTL, strong auth, replay rules unchanged).
"""
import os
import re
import threading
import time
from backend.chat import guard
from backend.chat.automation_chat import AutomationMixin
from backend.chat.cards import CardRenderer, OPERATIONS
from backend.chat.llm import DeepSeekToolClient, LLMUnavailable, build_system_prompt, redact_for_model
from backend.chat.store import ChatStore
from backend.chat.tools import TOOLS, openai_tools
from backend.chat.validator import Clarify, InjectionDetected, ToolCallValidator
from backend.domain import Rejected, digest, uid

CONFIRM_WORDS = re.compile(r"^(确认|确定|好的?|可以|没问题|是的?|对的?|ok|okay|yes|执行吧?|转吧|同意)[。.!！~～]*$", re.I)
CANCEL_WORDS = re.compile(r"^(算了|不要了|不用了|取消(吧|刚才的)?|不转了|先不(了|转了)?|别转了|停)[。.!！~～]*$")
STRIKE_LIMIT = 3
LOCK_SECONDS = 600
SMALLTALK = {
    "GREETING": "你好，我是 TRUSTA 银行助手。可以帮你查余额和账单、转账、管理银行卡和订阅、推荐理财产品。",
    "THANKS": "不客气，还有什么可以帮你？",
    "CAPABILITIES": "我可以帮你：① 转账（按姓名 / 手机号 / 称呼，支持定时）和 AA 收款；② 账单分析和异常交易识别；"
                    "③ 理财推荐、对比、申购赎回；④ 卡片冻结、挂失、限额和交易开关；⑤ 订阅和代扣的识别与取消。"
                    "所有资金操作都需要你逐笔确认。",
}
OFFER_TEXT = re.compile(r"需要我.{0,16}吗|要不要我|是否需要我|我(来|帮你|为你|可以帮你).{0,8}(办理|提交|执行|挂失|转账|冻结|申购|赎回|取消|关闭)"
                        r"|(已|已经)(为你|帮你)?.{0,4}(提交|执行|办理|转账|挂失|冻结)|确认后.{0,4}(执行|提交)|准备.{0,6}操作|待确认的信息")
HUMAN_WORDS = re.compile(r"转人工|人工客服|真人客服|找客服|投诉|要人工")
BACK_TO_AGENT = re.compile(r"取消转人工|不用人工|回到智能助手|继续用智能助手")
UNDO_WORDS = re.compile(r"撤销|撤回|回退|恢复刚才|改回去|弄错了.*恢复")
STATE_LABELS = {"HANDED_OFF": "已转人工", "IDLE": "空闲", "CLARIFYING": "等待用户补充信息", "AWAITING_CONFIRMATION": "等待用户在确认卡上决定", "LOCKED": "已锁定"}


class ChatService(AutomationMixin):
    def __init__(self, service, llm_client=None):
        if not service.banking:
            raise Rejected("CHAT_REQUIRES_BANKING_V5")
        self.s = service
        self.store = ChatStore(service.db, service.audit)
        self.llm = llm_client if llm_client is not None else DeepSeekToolClient()
        from backend.capabilities import CapabilityRegistry
        self.capabilities = service.deployment_capabilities = CapabilityRegistry(service, self)
        self._llm_gate = threading.BoundedSemaphore(max(1, int(os.environ.get("TRUSTA_CHAT_LLM_CONCURRENCY", "4"))))

    # ---- catalogue ---------------------------------------------------------------------------
    def catalog(self, owner):
        s = self.s
        accounts = []
        for row in s.db.rows("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,)):
            name = "储蓄账户" if "saving" in row["id"] else "活期账户"
            accounts.append({"id": row["id"], "display": f"{name}（{row['id']}）"})
        status_zh = {"ACTIVE": "正常", "FROZEN": "已冻结", "LOST": "已挂失", "CLOSED": "已注销"}
        cards = [{"id": c["id"], "display": f"{'借记卡' if c['card_type'] == 'DEBIT' else '虚拟卡'}（{c['id']}，{status_zh.get(c['status'], c['status'])}）",
                  "status": c["status"], "controls": {"online": c["online_enabled"], "international": c["international_enabled"],
                                                      "contactless": c["contactless_enabled"]}}
                 for c in s.service_agent.resources(owner, "card")]
        subs = [{"id": r["id"], "display": f"{r['merchant']} 每月{r['amount'] / 100:.2f}元"}
                for r in s.service_agent.resources(owner, "subscription") if r["status"] == "ACTIVE"]
        debits = [{"id": r["id"], "display": f"{r['merchant']} 每月{r['amount'] / 100:.2f}元"}
                  for r in s.service_agent.resources(owner, "direct_debit") if r["status"] == "ACTIVE"]
        products = [{"id": p["id"], "display": f"{p['name']}（R{p['risk_level']}，{p['term_days']}天）"}
                    for p in s.wealth.products(owner) if p["active"]]
        months = lambda e: f"{e['month']}月{e['day']}日"
        events = [{"id": e["id"], "display": f"{e['relation']}{e['person']}{'生日' if e['kind'] == 'BIRTHDAY' else '纪念日'}（{months(e)}）",
                   "month": e["month"], "day": e["day"]} for e in s.service_agent.resources(owner, "life_event")]
        merchants = [{"id": m["id"], "display": f"{m['name']}·{m['item']}（{m['price'] / 100:.2f}元）", "price": m["price"]}
                     for m in s.service_agent.resources(owner, "merchant")]
        funds = [{"id": f["id"], "display": f"心愿金“{f['name']}”（余 {f['balance'] / 100:.2f} 元）"}
                 for f in s.service_agent.resources(owner, "wish_fund") if f["status"] == "ACTIVE"]
        # Contacts: names and nicknames only (no phone numbers) so the model can map "房东" / "我妈" itself.
        payees = [{"id": r["id"], "display": r["name"] + (f"（{r['alias']}）" if r["alias"] else "")}
                  for r in s.db.rows("SELECT p.id, p.name, c.alias FROM payees p LEFT JOIN payee_contacts c ON c.payee_id=p.id "
                                     "WHERE p.customer_id=? AND (c.status IS NULL OR c.status='ACTIVE') ORDER BY p.id", (owner,))]
        engine = getattr(s, "automation", None)
        automations = [{"id": r["id"], "display": f"{r['name']}（{engine.describe_trigger(r['trigger'])}）"}
                       for r in engine.rules(owner)] if engine else []
        return {"accounts": accounts, "cards": cards, "subscriptions": subs, "direct_debits": debits, "products": products,
                "life_events": events, "merchants": merchants, "wish_funds": funds, "payees": payees, "automations": automations}

    # ---- sessions ----------------------------------------------------------------------------
    def create_session(self, owner, channel="app", default_account_id="acct-demo"):
        catalog = self.catalog(owner)
        if default_account_id not in {a["id"] for a in catalog["accounts"]}:
            raise Rejected("ACCOUNT_ACCESS_DENIED")
        body = self.store.create(owner, channel, default_account_id, catalog["accounts"])
        if self._sync_events(body, owner):
            body = self.store.save(body)
        view = self.view(body)
        view["suggestions"] = ["我这个月花了多少钱", "给张三转500元", "我的卡丢了", "帮我看看有哪些订阅"]
        return view

    def view(self, body):
        return {"session_id": body["session_id"], "revision": body["revision"], "state": self._state(body),
                "title": body.get("title") or "新对话", "active": not body["closed"] and body["expires_at"] > time.time(),
                "plan": body.get("plan"),
                "channel": body["channel"], "default_account_id": body["default_account_id"],
                "accounts": body["accounts"], "llm_mode": "deepseek" if getattr(self.llm, "available", False) else "rules",
                "messages": [{k: m[k] for k in ("role", "text", "at", "message_id", "cards") if k in m} for m in body["messages"]],
                "pending": [p["card"] for p in body["pending"]] + [p["card"] for p in body.get("pending_rules", [])]}

    def get(self, session_id, owner):
        body = self.store.get(session_id, owner)
        if not body["closed"] and not body["busy"] and self._sync_events(body, owner):
            body = self.store.save(body)
        return self.view(body)

    EVENT_LABELS = {"RECURRING_TRANSFER": "周期转账", "WISH_ORDER": "心愿金下单", "WISH_RELEASE": "心愿金到期释放",
                    "BIRTHDAY_TRANSFER": "生日转账", "SPENDING_REVIEW": "支出与订阅复查"}

    def _sync_events(self, body, owner, before_last=False):
        """Due proposal-only events become confirmation cards in the conversation (never auto-approved)."""
        try:
            self.s.events.tick(owner)
        except Rejected:
            pass
        surfaced = body.setdefault("surfaced_events", [])
        renderer, added = None, []
        for event in self.s.events.list(owner):
            if event["status"] != "PROPOSED" or event["id"] in surfaced or not event.get("task_id"):
                continue
            surfaced.append(event["id"])
            renderer = renderer or CardRenderer(self.s, owner, self.catalog(owner))
            task = self.s.get_task(event["task_id"], owner)
            new_cards, _ = renderer.task_cards(task)
            for card in new_cards:
                if card["type"] == "confirm":
                    body["pending"].append({"task_id": task["task_id"], "action_id": card["action_id"], "card": card,
                                            "task_type": task["task_type"], "key": "event:" + event["id"]})
            label = self.EVENT_LABELS.get(event["intent"]["event_type"], "计划任务")
            added.append({"role": "assistant", "message_id": "event-" + event["id"][:12], "at": time.time(), "cards": new_cards,
                          "text": f"提醒：{label}到时间了，请确认后执行。" if any(c["type"] == "confirm" for c in new_cards)
                          else f"提醒：{label}到时间了，但这次没能生成待确认的操作。"})
        added += self._deliver_firings(body, owner)
        added += self._proactive(body, owner, surfaced)
        if not added:
            return False
        del surfaced[:-200]
        if before_last:  # keep the user's message last so the turn reads naturally
            last = body["messages"].pop()
            body["messages"].extend(added + [last])
        else:
            body["messages"].extend(added)
        return True

    def _proactive(self, body, owner, surfaced):
        """Proactive, read-only nudges (renewals due within 3 days, life events within 30 days). Never a proposal by itself."""
        from datetime import datetime, date
        from backend.business import CHINA
        from backend.chat.amounts import format_cents
        messages = []
        try:
            reminders = self.s.events.reminders(owner, body["default_account_id"])["reminders"]
        except Rejected:
            reminders = []
        for r in reminders:
            key = "renewal:" + r["id"]
            if r["status"] in {"DUE", "OVERDUE"} and key not in surfaced:
                surfaced.append(key)
                when = datetime.fromtimestamp(r["next_charge_at"], CHINA).strftime("%m月%d日")
                messages.append({"role": "assistant", "message_id": "nudge-" + r["id"][:12], "at": time.time(), "cards": [],
                                 "text": f"提醒：{r['merchant']} 将在 {when} 扣费 {format_cents(r['amount'])}。不需要的话，可以直接说“取消{r['merchant']}”。"})
        today = datetime.now(CHINA).date()
        funds = [f["name"] for f in self.s.service_agent.resources(owner, "wish_fund") if f["status"] == "ACTIVE"]
        for event in self.s.service_agent.resources(owner, "life_event"):
            day = date(today.year, event["month"], event["day"])
            if day < today:
                day = date(today.year + 1, event["month"], event["day"])
            days, key = (day - today).days, f"life:{event['id']}:{day.year}"
            if not 0 < days <= 30 or key in surfaced or any(event["relation"] in n or event["person"] in n for n in funds):
                continue
            surfaced.append(key)
            kind = "生日" if event["kind"] == "BIRTHDAY" else "纪念日"
            messages.append({"role": "assistant", "message_id": "nudge-" + event["id"][:12], "at": time.time(), "cards": [],
                             "text": f"{event['relation']}{event['person']}的{kind}还有 {days} 天（{day.month}月{day.day}日）。"
                                     f"需要我帮你提前锁定一笔心愿金，并在{kind}前两天预订鲜花和蛋糕吗？"})
        return messages

    def submit_assessment(self, session_id, owner, answers):
        from backend.chat import risk
        try:
            level, answers_hash = risk.score(answers)
        except ValueError:
            raise Rejected("INVALID_ANSWERS") from None
        body = self.store.get(session_id, owner)
        if body["closed"]:
            raise Rejected("CHAT_CLOSED")
        normalized = {"task_type": "RISK_ASSESSMENT", "parameters": risk.parameters(level, answers_hash), "needs_clarification": False}
        task = self._run("理财风险测评", owner, body["default_account_id"], normalized)
        cards, texts = CardRenderer(self.s, owner, self.catalog(owner)).task_cards(task)
        for card in cards:
            if card["type"] == "confirm":
                body["pending"].append({"task_id": task["task_id"], "action_id": card["action_id"], "card": card,
                                        "task_type": "RISK_ASSESSMENT", "key": "risk"})
        text = f"测评结果：R{level} {risk.LEVELS[level]}。确认后更新你的风险等级，有效期一年。" + "".join(texts[1:])
        body["messages"].append({"role": "assistant", "message_id": "risk-" + answers_hash[:12], "text": text, "cards": cards, "at": time.time()})
        body = self.store.save(body)
        return {"session_id": session_id, "revision": body["revision"], "state": self._state(body),
                "reply": {"text": text, "cards": cards}, "plan": None}

    def demo_advance(self, session_id, owner, event_id):
        """TRUSTA_DEMO_MODE only: bring one scheduled step forward so a judge can see it without waiting days."""
        self.store.get(session_id, owner)
        self.s.events.advance_for_demo(owner, event_id)
        return self.get(session_id, owner)

    def close(self, session_id, owner):
        body = self.store.get(session_id, owner)
        self._cancel_pending(body, owner)
        body["closed"] = True
        return self.view(self.store.save(body))

    def hide(self, session_id, owner):
        """Delete from the history list. Pending proposals are cancelled; the audit trail and bank records stay."""
        body = self.store.get(session_id, owner)
        self._cancel_pending(body, owner)
        body["closed"] = body["hidden"] = True
        self.store.save(body)
        self.s.audit.append(None, None, "CHAT_SESSION_HIDDEN", "chat", {"session_id": session_id})
        return {"session_id": session_id, "hidden": True}

    @staticmethod
    def _state(body):
        if body.get("locked_until", 0) > time.time():
            return "LOCKED"
        if (body.get("handoff") or {}).get("status") == "OPEN":
            return "HANDED_OFF"
        if body["pending"]:
            return "AWAITING_CONFIRMATION"
        if body.get("clarify"):
            return "CLARIFYING"
        return "IDLE"

    # ---- turn --------------------------------------------------------------------------------
    def send(self, session_id, owner, message_id, revision, text, account_id=None, on_event=None):
        emit = on_event or (lambda name, data: None)
        body, replay = self.store.claim(session_id, owner, message_id, revision, text, account_id)
        if replay is not None:
            return replay
        account = account_id or body["default_account_id"]
        emit("turn.started", {"message_id": message_id, "revision": body["revision"]})
        try:
            if account not in {a["id"] for a in body["accounts"]}:
                raise Rejected("ACCOUNT_ACCESS_DENIED")
            outcome = self._process(body, owner, text.strip(), account, emit)
        except Exception as exc:  # The turn fails closed: no new proposal survives.
            code = exc.code if isinstance(exc, Rejected) else "CHAT_FAILED"
            self.s.audit.append(None, None, "CHAT_TURN_FAILED", "chat", {"session_id": session_id, "message_id": message_id, "code": code})
            outcome = {"text": "抱歉，这次没有处理成功，没有执行任何操作。请稍后再试或换个说法。", "cards": [], "mode": "error",
                       "tools": [], "error": code}
        return self._finish(body, owner, message_id, outcome, emit)

    @staticmethod
    def _smart_title(body, cards):
        """After the first exchange, name the conversation by what it did ("转账给 张三 · ¥500.00"), not by the
        customer's raw words; a title the customer set themselves is never touched."""
        if not body.get("title_auto") or sum(1 for m in body["messages"] if m["role"] == "user") != 1:
            return
        card = next((c for c in cards if c.get("title") and c.get("type") in {"confirm", "report", "risk_alert", "automation_confirm", "knowledge"}), None)
        if not card:
            return
        amount = next((f["value"] for f in card.get("fields") or [] if f.get("label") == "金额"), None)
        title = card["title"] + (f" · {amount}" if amount else "")
        body["title"] = " ".join(title.split())[:28]
        body["title_auto"] = False

    def _finish(self, body, owner, message_id, outcome, emit):
        reply = {"text": outcome["text"], "cards": outcome["cards"]}
        body["messages"].append({"role": "assistant", "message_id": message_id, "text": outcome["text"],
                                 "cards": outcome["cards"], "at": time.time()})
        body["busy"] = None
        turn = {"session_id": body["session_id"], "message_id": message_id, "revision": body["revision"] + 1,
                "state": None, "reply": reply, "plan": outcome.get("plan"), "understanding": outcome.get("mode"),
                "tools": outcome.get("tools", [])}
        if outcome.get("error"):
            turn["error"] = outcome["error"]
        if outcome.get("fallback_reason"):
            turn["fallback_reason"] = outcome["fallback_reason"]
        body["turns"][message_id]["response"] = turn
        self._smart_title(body, outcome["cards"])
        if outcome.get("trace"):
            # The full decision chain lives with the session; its hash is anchored in the append-only audit chain.
            trace = outcome["trace"] | {"message_id": message_id, "at": time.time()}
            event_id = self.s.audit.append(None, None, "CHAT_DECISION_TRACE", "chat", trace)
            body["turns"][message_id]["trace"] = trace | {"audit_event_id": event_id}
            turn["trace_id"] = event_id
        turn["state"] = self._state(body)
        body = self.store.save(body)
        turn["state"] = self._state(body)
        body["turns"][message_id]["response"] = turn
        self.store.save(body, bump=False)
        self.s.audit.append(None, None, "CHAT_TURN_COMPLETED", "chat",
                            {"session_id": body["session_id"], "message_id": message_id, "text_hash": digest(body["messages"][-2]["text"]),
                             "understanding": outcome.get("mode"), "tools": outcome.get("tools", []), "state": turn["state"]})
        for card in outcome["cards"]:
            emit("card", card)
        emit("message.delta", {"text": outcome["text"]})
        emit("turn.completed", turn)
        return turn

    def _corpus(self, body):
        return [m["text"] for m in body["messages"] if m["role"] == "user"][-8:]

    def _process(self, body, owner, text, account, emit):
        trace = {"text_hash": digest(text), "guard": None, "calls": [], "steps": [], "clarifications": []}
        outcome = self._process_inner(body, owner, text, account, emit, trace)
        trace.update(understanding=outcome.get("mode"), fallback_reason=outcome.get("fallback_reason"), risk=outcome.get("risk"),
                     model=getattr(self.llm, "model", None) if outcome.get("mode") == "deepseek" else None)
        outcome["trace"] = trace
        return outcome

    def _process_inner(self, body, owner, text, account, emit, trace):
        cards, texts, tools = [], [], []
        now = time.time()
        if body.get("locked_until", 0) > now:
            return {"text": "出于安全考虑，本会话已暂时锁定，请稍后再试或新开会话。", "cards": [], "mode": "locked", "tools": []}
        self._sync_events(body, owner, before_last=True)
        if self._state(body) == "HANDED_OFF":
            return self._handoff_message(body, owner, text)
        # 1. chat words never authorise anything
        if body["pending"] and CONFIRM_WORDS.match(text):
            return {"text": "请直接在确认卡上点“确认”完成授权（需要时输入验证码）。聊天消息不能代替授权。",
                    "cards": [p["card"] for p in body["pending"]], "mode": "rules", "tools": []}
        # 2. deterministic input guard
        hit = guard.inspect(text)
        emit("guard.result", {"passed": hit is None, "category": hit and hit["category"]})
        trace["guard"] = hit and {"category": hit["category"], "evidence": hit.get("evidence")}
        if hit:
            return self._risk(body, owner, hit, tools)
        # 3. understanding
        catalog = self.catalog(owner)
        fallback = None
        try:
            calls, mode = self._understand(body, owner, text, catalog), "deepseek"
        except LLMUnavailable as exc:
            calls, mode, fallback = None, "rules", exc.code
            self.s.audit.append(None, None, "CHAT_LLM_FALLBACK", "chat", {"session_id": body["session_id"], "reason": exc.code})
        rule_text = text
        if calls is None:
            from backend.chat.rules import rule_calls
            steps, clarifications = self._rule_control(text), []
            if not steps:
                previous = (body.get("clarify") or {}).get("text")
                payee = next((p["key"] for p in reversed(body["pending"]) if p["task_type"] == "TRANSFER"), None)
                if previous:  # multi-turn slot filling without a model: "帮我给张三转账" + "两千"
                    rule_text = previous + " " + text
                    calls = rule_calls(rule_text, catalog, payee) or None
                if calls is None:
                    rule_text = text
                    calls = rule_calls(text, catalog, payee) or None
            if calls is None and not steps:
                steps, clarifications = self._rule_steps(body, text, catalog)
            if calls is None:
                trace["calls"] = [{"name": s.get("tool"), "arguments": s.get("normalized") or s.get("args"), "verdict": "RULES"} for s in steps]
        if calls is not None:
            steps, clarifications = [], []
            validator = ToolCallValidator(self._corpus(body), catalog,
                                          lambda value: self.s.gateway.read("transaction", owner, "payee.resolve", name=value),
                                          default_account_id=account)
            for call in calls:
                tools.append(call["name"])
                record = {"name": call["name"], "arguments": call["arguments"], "verdict": "ACCEPTED"}
                trace["calls"].append(record)
                enabled, feature = self.capabilities.tool_enabled(call["name"], owner)
                if not enabled:  # fail closed: a feature the deployment does not support is never attempted
                    record["verdict"] = "UNAVAILABLE:" + feature["id"]
                    texts.append(f"“{feature['title']}”在当前配置下不可用（{feature['reason']}）")
                    continue
                try:
                    steps.extend(validator.validate(call["name"], call["arguments"]))
                except InjectionDetected as exc:
                    record["verdict"] = "INJECTION:" + exc.code
                    hit = {"category": "PROMPT_INJECTION", "severity": "HIGH", "blocking": True,
                           "message": guard.MESSAGES["PROMPT_INJECTION"], "evidence": exc.code}
                    return self._risk(body, owner, hit, tools, mode)
                except Clarify as exc:
                    record["verdict"] = "CLARIFY:" + exc.code
                    clarifications.append(exc)
        emit("intent.recognized", {"tools": tools or [s.get("tool") for s in steps], "mode": mode})
        # 4. control steps
        for step in [s for s in steps if s["kind"] == "control"]:
            name, args = step["tool"], step.get("args", {})
            if name == "flag_risk":
                category = args["category"]
                hit = {"category": category, "severity": "HIGH", "blocking": True,
                       "message": guard.MESSAGES[category].format(pattern="可疑转账要求"), "evidence": args.get("evidence_span", "")}
                return self._risk(body, owner, hit, tools, mode)
            if name == "request_human":
                return self._open_handoff(body, owner, args.get("reason") or text, mode, tools)
            if name == "undo_last":
                undo_steps, undo_text = self._undo_steps(body, owner)
                steps.extend(undo_steps)
                if undo_text:
                    texts.append(undo_text)
                continue
            if name == "cancel_pending":
                cancelled = self._cancel_pending(body, owner)
                body["clarify"] = None
                texts.append(f"好的，已取消 {cancelled} 项待确认的操作，没有执行任何操作。" if cancelled else "好的，当前没有需要取消的操作。")
            elif name == "ask_user":
                clarifications.append(Clarify("MODEL_ASKED", args["slot"], args["question"], args.get("options")))
            elif name == "smalltalk":
                texts.append(SMALLTALK[args["kind"]])
                if args["kind"] == "CAPABILITIES":
                    off = [f["title"].split("（")[0] for f in self.capabilities.features(owner=owner) if f["status"] in {"UNAVAILABLE", "OFF"}]
                    if off:
                        texts.append("当前部署未开通：" + "、".join(off))
            elif name == "cancel_automation":
                try:
                    count = self.s.automation.cancel(owner, args["rule_id"])
                    texts.append(f"已停用 {count} 条联动规则")
                except Rejected as exc:
                    texts.append("没有找到这条联动规则")
            elif name == "enable_protection":
                state = self.s.safety.enable_protection(owner, args.get("hours", 24), "USER_REQUEST", "chat:" + body["session_id"])
                cancelled = self._cancel_pending(body, owner)
                texts.append(f"已开启转账保护，到 {time.strftime('%m-%d %H:%M', time.localtime(state['until']))} 前对外转账、新增收款人都会暂停"
                             + (f"，并取消了 {cancelled} 项待确认的操作" if cancelled else "")
                             + "。保护期内只能由银行安全人员核实后提前解除")
            elif name == "knowledge":
                from backend.chat import knowledge
                answer, sources = knowledge.answer(args["topic"], self.s.config, [OPERATIONS.get(k, k) for k in self.IRREVERSIBLE])
                cards.append({"type": "knowledge", "card_id": "c_kb_" + uid()[:8], "topic": args["topic"],
                              "title": knowledge.TITLES[args["topic"]], "text": answer, "sources": sources})
                texts.append(answer)
            elif name == "navigate":
                from backend.chat import knowledge
                cards.append(knowledge.navigate_card(args["page"], "c_nav_" + uid()[:8]))
                texts.append(f"已为你找到“{knowledge.PAGES[args['page']]}”入口，点击下方按钮打开。没有发起任何交易")
            elif name == "out_of_scope":
                texts.append("这个我暂时帮不上忙。" + SMALLTALK["CAPABILITIES"])
        # 5. execute reads, register proposals
        renderer = CardRenderer(self.s, owner, catalog)
        goal = text
        plan, last_lock, nodes = None, None, []
        balance_gate, queued_now = None, []
        # Every bank action's preconditions include the account snapshot, so once a turn moves money, its writes must run
        # one after another: each later card is proposed on the state the previous one left behind.
        writes = [s for s in steps if s["kind"] == "propose" and s.get("normalized")]
        serial = len(writes) >= 2 and any(self._moves_balance(s["normalized"]) for s in writes)
        superseded = []
        for index, step in enumerate([s for s in steps if s["kind"] in {"read", "propose", "plan", "automation", "trip"}]):
            emit("tool.started", {"tool": step["tool"]})
            node = {"id": f"n{len(nodes) + 1}", "title": self._step_title(step), "kind": "WRITE" if step["kind"] != "read" else "READ",
                    "status": "SUCCEEDED", "action_ids": [], "depends_on": [nodes[-1]["id"]] if nodes else []}
            if step["kind"] != "plan":
                nodes.append(node)
                trace["steps"].append({"tool": step["tool"], "kind": step["kind"], "node": node})
            try:
                if step["kind"] == "plan":
                    if step.get("link_to_previous") and last_lock is None:
                        continue  # the lock proposal failed: never schedule orders against a fund that will not exist
                    request = f"chat:{body['session_id']}:{body['messages'][-1]['message_id']}:{index}"
                    event_ids = [self.s.events.create(owner, f"{request}:{i}", account, e["event_type"], e["execute_at"],
                                                      e["parameters"] | ({"series_id": request} if e["event_type"] == "RECURRING_TRANSFER" else {}))["id"]
                                 for i, e in enumerate(step["events"])]
                    self.s.audit.append(None, None, "CHAT_PLAN_CREATED", "chat", {"session_id": body["session_id"], "events": event_ids})
                    if step.get("link_to_previous"):
                        last_lock["linked_events"] = event_ids
                        plan = dict(step["plan"], plan_id="p_" + event_ids[0][:8])
                        texts.append("计划已排好：先确认锁定，之后的步骤到时间会再提醒你逐项确认。")
                    else:  # recurring transfer
                        d = step["display"]
                        cards.append({"type": "report", "card_id": "c_" + event_ids[0][:8], "report_type": "STANDING_PLAN",
                                      "items": [{"event_id": event_ids[0], "payee": d["payee"], "amount": d["amount"], "first_run": d["when"],
                                                 "frequency": d["frequency"], "count": d["count"], "memo": step.get("memo")}]})
                        texts.append(f"已设置{d['frequency']}给 {d['payee']} 转账 {d['amount']}，共 {d['count']} 次，首次 {d['when']}。"
                                     "每次到期我会提醒你，确认后才会转出。")
                    emit("tool.finished", {"tool": step["tool"], "status": "OK"})
                    continue
                if step.get("local"):
                    card, text_ = self._local_read(owner, step["local"])
                    cards.append(card), texts.append(text_)
                    emit("tool.finished", {"tool": step["tool"], "status": "OK"})
                    continue
                if step["kind"] == "automation":
                    card, text_ = self._draft_automation(body, owner, account, step["specs"], step.get("auto_tighten"), catalog)
                    cards.append(card), texts.append(text_)
                    node.update(status="AWAITING_CONFIRMATION", bundle_id=card["bundle_id"])
                    emit("tool.finished", {"tool": step["tool"], "status": "OK"})
                    continue
                if step["kind"] == "trip":
                    trip_origin = {"channel": body["channel"], "session_id": body["session_id"],
                                   "message_id": body["messages"][-1]["message_id"], "utterance_hash": trace["text_hash"],
                                   "understanding": mode, "tool_calls_hash": digest(trace["calls"])}
                    new_cards, new_texts, plan = self._trip(body, owner, account, step, catalog, renderer, trip_origin)
                    cards.extend(new_cards), texts.extend(new_texts)
                    nodes.remove(node)  # the trip carries its own DAG
                    emit("tool.finished", {"tool": step["tool"], "status": "OK"})
                    continue
                if step.get("op") == "RISK_QUESTIONNAIRE":
                    from backend.chat import risk
                    cards.append(risk.card() | {"card_id": "c_risk_" + str(body["revision"])})
                    texts.append("请完成下面 5 道题，我会根据结果更新你的风险等级（需要你确认）。")
                    emit("tool.finished", {"tool": step["tool"], "status": "OK"})
                    continue
                origin = {"channel": body["channel"], "session_id": body["session_id"], "message_id": body["messages"][-1]["message_id"],
                          "utterance_hash": trace["text_hash"], "understanding": mode,
                          "model": getattr(self.llm, "model", None) if mode == "deepseek" else None,
                          "tool_calls_hash": digest(trace["calls"])}
                if step["kind"] == "propose" and serial and balance_gate is not None and not step.get("has_plan"):
                    # Sequential dependency: this step's preconditions (balance) are only meaningful after the previous
                    # money movement has executed. Its proposal -- and its authorization clock -- start then.
                    node["status"], node["after"] = "QUEUED", balance_gate
                    body.setdefault("queued", []).append({"after": balance_gate if not queued_now else None, "node_id": node["id"],
                                                          "step": step, "goal": goal, "account": account, "origin": origin})
                    queued_now.append(node["title"])
                    emit("tool.finished", {"tool": step["tool"], "status": "QUEUED"})
                    continue
                if step["kind"] == "propose":
                    superseded.extend(self._supersede(body, owner, step["normalized"]))
                task = self._run(goal, owner, account, step["normalized"], origin)
                node["task_id"] = task["task_id"]
                new_cards, new_texts = renderer.task_cards(task, step)
                cards.extend(new_cards), texts.extend(new_texts)
                for card in new_cards:
                    if card["type"] == "confirm":
                        if step.get("memo"):
                            card["fields"].insert(2, {"label": "备注", "value": step["memo"]})
                        entry = {"task_id": task["task_id"], "action_id": card["action_id"], "card": card, "memo": step.get("memo"),
                                 "task_type": step["normalized"]["task_type"], "key": self._key(step["normalized"])}
                        body["pending"].append(entry)
                        node["action_ids"].append(card["action_id"])
                        node["status"] = "AWAITING_CONFIRMATION"
                        if step["normalized"].get("operation") == "FUND_LOCK":
                            last_lock = entry
                        if serial:
                            balance_gate = card["action_id"]
                        for item in body.get("queued", []):
                            if item["after"] in superseded:
                                item["after"] = card["action_id"]  # "改成800": the follow-up now waits for the new card
                if task.get("status") in {"BLOCKED", "FAILED"} and not node["action_ids"]:
                    node["status"] = "FAILED"
                elif task.get("status") == "WAITING_USER" and not node["action_ids"]:
                    node["status"] = "NEEDS_INPUT"
                emit("tool.finished", {"tool": step["tool"], "status": "OK"})
            except Rejected as exc:
                node["status"] = "FAILED"
                emit("tool.finished", {"tool": step["tool"], "status": exc.code})
                label = (OPERATIONS.get(step["normalized"]["task_type"], "这项操作") if step.get("normalized")
                         else "计划" if step["kind"] == "plan" else "查询")
                texts.append(f"{label}没有完成：" + self._error(exc.code))
        if queued_now:
            texts.append("“" + "”、“".join(queued_now) + "”要基于上一步执行后的账户状态，会在上一步完成后再生成确认卡；"
                         "上一步被拒绝或失败时自动取消")
        if plan is None and len(nodes) >= 2:
            # Multi-intent turn: show it as an ordered plan; each write node is still decided card by card.
            plan = {"plan_id": "p_" + digest([body["session_id"], body["messages"][-1]["message_id"]])[:10], "goal": text[:60], "nodes": nodes}
            if clarifications:
                plan["nodes"].append({"id": f"n{len(nodes) + 1}", "title": "等待补充信息", "kind": "WRITE", "status": "NEEDS_INPUT",
                                      "action_ids": [], "depends_on": [nodes[-1]["id"]]})
        if plan is not None:
            body["plan"] = plan
            emit("plan.created", plan)
        # 6. clarifications (one question at a time keeps the dialogue short)
        trace["clarifications"] = [c.code for c in clarifications]
        if clarifications:
            first = clarifications[0]
            cards.append(renderer.clarify_card(first.slot, first.question, first.options))
            texts.append(first.question)
            body["clarify"] = {"slot": first.slot, "code": first.code, "text": rule_text if mode == "rules" else None}
        elif steps:
            body["clarify"] = None
        if not texts:
            texts.append("我没太理解你的意思，可以换个说法吗？例如“给张三转500元”或“我这个月花了多少钱”。")
            if mode == "rules":
                texts[-1] = "智能理解暂不可用，我目前只能识别标准说法。" + texts[-1]
        return {"text": "".join(t if t.endswith(("。", "？", "！")) else t + "。" for t in texts), "plan": plan,
                "cards": cards, "mode": mode, "tools": tools or [s.get("tool") for s in steps], "fallback_reason": fallback}

    def _error(self, code):
        from backend.chat.cards import error_text
        return error_text([code]) + "。"

    def _run(self, goal, owner, account, normalized, origin=None):
        """Hand a validated intent to the six-agent runtime. `origin` (provenance of the interpretation) rides inside the
        normalized intent, so the Supervisor's signed REQUEST message attests where the request came from."""
        from backend.runtime.banking_plan import TASKS
        if origin:
            # The deployment capability snapshot in force is bound into the signed request as well.
            normalized = normalized | {"origin": origin | {"capability_snapshot": self.capabilities.snapshot()[0]}}
        if normalized["task_type"] in TASKS:
            return self.s.banking.create(goal, owner, account, normalized)
        return self.s._create_task(goal, owner, account, normalized)

    @staticmethod
    def _key(normalized):
        return normalized.get("recipient_name") or normalized.get("target_id") or normalized["task_type"]

    def _supersede(self, body, owner, normalized):
        """A changed proposal for the same target replaces the old undecided one (new hash, new card)."""
        key, kind = self._key(normalized), normalized["task_type"]
        kinds = {kind} | ({"TRANSFER", "SCHEDULED_TRANSFER"} if kind in {"TRANSFER", "SCHEDULED_TRANSFER"} else set())
        keep, replaced = [], []
        for entry in body["pending"]:
            if entry["task_type"] in kinds and entry["key"] == key:
                replaced.append(entry["action_id"])
                self._cancel_task(entry["task_id"], owner, entry.get("linked_events"))
                self.s.audit.append(entry["task_id"], entry["action_id"], "CHAT_PROPOSAL_SUPERSEDED", "chat",
                                    {"session_id": body["session_id"]})
            else:
                keep.append(entry)
        body["pending"] = keep
        return replaced

    def _cancel_task(self, task_id, owner, linked_events=()):
        try:
            self.s.cancel_draft(task_id, owner)
        except Rejected:
            pass
        self._cancel_events(owner, linked_events)

    def _cancel_events(self, owner, event_ids):
        for event_id in event_ids or ():
            try:
                self.s.events.cancel(event_id, owner)
            except Rejected:
                pass

    def _cancel_pending(self, body, owner):
        count = 0
        for task_id in dict.fromkeys(entry["task_id"] for entry in body["pending"]):
            self._cancel_task(task_id, owner, [e for p in body["pending"] if p["task_id"] == task_id for e in p.get("linked_events", [])])
            count += sum(1 for e in body["pending"] if e["task_id"] == task_id)
        body["pending"] = []
        count += self._drop_queue(body, None, everything=True)
        count += self._cancel_rule_drafts(body, owner)
        return count

    BALANCE_OPERATIONS = {"TRANSFER", "INVESTMENT_PURCHASE", "INVESTMENT_REDEEM", "FUND_LOCK", "FUND_RELEASE"}

    @classmethod
    def _moves_balance(cls, normalized):
        return (normalized.get("operation") or normalized.get("task_type")) in cls.BALANCE_OPERATIONS \
            or normalized.get("task_type") == "TRANSFER"

    @staticmethod
    def _drop_queue(body, after, everything=False):
        """Cancel queued follow-ups that wait on `after` (and the ones chained behind them)."""
        queue, keep, dropped, chained = body.get("queued", []), [], [], False
        for item in queue:
            if everything or item["after"] == after or (chained and item["after"] is None):
                dropped.append(item)
                chained = True
            else:
                keep.append(item)
                chained = False
        body["queued"] = keep
        ids = {item["node_id"] for item in dropped}
        for node in (body.get("plan") or {}).get("nodes", []):
            if node["id"] in ids and node["status"] == "QUEUED":
                node["status"] = "SKIPPED"
        return len(dropped)

    def _release_queue(self, body, owner, action_id):
        """The gating action succeeded: propose the next queued step against the post-execution state."""
        queue = body.get("queued", [])
        index = next((i for i, item in enumerate(queue) if item["after"] == action_id), None)
        if index is None:
            return [], []
        item = queue.pop(index)
        node = next((n for n in (body.get("plan") or {}).get("nodes", []) if n["id"] == item["node_id"]), {})
        follower = queue[index] if index < len(queue) and queue[index]["after"] is None else None
        step = item["step"]
        try:
            self._supersede(body, owner, step["normalized"])
            task = self._run(item["goal"], owner, item["account"], step["normalized"], item["origin"] | {"released_after": action_id})
        except Rejected as exc:
            task, cards, texts = None, [], [f"后续步骤“{node.get('title', '操作')}”没有生成：" + self._error(exc.code)]
        else:
            cards, texts = CardRenderer(self.s, owner, self.catalog(owner)).task_cards(task, step)
            node["task_id"] = task["task_id"]
            texts = ["下一步：" + (texts[0] if texts else "请确认。")] + texts[1:]
        confirm = [c for c in cards if c["type"] == "confirm"]
        for card in confirm:
            body["pending"].append({"task_id": task["task_id"], "action_id": card["action_id"], "card": card, "memo": step.get("memo"),
                                    "task_type": step["normalized"]["task_type"], "key": self._key(step["normalized"])})
            node.setdefault("action_ids", []).append(card["action_id"])
        node["status"] = "AWAITING_CONFIRMATION" if confirm else "FAILED"
        if follower is not None:
            follower["after"] = confirm[-1]["action_id"] if confirm else "__dropped__"
            if not confirm:
                self._drop_queue(body, "__dropped__")
        self.s.audit.append(task and task["task_id"], None, "CHAT_QUEUED_STEP_RELEASED", "chat",
                            {"session_id": body["session_id"], "after_action": action_id, "proposed": bool(confirm)})
        return cards, texts

    def _risk(self, body, owner, hit, tools, mode="guard"):
        event = self.s.audit.append(None, None, "CHAT_RISK_FLAGGED", "chat",
                                    {"session_id": body["session_id"], "category": hit["category"],
                                     "evidence_hash": digest(hit.get("evidence", ""))})
        text = hit["message"]
        body["clarify"] = None
        if hit["category"] == "PROMPT_INJECTION":
            body["strikes"] = body.get("strikes", 0) + 1
            if body["strikes"] >= STRIKE_LIMIT:
                body["locked_until"] = time.time() + LOCK_SECONDS
                self._cancel_pending(body, owner)
                hit = hit | {"category": "ACCOUNT_LOCKED", "cooldown_seconds": LOCK_SECONDS}
                text += "多次尝试绕过安全规则，本会话已锁定 10 分钟，待确认的操作已全部取消。"
                self.s.audit.append(None, None, "CHAT_SESSION_LOCKED", "chat", {"session_id": body["session_id"]})
        if hit["category"] == "FRAUD_SUSPECTED":
            self._cancel_pending(body, owner)
            text += "如果你担心自己正被人诱导转账，可以点“开启 24 小时转账保护”：保护期内对外转账暂停，只能由银行人员核实后解除。"
        card = CardRenderer.risk_card(hit, event)
        if hit["category"] == "FRAUD_SUSPECTED":
            card["offer"] = {"type": "ENABLE_PROTECTION", "hours": 24, "label": "开启 24 小时转账保护"}
        return {"text": text, "cards": [card], "mode": mode, "tools": tools, "risk": hit["category"]}

    # ---- understanding -----------------------------------------------------------------------
    BREAKER_FAILURES, BREAKER_WINDOW, BREAKER_OPEN_SECONDS = 3, 60, 120
    LLM_GATE_WAIT = 2.0
    LLM_DAILY_CALLS = int(os.environ.get("TRUSTA_CHAT_LLM_DAILY_CALLS", "300"))

    def _understand(self, body, owner, text, catalog):
        if not getattr(self.llm, "available", False):
            raise LLMUnavailable("LLM_NOT_CONFIGURED")
        now = time.time()
        if now < getattr(self, "_breaker_open_until", 0):
            raise LLMUnavailable("LLM_CIRCUIT_OPEN")  # model kept failing: serve from rules, retry after cool-down
        # Concurrency gate: a slow upstream must not pin every server thread; overflow is served by the rules engine.
        if not self._llm_gate.acquire(timeout=self.LLM_GATE_WAIT):
            raise LLMUnavailable("LLM_BUSY")
        try:
            # Per-owner daily call budget (persisted): bounds cost and abuse; the bank keeps working on rules.
            if not self.store.reserve_llm_call(owner, self.LLM_DAILY_CALLS):
                raise LLMUnavailable("LLM_BUDGET_EXHAUSTED")
            return self._understand_once(body, owner, text, catalog)
        except LLMUnavailable as exc:
            if exc.code in {"LLM_BUDGET_EXHAUSTED"}:
                raise
            failures = [t for t in getattr(self, "_llm_failures", []) if now - t < self.BREAKER_WINDOW] + [now]
            self._llm_failures = failures
            if len(failures) >= self.BREAKER_FAILURES:
                self._breaker_open_until, self._llm_failures = now + self.BREAKER_OPEN_SECONDS, []
                self.s.audit.append(None, None, "CHAT_LLM_CIRCUIT_OPEN", "chat", {"reason": exc.code, "seconds": self.BREAKER_OPEN_SECONDS})
            raise
        finally:
            self._llm_gate.release()

    def _understand_once(self, body, owner, text, catalog):
        history = []
        for message in body["messages"][-12:]:
            if message["role"] in {"user", "assistant"} and message.get("text"):
                content = message["text"][:500]
                if message is not body["messages"][-1] and guard.inspect(content):
                    # Earlier blocked attempts (or bank data echoed in a reply) are not replayed to the model.
                    content = "[已拦截的消息]" if message["role"] == "user" else "[系统回复，内容已省略]"
                history.append({"role": message["role"], "content": redact_for_model(content)})
        pending = "；".join(p["card"]["title"] + "（" + "，".join(f["value"] for f in p["card"]["fields"][:2]) + "）"
                           for p in body["pending"])
        prompt = build_system_prompt(STATE_LABELS.get(self._state(body), "空闲"), pending, catalog)
        disabled = set(self.capabilities.disabled_tools(owner))
        result = self.llm.complete(prompt, history, tools=openai_tools(disabled)) if disabled else self.llm.complete(prompt, history)
        usage = result.get("usage") or {}
        self.store.record_llm_tokens(owner, usage.get("total_tokens"))
        calls = [c for c in result["calls"] if isinstance(c, dict) and isinstance(c.get("name"), str)]
        if not calls:
            content = (result.get("text") or "").strip()
            # The model answered in prose although the request is actionable ("我的卡丢了怎么办" → "需要我帮你挂失吗？").
            # Prose never authorises or prepares anything, so prefer the deterministic grammar when it recognises an action.
            from backend.chat.rules import rule_calls
            actionable = [c for c in rule_calls(text, catalog) if c["name"] not in {"smalltalk", "ask_user", "knowledge", "navigate"}]
            if actionable:
                return actionable
            # Plain model text is shown only when it carries no numbers it could have invented and does not
            # describe / offer an operation in words (the confirmation card is the only place an operation is offered).
            if content and not re.search(r"[0-9０-９]", content) and len(content) <= 200 and not OFFER_TEXT.search(content):
                return self._plain(content)
            if content and OFFER_TEXT.search(content):
                return [{"name": "ask_user", "arguments": {"slot": "operation",
                                                           "question": "请直接告诉我要办理的操作，例如“挂失我的卡”“冻结卡片”，我会生成确认卡给你核对。"}}]
            return [{"name": "out_of_scope", "arguments": {}}]
        return calls

    @staticmethod
    def _plain(content):
        # Represented as a control step the validator knows: ask_user carries the model's question verbatim.
        return [{"name": "ask_user", "arguments": {"slot": "other", "question": content[:120]}}]

    @staticmethod
    def _rule_control(text):
        if CANCEL_WORDS.match(text):
            return [{"kind": "control", "tool": "cancel_pending", "args": {}}]
        if HUMAN_WORDS.search(text) and not re.search(r"就|的话|触发|自动", text):  # "…就冻结卡并转人工" is a rule, not a request now
            return [{"kind": "control", "tool": "request_human", "args": {}}]
        if UNDO_WORDS.search(text):
            return [{"kind": "control", "tool": "undo_last", "args": {}}]
        return []

    def _rule_steps(self, body, text, catalog):
        """Last resort when neither the model nor the colloquial rules matched: the original finite grammar."""
        intent = self.s.intent.normalize(text)
        previous = (body.get("clarify") or {}).get("text")
        if intent.get("needs_clarification") and previous:
            combined = self.s.intent.normalize(previous + " " + text)
            if not combined.get("needs_clarification"):
                intent = combined
        if intent.get("needs_clarification"):
            if intent["task_type"] == "TRANSFER" and "转" not in text:
                return [], []
            return [], [Clarify("RULE_CLARIFICATION", "other", intent["clarification"])]
        kind = intent["task_type"]
        read = kind in {"BILL_ANALYSIS", "WEALTH_RECOMMENDATION", "PRODUCT_COMPARE"} or intent.get("operation") in {"LIST", "DETECT"}
        return [{"kind": "read" if read else "propose", "tool": "rules:" + kind.lower(), "normalized": intent}], []

    # ---- decisions ---------------------------------------------------------------------------
    def decide(self, session_id, owner, action_id, action_hash, approve, verification=None):
        if type(approve) is not bool or not isinstance(action_hash, str):
            raise Rejected("INVALID_INPUT")
        secret = ""
        if verification is not None:
            if (not isinstance(verification, dict) or set(verification) != {"method", "code"}
                    or verification["method"] not in {"TOTP", "SMS", "DEMO_SECRET"} or not isinstance(verification["code"], str)
                    or not 0 < len(verification["code"]) <= 64):
                raise Rejected("INVALID_VERIFICATION")
            secret = ("sms:" if verification["method"] == "SMS" else "") + verification["code"]
        body = self.store.get(session_id, owner)
        entry = next((p for p in body["pending"] if p["action_id"] == action_id), None)
        if not entry:
            raise Rejected("ACTION_NOT_PENDING")
        if entry["card"]["action_hash"] != action_hash:
            raise Rejected("ACTION_HASH_MISMATCH")
        task = self.s.get_task(entry["task_id"], owner)
        try:
            if "action_records" in task:
                task = self.s.banking.authorize(entry["task_id"], owner, action_hash, approve, secret, action_id)
            else:
                task = self.s.authorize(entry["task_id"], owner, action_hash, approve, secret)
        except Rejected as exc:
            retry = exc.code.startswith(("TOTP_", "STRONG_AUTH", "SMS_")) and exc.code != "TOTP_NOT_ENROLLED"
            texts = {"TOTP_NOT_ENROLLED": "你还没有绑定认证器，可以改用短信验证码完成验证。",
                     "SMS_NOT_SENT_OR_EXPIRED": "短信验证码未发送或已过期，请重新获取。",
                     "BANKING_SECURITY_LOCKED": "验证失败次数过多，账户已安全锁定，请联系客服解锁。"}
            text = texts.get(exc.code, "验证码不正确或已过期，请重新输入。" if retry else "这项操作已失效，请重新发起。")
            if not retry and exc.code not in {"TOTP_NOT_ENROLLED"}:
                body["pending"] = [p for p in body["pending"] if p["action_id"] != action_id]
            self.s.audit.append(entry["task_id"], action_id, "CHAT_DECISION_REJECTED", "chat",
                                {"session_id": session_id, "code": exc.code})
            body["messages"].append({"role": "assistant", "message_id": "decision-" + action_id[:12] + "-" + str(body["revision"]),
                                     "text": text, "cards": [], "at": time.time()})
            body = self.store.save(body)
            return {"session_id": session_id, "revision": body["revision"], "state": self._state(body),
                    "reply": {"text": text, "cards": []}, "plan": None, "error": exc.code}
        renderer = CardRenderer(self.s, owner, self.catalog(owner))
        still = {a["action_id"] for a, _, _ in renderer.pending_actions(task)}
        cards = [] if action_id in still else renderer.result_cards(task, action_id)
        if cards:
            self._record_outcome(body, task, action_id, cards[0]["status"])
        if action_id in still:
            text = "验证没有通过，请重新输入验证码。" if approve else "操作仍在等待确认。"
        else:
            if cards and entry.get("memo"):
                cards[0]["message"] += f"备注：{entry['memo']}。"
            if cards and cards[0]["status"] in {"CANCELLED", "FAILED"} and entry.get("linked_events"):
                self._cancel_events(owner, entry["linked_events"])
                cards[0]["message"] += "相关的后续计划也已取消。"
            if cards and cards[0]["status"] == "SUCCEEDED" and entry["card"]["action_type"] == "CARD_PIN_RESET":
                cards[0]["message"] = "已向你的预留手机号发送设置新密码的安全链接（15 分钟内有效）。新密码只在银行页面输入，不会经过我。"
            text = cards[0]["message"] if cards else "已处理。"
            body["pending"] = [p for p in body["pending"] if p["action_id"] != action_id]
            if cards and cards[0]["status"] == "SUCCEEDED":
                more_cards, more_texts = self._release_queue(body, owner, action_id)
                cards += more_cards
                text += "".join(t if t.endswith(("。", "？", "！")) else t + "。" for t in more_texts)
            elif self._drop_queue(body, action_id):
                text += "依赖这一步的后续操作也已取消。"
        body["pending"] = [p for p in body["pending"] if p["action_id"] != action_id or p["action_id"] in still]
        message_id = "decision-" + action_id[:12] + "-" + str(body["revision"])
        body["messages"].append({"role": "assistant", "message_id": message_id, "text": text, "cards": cards, "at": time.time()})
        body = self.store.save(body)
        return {"session_id": session_id, "message_id": message_id, "revision": body["revision"], "state": self._state(body),
                "reply": {"text": text, "cards": cards}, "plan": body.get("plan")}

    # ---- plan / execution bookkeeping ---------------------------------------------------------
    TOOL_TITLES = {"get_balance": "查询余额", "analyze_spending": "账单分析", "query_spending": "消费查询", "get_portfolio": "理财持仓", "recommend_products": "理财推荐", "compare_products": "产品对比",
                   "list_subscriptions": "查询订阅", "list_direct_debits": "查询代扣", "list_cards": "查询银行卡",
                   "renewal_reminders": "续费提醒", "start_risk_assessment": "风险测评", "list_wish_funds": "查询心愿金",
                   "review_subscriptions": "识别可取消的订阅", "list_automations": "查询联动规则", "list_income": "查询入账",
                   "propose_automation": "创建联动规则", "propose_trip_plan": "出行管家",
                   "trip:redeem": "赎回理财补足预算", "trip:international_on": "开通境外交易"}

    def _step_title(self, step):
        normalized = step.get("normalized") or {}
        if step.get("tool") in self.TOOL_TITLES:
            return self.TOOL_TITLES[step["tool"]]
        operation = normalized.get("operation") or normalized.get("task_type")
        title = OPERATIONS.get(operation, OPERATIONS.get(normalized.get("task_type"), "操作"))
        display = step.get("display") or {}
        detail = " ".join(str(display[k]) for k in ("payee", "amount", "card", "subscription", "product") if k in display)
        return f"{title} {detail}".strip()

    def _record_outcome(self, body, task, action_id, status):
        for node in (body.get("plan") or {}).get("nodes", []):
            if action_id in node.get("action_ids", []):
                node["status"] = status
        if status != "SUCCEEDED":
            return
        if "action_records" in task:
            action = next(e["action"] for e in task["action_records"] if e["action"]["action_id"] == action_id)
        else:
            action = task["proposed_actions"][0]
        body.setdefault("executed", []).append({
            "task_id": task["task_id"], "action_id": action_id, "action_type": action["action_type"],
            "target_id": (action.get("target") or {}).get("id"), "account_id": action.get("source_account_id"),
            "parameters": action.get("parameters") or {}, "before": (action.get("preconditions") or {}).get("target"),
            "at": time.time(), "undo": None})
        del body["executed"][:-20]

    IRREVERSIBLE = {
        "TRANSFER": "转账已经到账，银行无法单方面撤回。可以联系收款人退回，或者我帮你向对方发起一笔收款请求。",
        "WISH_ORDER": "商户订单已支付，需要联系商户办理退款。",
        "SUBSCRIPTION_CANCEL": "订阅已取消，需要在商户那里重新开通。",
        "DIRECT_DEBIT_DISABLE": "代扣已关闭，需要在缴费机构重新签约。",
        "CARD_PIN_RESET": "密码重置链接已发出；不使用链接，原密码继续有效。",
        "CARD_APPLY": "卡片申请已提交，如需撤回请联系客服。",
        "RISK_PROFILE_UPDATE": "风险等级只能通过重新测评更新，不能直接改回。",
        "FUND_RELEASE": "心愿金已释放，如需要可以重新锁定一笔。",
    }

    def _undo_steps(self, body, owner):
        """回退：for the latest executed action, propose its inverse. The inverse is a NEW action that still needs consent."""
        done = [e for e in body.get("executed", []) if e["undo"] is None]
        if not done:
            return [], "这个会话里还没有已执行的操作可以撤销。"
        last = done[-1]
        kind, target, params, before = last["action_type"], last["target_id"], last["parameters"], last["before"] or {}
        card = lambda op, values=None: {"task_type": "CARD_MANAGEMENT", "operation": op, "target_id": target,
                                        "parameters": values or {}, "needs_clarification": False}
        inverse = {
            "CARD_FREEZE": lambda: card("CARD_UNFREEZE"), "CARD_UNFREEZE": lambda: card("CARD_FREEZE"),
            "CARD_REPORT_LOST": lambda: card("CARD_CANCEL_LOST"), "CARD_CANCEL_LOST": lambda: card("CARD_REPORT_LOST"),
            "CARD_LIMIT_CHANGE": lambda: card("CARD_LIMIT_CHANGE", {"spending_limit": before["spending_limit"]}),
            "CARD_TRANSACTION_CONTROL": lambda: card("CARD_TRANSACTION_CONTROL", {
                "online_enabled": bool(before["online_enabled"]), "international_enabled": bool(before["international_enabled"]),
                "contactless_enabled": bool(before["contactless_enabled"])}),
            "FUND_LOCK": lambda: {"task_type": "WISH_FUND_MANAGEMENT", "operation": "FUND_RELEASE", "target_id": target,
                                  "parameters": {}, "needs_clarification": False},
            "INVESTMENT_PURCHASE": lambda: {"task_type": "INVESTMENT_REDEEM", "operation": "INVESTMENT_REDEEM", "target_id": target,
                                            "parameters": dict(params), "needs_clarification": False},
            "INVESTMENT_REDEEM": lambda: {"task_type": "INVESTMENT_PURCHASE", "operation": "INVESTMENT_PURCHASE", "target_id": target,
                                          "parameters": dict(params), "needs_clarification": False},
        }.get(kind)
        last["undo"] = "PROPOSED" if inverse else "IRREVERSIBLE"
        self.s.audit.append(last["task_id"], last["action_id"], "CHAT_UNDO_REQUESTED", "chat",
                            {"session_id": body["session_id"], "reversible": bool(inverse)})
        if not inverse:
            return [], f"刚才的“{OPERATIONS.get(kind, kind)}”不能直接撤销：" + self.IRREVERSIBLE.get(kind, "请联系客服处理。")
        return ([{"kind": "propose", "tool": "undo_last", "normalized": inverse()}],
                f"可以撤销刚才的“{OPERATIONS.get(kind, kind)}”。下面是反向操作，确认后生效。")

    # ---- human takeover ------------------------------------------------------------------------
    def _open_handoff(self, body, owner, reason, mode, tools):
        from backend.domain import uid, canonical
        handoff_id = "H-" + uid()[:8].upper()
        transcript = [{"role": m["role"], "text": m.get("text", "")[:300]} for m in body["messages"][-10:]]
        case = {"id": handoff_id, "owner": owner, "session_id": body["session_id"], "status": "OPEN", "reason": str(reason)[:120],
                "created_at": time.time(), "transcript": transcript, "pending": [p["card"]["title"] for p in body["pending"]],
                "queue": []}
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO chat_handoffs VALUES(?,?,?,?,?,?)",
                         (handoff_id, owner, body["session_id"], "OPEN", case["created_at"], canonical(case)))
            self.s.audit.append_in(conn, None, None, "CHAT_HANDOFF_OPENED", "chat", {"session_id": body["session_id"], "handoff_id": handoff_id})
        body["handoff"] = {"id": handoff_id, "status": "OPEN"}
        text = (f"已为你转接人工客服（工单 {handoff_id}），客服会看到我们刚才的对话记录。"
                "人工客服可以解答和协助，但资金操作仍然需要你本人在确认卡上确认。想回到智能助手，说“取消转人工”即可。")
        return {"text": text, "cards": [{"type": "handoff", "card_id": "c_" + handoff_id, "handoff_id": handoff_id, "status": "OPEN"}],
                "mode": mode, "tools": tools}

    def _handoff_message(self, body, owner, text):
        handoff_id = body["handoff"]["id"]
        if BACK_TO_AGENT.search(text):
            self._close_handoff_row(handoff_id, "CANCELLED_BY_USER")
            body["handoff"] = {"id": handoff_id, "status": "CLOSED"}
            return {"text": "好的，已回到智能助手，有什么可以帮你？", "cards": [], "mode": "human", "tools": []}
        hit = guard.inspect(text)
        with self.s.db.transaction() as conn:
            row = conn.execute("SELECT body FROM chat_handoffs WHERE id=?", (handoff_id,)).fetchone()
            import json
            case = json.loads(row["body"])
            case["queue"].append({"text": text[:500], "at": time.time(), "risk": hit and hit["category"]})
            case["queue"] = case["queue"][-50:]
            from backend.domain import canonical
            conn.execute("UPDATE chat_handoffs SET body=? WHERE id=?", (canonical(case), handoff_id))
        return {"text": f"已转给人工客服（工单 {handoff_id}），请稍候，客服回复会显示在这里。", "cards": [], "mode": "human", "tools": []}

    def _close_handoff_row(self, handoff_id, status):
        import json
        from backend.domain import canonical
        with self.s.db.transaction() as conn:
            row = conn.execute("SELECT body FROM chat_handoffs WHERE id=?", (handoff_id,)).fetchone()
            case = json.loads(row["body"])
            case["status"] = status
            conn.execute("UPDATE chat_handoffs SET status=?, body=? WHERE id=?", ("CLOSED", canonical(case), handoff_id))
            self.s.audit.append_in(conn, None, None, "CHAT_HANDOFF_CLOSED", "chat", {"handoff_id": handoff_id, "status": status})

    def _operator(self, token):
        import hmac
        expected = self.s.config.security_operator_token
        if not expected or not isinstance(token, str) or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Rejected("SECURITY_OPERATOR_REQUIRED")

    def operator_handoffs(self, token):
        import json
        self._operator(token)
        return {"handoffs": [json.loads(r["body"]) for r in self.s.db.rows(
            "SELECT body FROM chat_handoffs WHERE status='OPEN' ORDER BY created_at")]}

    def operator_reply(self, token, handoff_id, text, close=False):
        """A human agent can talk to the customer; it cannot approve anything (no authorization API is exposed here)."""
        self._operator(token)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise Rejected("INVALID_INPUT")
        row = self.s.db.one("SELECT owner, session_id, status FROM chat_handoffs WHERE id=?", (handoff_id,))
        if not row or row["status"] != "OPEN":
            raise Rejected("HANDOFF_NOT_OPEN")
        body = self.store.get(row["session_id"], row["owner"])
        body["messages"].append({"role": "human_agent", "message_id": f"human-{handoff_id}-{len(body['messages'])}",
                                 "text": text.strip(), "cards": [], "at": time.time()})
        self.s.audit.append(None, None, "CHAT_HANDOFF_REPLY", "local_operator", {"handoff_id": handoff_id, "text_hash": digest(text)})
        if close:
            self._close_handoff_row(handoff_id, "RESOLVED")
            body["handoff"] = {"id": handoff_id, "status": "CLOSED"}
            body["messages"].append({"role": "assistant", "message_id": f"human-{handoff_id}-end", "cards": [], "at": time.time(),
                                     "text": "人工客服已结束本次服务，智能助手继续为你服务。"})
        body = self.store.save(body)
        return {"handoff_id": handoff_id, "status": "CLOSED" if close else "OPEN", "session_revision": body["revision"]}

    def trace(self, session_id, owner, message_id):
        """完整决策链路: words -> guard -> model tool calls + validator verdicts -> policy -> authorization ->
        execution -> verification -> receipt, plus the audit events and a tamper check of the trace itself."""
        import json
        body = self.store.get(session_id, owner)
        record = (body["turns"].get(message_id) or {}).get("trace")
        if not record:
            raise Rejected("TRACE_NOT_FOUND")
        stored = {k: v for k, v in record.items() if k != "audit_event_id"}
        row = self.s.db.one("SELECT body FROM audit_events WHERE json_extract(body,'$.audit_event_id')=?", (record["audit_event_id"],))
        anchored = bool(row) and json.loads(row["body"])["payload_hash"] == digest(stored)
        tasks = []
        for step in record["steps"]:
            task_id = step["node"].get("task_id")
            if not task_id:
                continue
            task = self.s.get_task(task_id, owner)
            records = task.get("action_records")
            if records is None:  # legacy transfer task shape
                records = [{"action": a, "policy": task.get("policy_decision"), "authorization": task.get("authorization"),
                            "execution": task.get("execution"), "verification": task.get("verification"), "receipt": task.get("receipt"),
                            "status": task["status"]} for a in task.get("proposed_actions", [])]
            actions = []
            for entry in records:
                policy, auth = entry.get("policy") or {}, entry.get("authorization") or {}
                actions.append({"action_id": entry["action"]["action_id"], "action_type": entry["action"]["action_type"],
                                "action_hash": digest(entry["action"]), "status": entry.get("status"),
                                "policy": {k: policy.get(k) for k in ("decision", "risk_level", "reason_codes")},
                                "authorization": {k: auth.get(k) for k in ("status", "auth_method", "expires_at")},
                                "verification": {k: (entry.get("verification") or {}).get(k) for k in ("status", "reason")},
                                "receipt_id": (entry.get("receipt") or {}).get("receipt_id")})
            events = [json.loads(r["body"]) for r in self.s.db.rows(
                "SELECT body FROM audit_events WHERE json_extract(body,'$.task_id')=? ORDER BY seq", (task_id,))]
            tasks.append({"task_id": task_id, "task_type": task["task_type"], "status": task["status"], "actions": actions,
                          "agent_messages": self._agent_messages(task_id),
                          "audit_events": [{k: e[k] for k in ("audit_event_id", "event_type", "actor", "timestamp", "event_hash")} for e in events]})
        return {"session_id": session_id, "message_id": message_id, "trace": record, "trace_anchored_in_audit": anchored,
                "tasks": tasks, "audit_chain_valid": self.s.audit.verify_chain()}

    def _agent_messages(self, task_id):
        """Every inter-agent message of a task, re-verified now: the sender's ECDSA signature over the V3 envelope and the
        runtime's countersignature over the causal handoff evidence (parents = hashes of earlier evidence)."""
        import json
        from backend.runtime.handoff_evidence import DOMAIN as EVIDENCE_DOMAIN
        from backend.security.crypto import verify_object
        from backend.security.message_v3 import MESSAGE_DOMAIN
        registry = self.s.crypto_registry
        if registry is None:
            return []
        def valid(key_id, domain, body, signature):
            try:
                with self.s.db.transaction() as conn:
                    key = registry._key(key_id, conn)
                verify_object(key["public_key"], domain, body, signature)
                return True
            except Rejected:
                return False
        out, previous = [], []
        for row in self.s.db.rows("SELECT body FROM agent_handoff_evidence WHERE task_id=? ORDER BY rowid", (task_id,)):
            stored = json.loads(row["body"])
            evidence = stored["evidence"]
            message = evidence["message"]
            out.append({"step_id": message["step_id"], "sender": message["sender_agent"], "receiver": message["receiver_agent"],
                        "type": message["message_type"], "sender_identity": message["sender_identity"], "key_id": message["sender_key_id"],
                        "payload_hash": message["payload_hash"], "capability_snapshot_hash": message["capability_snapshot_hash"],
                        "issued_at": message["issued_at"], "nonce": message["nonce"],
                        "message_signature_valid": valid(message["sender_key_id"], MESSAGE_DOMAIN,
                                                         {k: v for k, v in message.items() if k != "signature"}, message["signature"]),
                        "evidence_signature_valid": valid(evidence["signer_key_id"], EVIDENCE_DOMAIN,
                                                          {k: v for k, v in evidence.items() if k != "signature"}, evidence["signature"]),
                        "causal_parents_known": all(p in previous for p in evidence["parents"])})
            previous.append(digest(stored))  # the runtime links each handoff to the hash of earlier stored evidence
        return out

    def evidence_bundle(self, session_id, owner, task_id):
        """Offline-verifiable bundle (scripts/verify_handoff_evidence.py) + the public registry to pin out of band."""
        body = self.store.get(session_id, owner)
        linked = {step["node"].get("task_id") for turn in body["turns"].values() for step in (turn.get("trace") or {}).get("steps", [])}
        if task_id not in linked:
            raise Rejected("TRACE_NOT_FOUND")
        return {"bundle": self.s.handoffs.export(task_id, owner), "public_registry": self.s.crypto_registry.snapshot(),
                "verify_with": "python -m scripts.verify_handoff_evidence bundle.json --trusted-registry registry.json"}

    def send_sms(self, session_id, owner, action_id):
        """Simulated SMS second factor for a RED card. The code is bound to that card's authorization only."""
        import os
        body = self.store.get(session_id, owner)
        entry = next((p for p in body["pending"] if p["action_id"] == action_id), None)
        if not entry:
            raise Rejected("ACTION_NOT_PENDING")
        task = self.s.get_task(entry["task_id"], owner)
        if "action_records" in task:
            record = next((e for e in task["action_records"] if e["action"]["action_id"] == action_id), None)
            auth = record and record.get("authorization")
        else:
            auth = task.get("authorization")
        if not auth:
            raise Rejected("ACTION_NOT_PENDING")
        return self.s.safety.issue_sms(owner, auth, demo=os.environ.get("TRUSTA_DEMO_MODE", "false").lower() == "true")

    def interrupt(self, session_id, owner, revision):
        body = self.store.get(session_id, owner)
        if type(revision) is not int or revision != body["revision"]:
            raise Rejected("CHAT_STALE")
        cancelled = self._cancel_pending(body, owner)
        body["clarify"] = None
        for node in (body.get("plan") or {}).get("nodes", []):
            if node["status"] in {"AWAITING_CONFIRMATION", "NEEDS_INPUT", "SCHEDULED", "PENDING"}:
                node["status"] = "CANCELLED"
        text = (f"已中断，取消了 {cancelled} 项未执行的操作。已经执行完成的操作不会自动回滚；"
                "如需撤销，可以说“撤销刚才的操作”，我会生成反向操作给你确认。")
        body["messages"].append({"role": "assistant", "message_id": "interrupt-" + str(body["revision"]), "text": text,
                                 "cards": [], "at": time.time()})
        body = self.store.save(body)
        return {"session_id": session_id, "revision": body["revision"], "state": self._state(body),
                "reply": {"text": text, "cards": []}, "plan": body.get("plan")}


assert {"ask_user", "cancel_pending", "flag_risk", "smalltalk", "out_of_scope"} <= set(TOOLS)
