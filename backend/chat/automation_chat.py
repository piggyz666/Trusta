"""Chat-side glue for 联动规则, 出行管家, 入账/隔离 and 反诈冷静期 (mixed into ChatService).

Everything here reuses the existing proposal path: a money action is a normal task in the six-agent runtime and gets a
normal confirmation card. The only thing that runs without a fresh tap is a *tightening* card action under a standing
consent the customer gave on that exact rule, and only after it is re-checked here (tightening + YELLOW).
"""
import os
import time
from backend.automation.engine import CONTROL_LABELS, TIGHTENING
from backend.chat.amounts import format_cents
from backend.chat.cards import CardRenderer, OPERATIONS
from backend.domain import Rejected, digest, uid

DEMO = lambda: os.environ.get("TRUSTA_DEMO_MODE", "false").lower() == "true"


class AutomationMixin:
    # ---- drafting -------------------------------------------------------------------------------
    def _automation_card(self, bundle_id, bundle_hash, rules, catalog):
        engine = self.s.automation
        names = {i["id"]: i["display"] for key in ("products", "cards") for i in catalog.get(key, [])}
        items = [{"rule_id": r["id"], "name": r["name"], "trigger": engine.describe_trigger(r["trigger"]),
                  "actions": [engine.describe_action(a, r["auto_tighten"], names) for a in r["actions"]]} for r in rules]
        notes = ["规则确认后生效，可随时说“停用规则”关闭", "触发时涉及资金的操作仍会逐笔请你确认，大额需要验证码"]
        if any(r["auto_tighten"] for r in rules):
            notes.append("冻结卡 / 关闭交易开关属于只收紧风险的操作，触发时将自动执行并通知你")
        return {"type": "automation_confirm", "card_id": "c_ar_" + uid()[:8], "bundle_id": bundle_id, "bundle_hash": bundle_hash,
                "title": "确认联动规则", "rules": items, "risk_level": "YELLOW", "notes": notes,
                "expires_at": min(r["draft_expires_at"] for r in rules)}

    def _draft_automation(self, body, owner, account, specs, auto_tighten, catalog, source=None):
        bundle_id, bundle_hash, rules = self.s.automation.draft(owner, account, specs, auto_tighten,
                                                               source or {"session_id": body["session_id"]})
        card = self._automation_card(bundle_id, bundle_hash, rules, catalog)
        body.setdefault("pending_rules", []).append({"bundle_id": bundle_id, "card": card})
        lines = "；".join(f"{i['trigger']}，{'、'.join(i['actions'])}" for i in card["rules"])
        return card, f"我按你的意思整理成联动规则：{lines}。请在卡片上确认后生效"

    def decide_rule(self, session_id, owner, bundle_id, bundle_hash, approve):
        if type(approve) is not bool or not isinstance(bundle_hash, str):
            raise Rejected("INVALID_INPUT")
        body = self.store.get(session_id, owner)
        entry = next((p for p in body.get("pending_rules", []) if p["bundle_id"] == bundle_id), None)
        if not entry:
            raise Rejected("ACTION_NOT_PENDING")
        if approve:
            rules = self.s.automation.activate(owner, bundle_id, bundle_hash)
            text = f"已生效 {len(rules)} 条联动规则。触发时我会提醒你，资金操作仍需你逐笔确认。"
            status = "ACTIVE"
        else:
            self.s.automation.cancel(owner, bundle_id, "DECLINED")
            text, status = "好的，没有创建这条规则。", "CANCELLED"
        body["pending_rules"] = [p for p in body["pending_rules"] if p["bundle_id"] != bundle_id]
        for node in (body.get("plan") or {}).get("nodes", []):
            if node.get("bundle_id") == bundle_id:
                node["status"] = "SUCCEEDED" if approve else "CANCELLED"
        card = {"type": "result", "card_id": "c_" + uid()[:8], "bundle_id": bundle_id, "status": status, "message": text}
        message_id = "rule-" + bundle_id[-10:] + "-" + str(body["revision"])
        body["messages"].append({"role": "assistant", "message_id": message_id, "text": text, "cards": [card], "at": time.time()})
        body = self.store.save(body)
        return {"session_id": session_id, "message_id": message_id, "revision": body["revision"], "state": self._state(body),
                "reply": {"text": text, "cards": [card]}, "plan": body.get("plan")}

    def _cancel_rule_drafts(self, body, owner):
        count = 0
        for entry in body.get("pending_rules", []):
            try:
                count += self.s.automation.cancel(owner, entry["bundle_id"], "SESSION_CANCELLED")
            except Rejected:
                pass
        body["pending_rules"] = []
        return count

    # ---- intents for actions --------------------------------------------------------------------
    def _card_state(self, owner, card_id):
        return self.s.gateway.read("service", owner, "card.get", resource_id=card_id)

    def _intent_for(self, owner, action, now=None):
        now = now or time.time()
        kind = action["type"]
        if kind == "TRANSFER":
            return {"task_type": "TRANSFER", "recipient_name": action["payee_name"], "amount": action["amount"], "currency": "CNY",
                    "needs_clarification": False}
        if kind == "WISH_LOCK":
            return {"task_type": "WISH_FUND_MANAGEMENT", "operation": "FUND_LOCK", "target_id": "wish-" + uid().replace("-", "")[:12],
                    "parameters": {"amount": action["amount"], "currency": "CNY", "name": action.get("fund_name", "储蓄"),
                                   "release_at": now + 90 * 86400}, "needs_clarification": False}
        if kind == "INVEST":
            return {"task_type": "INVESTMENT_PURCHASE", "operation": "INVESTMENT_PURCHASE", "target_id": action["product_id"],
                    "parameters": {"amount": action["amount"], "currency": "CNY"}, "needs_clarification": False}
        if kind == "REDEEM":
            return {"task_type": "INVESTMENT_REDEEM", "operation": "INVESTMENT_REDEEM", "target_id": action["product_id"],
                    "parameters": {"amount": action["amount"], "currency": "CNY"}, "needs_clarification": False}
        if kind == "CARD_FREEZE":
            return {"task_type": "CARD_MANAGEMENT", "operation": "CARD_FREEZE", "target_id": action["card_id"], "parameters": {},
                    "needs_clarification": False}
        if kind == "CARD_CONTROL":
            current = self._card_state(owner, action["card_id"])
            params = {f"{k}_enabled": bool(current[f"{k}_enabled"]) for k in CONTROL_LABELS}
            params.update({f"{k}_enabled": v for k, v in action["controls"].items()})
            return {"task_type": "CARD_MANAGEMENT", "operation": "CARD_TRANSACTION_CONTROL", "target_id": action["card_id"],
                    "parameters": params, "needs_clarification": False}
        raise Rejected("AUTOMATION_INVALID_ACTION")

    def _propose_steps(self, body, owner, account, steps, goal, origin, renderer):
        """Same semantics as a chat turn: every write becomes a confirmation card; a later money step waits for the
        previous money step to execute (queued), so its preconditions are taken on the post-execution balance."""
        cards, texts, gate, chained = [], [], None, False
        serial = len(steps) >= 2 and any(self._moves_balance(s["normalized"]) for s in steps)
        for step in steps:
            step["queued"] = False
            if serial and gate is not None:
                step["queued"] = True
                body.setdefault("queued", []).append({"after": None if chained else gate, "node_id": None, "step": step,
                                                      "goal": goal, "account": account, "origin": origin})
                chained = True
                texts.append(f"“{self._step_title(step)}”会在上一步完成后再请你确认")
                continue
            try:
                self._supersede(body, owner, step["normalized"])
                task = self._run(goal, owner, account, step["normalized"], origin)
            except Rejected as exc:
                texts.append(f"“{self._step_title(step)}”没有生成：" + self._error(exc.code))
                continue
            new_cards, new_texts = renderer.task_cards(task, step)
            for card in new_cards:
                if card["type"] == "confirm":
                    body["pending"].append({"task_id": task["task_id"], "action_id": card["action_id"], "card": card,
                                            "task_type": step["normalized"]["task_type"], "key": self._key(step["normalized"])})
                    if serial:
                        gate = card["action_id"]
            cards += new_cards
            texts += [t for t in new_texts if t not in texts]
        return cards, texts

    def _standing_tighten(self, body, owner, account, normalized, firing, renderer):
        """Run a tightening card action under the rule's standing consent, after re-checking it really tightens."""
        task = self._run("联动规则：" + firing["rule_name"], owner, account, normalized,
                         {"channel": "automation", "rule_id": firing["rule_id"], "firing_id": firing["id"], "rule_hash": firing["rule_hash"]})
        pending = renderer.pending_actions(task)
        if len(pending) != 1:
            return renderer.task_cards(task)
        action, authorization, policy = pending[0]
        params = action.get("parameters") or {}
        before = ((action.get("preconditions") or {}).get("target")) or {}
        tightening = (action["action_type"] == "CARD_FREEZE" or action["action_type"] == "CARD_TRANSACTION_CONTROL"
                      and all(not params[k] or before.get(k) for k in params))
        if not tightening or policy.get("risk_level") != "YELLOW":
            return renderer.task_cards(task)  # not provably tightening: ask the customer like any other action
        action_hash = digest(action)
        self.s.audit.append(task["task_id"], action["action_id"], "AUTOMATION_STANDING_CONSENT_USED", "automation",
                            {"rule_id": firing["rule_id"], "firing_id": firing["id"], "rule_hash": firing["rule_hash"],
                             "action_hash": action_hash})
        task = self.s.banking.authorize(task["task_id"], owner, action_hash, True, "", action["action_id"])
        result = renderer.result_cards(task, action["action_id"])
        if result and body is not None:
            self._record_outcome(body, task, action["action_id"], result[0]["status"])
        return result, [result[0]["message"] if result else "已执行。"]

    # ---- firings: phase A acts (worker or chat), phase B delivers into a conversation -------------------
    def run_automations(self, owner):
        """Evaluate rules and immediately carry out standing-consent tightening actions (card freeze / channel off),
        so protection does not wait for the customer to open the app. Money actions are left for delivery."""
        engine = getattr(self.s, "automation", None)
        if engine is None:
            return 0
        try:
            engine.evaluate(owner)
        except Rejected:
            return 0
        acted = 0
        for firing in engine.ready(owner):
            if firing.get("acted"):
                continue
            renderer = CardRenderer(self.s, owner, self.catalog(owner))
            outcomes = []
            for index, action in enumerate(firing["actions"]):
                if action["type"] in TIGHTENING and firing["auto_tighten"] and not any(action.get("controls", {}).values()):
                    try:
                        cards, texts = self._standing_tighten(None, owner, firing["account_id"], self._intent_for(owner, action), firing, renderer)
                    except Rejected as exc:
                        cards, texts = [], ["自动执行没有完成：" + self._error(exc.code)]
                    outcomes.append({"index": index, "cards": cards, "texts": texts})
            engine.update_firing(firing["id"], {"acted": True, "outcomes": outcomes})
            acted += 1
        return acted

    def _deliver_firings(self, body, owner):
        engine = getattr(self.s, "automation", None)
        if engine is None:
            return []
        self.run_automations(owner)
        added = []
        for firing in engine.ready(owner):
            renderer = CardRenderer(self.s, owner, self.catalog(owner))
            account = firing["account_id"]
            done = {o["index"]: o for o in firing.get("outcomes", [])}
            cards, texts, steps, handoff = [], [f"联动规则“{firing['rule_name']}”已触发：{firing['fact']['detail']}"], [], False
            origin = {"channel": "automation", "session_id": body["session_id"], "rule_id": firing["rule_id"],
                      "firing_id": firing["id"], "rule_hash": firing["rule_hash"]}
            for index, action in enumerate(firing["actions"]):
                if index in done:
                    cards += done[index]["cards"]
                    texts += done[index]["texts"]
                    continue
                if action["type"] == "NOTIFY":
                    if action.get("detail"):
                        texts.append(action["detail"])
                    continue
                if action["type"] == "HANDOFF":
                    handoff = True
                    continue
                if action.get("amount", 1) <= 0:
                    texts.append("金额为 0，已跳过一步")
                    continue
                try:
                    normalized = self._intent_for(owner, action)
                except Rejected as exc:
                    texts.append("有一步没有生成：" + self._error(exc.code))
                    continue
                steps.append({"kind": "propose", "tool": "automation:" + action["type"].lower(), "normalized": normalized})
            c, t = self._propose_steps(body, owner, account, steps, "联动规则：" + firing["rule_name"], origin, renderer)
            cards += c
            texts += t
            if handoff and self._state(body) != "HANDED_OFF":
                outcome = self._open_handoff(body, owner, f"联动规则触发：{firing['fact']['detail']}", "automation", [])
                texts.append(outcome["text"])
                cards += outcome.get("cards", [])
            engine.mark(firing["id"], "DELIVERED", {"session_id": body["session_id"]})
            added.append({"role": "assistant", "message_id": "rule-fire-" + firing["id"][-10:], "at": time.time(), "cards": cards,
                          "text": "".join(x if x.endswith(("。", "？", "！")) else x + "。" for x in texts)})
        return added

    # ---- 出行管家 ---------------------------------------------------------------------------------
    def _trip(self, body, owner, account, step, catalog, renderer, origin):
        budget, start, end, card_id = step["budget"], step["start"], step["end"], step["card_id"]
        texts, steps = [], []
        balance = self.s.transaction.balance(owner, account)["balance"]
        if balance < budget:
            # Shortfall: offer to redeem from a 0-day (T+0) holding. The amount comes from bank data, not from the model.
            shortfall, sources = budget - balance, []
            for product in self.s.wealth.products(owner):
                if product["term_days"] == 0 and product["active"]:
                    held = self.s.gateway.read("wealth", owner, "portfolio.get", account_id=account, product_id=product["id"])["amount_cents"]
                    if held > 0:
                        sources.append((product, held))
            if sources:
                product, held = sources[0]
                amount = min(shortfall, held)
                steps.append({"kind": "propose", "tool": "trip:redeem", "display": {"product": product["name"], "amount": format_cents(amount)},
                              "normalized": self._intent_for(owner, {"type": "REDEEM", "product_id": product["id"], "amount": amount})})
                texts.append(f"活期余额 {format_cents(balance)}，比预算少 {format_cents(shortfall)}，"
                             f"建议从“{product['name']}”赎回 {format_cents(amount)}（T+0）")
            else:
                texts.append(f"活期余额 {format_cents(balance)}，比预算少 {format_cents(shortfall)}，也没有可快速赎回的理财，请留意资金安排")
        else:
            texts.append(f"活期余额 {format_cents(balance)}，足够覆盖预算 {format_cents(budget)}")
        if step["international"]:
            state = self._card_state(owner, card_id)
            if not state["international_enabled"]:
                steps.append({"kind": "propose", "tool": "trip:international_on",
                              "normalized": self._intent_for(owner, {"type": "CARD_CONTROL", "card_id": card_id,
                                                                     "controls": {"international": True}})})
        cards, more = self._propose_steps(body, owner, account, steps, "出行管家", origin, renderer)
        texts += more
        name = (step["destination"] or "出行")[:10]
        specs = [{"name": f"{name}·{p}%预算提醒", "trigger": {"type": "SPEND_OVER", "amount": budget * p // 100, "window": [start, end + 60]},
                  "actions": [{"type": "NOTIFY"}], "max_runs": 1} for p in (80, 100)]
        if step["international"]:
            specs.insert(0, {"name": f"{name}·返程关闭境外交易", "trigger": {"type": "SCHEDULE", "frequency": "ONCE", "first_at": end + 60},
                             "actions": [{"type": "CARD_CONTROL", "card_id": card_id, "controls": {"international": False}}], "max_runs": 1})
        rule_card, rule_text = self._draft_automation(body, owner, account, specs, True, catalog)
        # The trip as a DAG: the read already happened; writes wait for the customer; the rules hang off the plan.
        nodes = [{"id": "n1", "title": f"核对余额与预算（{format_cents(budget)}）", "kind": "READ", "status": "SUCCEEDED", "depends_on": []}]
        confirms = iter([c for c in cards if c["type"] == "confirm"])
        for step in steps:
            card = None if step.get("queued") else next(confirms, None)
            node = {"id": f"n{len(nodes) + 1}", "title": self._step_title(step), "kind": "WRITE", "depends_on": [nodes[-1]["id"]],
                    "status": "QUEUED" if step.get("queued") else "AWAITING_CONFIRMATION" if card else "FAILED",
                    "action_ids": [card["action_id"]] if card else []}
            nodes.append(node)
            for item in body.get("queued", []):
                if item["step"] is step:
                    item["node_id"] = node["id"]
        nodes.append({"id": f"n{len(nodes) + 1}", "title": "行程联动：返程关闭境外交易、80% / 100% 预算提醒", "kind": "WRITE",
                      "status": "AWAITING_CONFIRMATION", "bundle_id": rule_card["bundle_id"], "depends_on": ["n1"]})
        for item in body.get("queued", []):
            item["step"].pop("queued", None)
        plan = {"plan_id": "p_trip_" + rule_card["bundle_id"][-8:], "goal": f"{name}出行管家", "nodes": nodes}
        return cards + [rule_card], texts + [rule_text], plan

    # ---- local reads --------------------------------------------------------------------------------
    def _local_read(self, owner, kind):
        if kind.startswith('EXTENDED:'):
            from backend.chat.knowledge import navigate_card
            from backend.banking.extensions import Extensions
            from backend.banking.advanced import Advanced
            page=kind.split(':',1)[1]
            if page=='todos':
                from backend.banking.todos import view
                v=view(self,owner);text=f"目前有 {len(v['items'])} 项待办。"+'；'.join(x['title']+('（'+x['due']+'）' if x['due'] else '') for x in v['items'][:5])
            elif page in {'deposits','exchange'}:
                v=Extensions(self).view(owner)
                text='；'.join((d['id']+' 本金 '+format_cents(d['principal'])+' 到期 '+d['matures']) for d in v['deposits'] if d['status']=='ACTIVE') if page=='deposits' else '；'.join(str(w['units']/100)+' '+w['currency'] for w in v['wallets'])
                text=text or '暂无相关持仓。'
            else:
                v=Advanced(self).view(owner)
                text=('信用账户待还 '+format_cents(sum(s['debt'] for s in v['states']))+'；溢缴款 '+format_cents(sum(s['surplus'] for s in v['states']))) if page=='credit-account' else '基金待确认订单 '+str(sum(sum(o['status']=='PENDING' for o in s['orders']) for s in v['states']))+' 笔；持仓 '+str(sum(len([h for h in s['holdings'].values() if h['units']]) for s in v['states']))+' 项。使用固定测试净值。'
            return navigate_card(page,'c_ext_'+uid()[:8]),text
        if kind=='FINANCE':
            from backend.banking.finance import Finance
            from backend.chat.knowledge import navigate_card
            v=Finance(self).view(owner)
            units=sum(p['units'] for p in v['positions']);cost=sum(p['cost'] for p in v['positions'])
            pending=sorted((r for t in v['contracts'] if t['status']=='ACTIVE' for r in t['schedule'] if not r['paid']),key=lambda r:r['due'])
            text=f"虚拟贷款与分期待还本金 {format_cents(v['outstanding'])}；黄金 {units/100:.2f} 克，持仓成本 {format_cents(cost)}（不是市值）。"
            if pending:text+=f"最近还款日 {pending[0]['due']}，当日应还 {format_cents(sum(r['total'] for r in pending if r['due']==pending[0]['due']))}。"
            return navigate_card('credit','c_fin_'+uid()[:8]),text
        card = {"type": "report", "card_id": "c_" + uid()[:8]}
        if kind == "AUTOMATIONS":
            engine = self.s.automation
            rules = engine.rules(owner)
            card |= {"report_type": "AUTOMATIONS", "items": [
                {"rule_id": r["id"], "name": r["name"], "trigger": engine.describe_trigger(r["trigger"]), "runs": r["runs"],
                 "max_runs": r["max_runs"], "auto_tighten": r["auto_tighten"],
                 "actions": [engine.describe_action(a, r["auto_tighten"]) for a in r["actions"]]} for r in rules],
                "sources": [{"label": "联动规则登记（HMAC 封存）", "kind": "BANK_DATA"}]}
            return card, (f"你有 {len(rules)} 条生效中的联动规则。" if rules else "你还没有设置联动规则。")
        feed = getattr(self.s, "feed", None)
        if feed is None:
            return card | {"report_type": "INCOME", "items": []}, "当前接入的银行接口不提供入账明细。"
        rows = feed.incoming(owner, time.time() - 90 * 86400)[-20:]
        items, flagged = [], 0
        for r in rows:
            bad = r["quarantine"]
            flagged += bool(bad)
            items.append({"credit_id": r["id"], "amount_minor": r["amount"], "at": r["occurred_at"],
                          "payer": "[可疑内容已隔离]" if bad else r["payer"], "memo": "[可疑内容已隔离]" if bad else r["memo"],
                          "quarantined": bad})
        card |= {"report_type": "INCOME", "items": items, "quarantined_count": flagged,
                 "sources": [{"label": "银行入账流水", "kind": "BANK_DATA"},
                             {"label": "外部文本隔离：付款人与备注由他人填写，按不可信数据处理，不会发给 AI 模型", "kind": "RULE"}]}
        text = f"近 90 天有 {len(items)} 笔入账，共 {format_cents(sum(i['amount_minor'] for i in items))}。"
        if flagged:
            text += f"其中 {flagged} 笔的付款信息里夹带了类似指令的文字，已隔离显示并记录审计，这些文字不会被执行。"
        return card, text

    # ---- 反诈冷静期 ----------------------------------------------------------------------------------
    def protect(self, session_id, owner, hours=24):
        body = self.store.get(session_id, owner)
        state = self.s.safety.enable_protection(owner, hours, "USER_REQUEST", "chat:" + session_id)
        cancelled = self._cancel_pending(body, owner)
        text = (f"已开启转账保护，到 {time.strftime('%m-%d %H:%M', time.localtime(state['until']))} 前对外转账、新增收款人都会被暂停"
                + (f"，并取消了 {cancelled} 项待确认的操作" if cancelled else "")
                + "。保护期内只能由银行安全人员核实身份后提前解除，任何人让你“先解除再转账”都是诈骗。")
        card = {"type": "result", "card_id": "c_" + uid()[:8], "status": "PROTECTION_ACTIVE", "message": text, "until": state["until"]}
        message_id = "protect-" + str(body["revision"])
        body["messages"].append({"role": "assistant", "message_id": message_id, "text": text, "cards": [card], "at": time.time()})
        body = self.store.save(body)
        return {"session_id": session_id, "message_id": message_id, "revision": body["revision"], "state": self._state(body),
                "reply": {"text": text, "cards": [card]}}

    # ---- sandbox demo hooks (TRUSTA_DEMO_MODE only) ---------------------------------------------------
    def demo_income(self, session_id, owner, amount, payer, memo=""):
        if not DEMO() or not getattr(self.s, "feed", None):
            raise Rejected("DEMO_MODE_DISABLED")
        body = self.store.get(session_id, owner)
        self.s.feed.credit(owner, body["default_account_id"], amount, payer, memo)
        return self.get(session_id, owner)

    def demo_purchase(self, session_id, owner, merchant, category, amount):
        if not DEMO() or not getattr(self.s, "feed", None):
            raise Rejected("DEMO_MODE_DISABLED")
        body = self.store.get(session_id, owner)
        self.s.feed.purchase(owner, body["default_account_id"], merchant, category, amount)
        return self.get(session_id, owner)

    def demo_fire_rule(self, session_id, owner, rule_id):
        """Bring a SCHEDULE rule's next run forward to now (demo only; audited)."""
        if not DEMO():
            raise Rejected("DEMO_MODE_DISABLED")
        self.store.get(session_id, owner)
        engine = self.s.automation
        rule = next((r for r in engine.rules(owner) if r["id"] == rule_id and r["trigger"]["type"] == "SCHEDULE"), None)
        if not rule:
            raise Rejected("AUTOMATION_NOT_FOUND")
        with self.s.db.transaction() as conn:
            rule["state"]["next_at"] = time.time() - 1
            engine._save(conn, rule)
            self.s.audit.append_in(conn, None, None, "AUTOMATION_DEMO_ADVANCED", "automation", {"rule_id": rule_id})
        return self.get(session_id, owner)
