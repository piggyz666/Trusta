"""Deterministic rendering of runtime results into protocol cards and reply text.

Every number shown to the user comes from the task / bank snapshot passed in,
never from model output. Card shapes follow docs/chat-protocol-v1.md section 3.
"""
from datetime import datetime
from backend.business import CHINA
from backend.chat.amounts import format_cents
from backend.chat.guard import mask_id
from backend.domain import uid

RISK_REASONS = {
    "V1_CONFIRM_ALL": "资金操作需要你本人确认", "CONFIRM_EACH_ACTION": "该操作需要你本人确认",
    "NEW_RECIPIENT": "首次向该收款人转账", "HIGH_RISK_AMOUNT": "金额达到大额阈值",
    "DAILY_TRANSFER_STRONG_AUTH": "今日累计转账将超过 ¥1,000.00", "FOREIGN_CURRENCY_STRONG_AUTH": "外币转账需要强验证",
    "HIGH_RISK_CHANGE": "该操作属于高风险变更",
    "TRANSFER_VELOCITY_STRONG_AUTH": "10 分钟内已连续转账 3 笔，需要额外验证",
    "NEW_PAYEE_STRONG_AUTH": "向 24 小时内新添加的收款人转账 ≥ ¥500.00，需要额外验证", "INVESTMENT_STRONG_AUTH": "理财申购 / 赎回需要强验证",
}
ERRORS = {
    "GOLD_QUOTE_UNAVAILABLE": "暂时拿不到可成交的金价，请稍后再试", "FX_QUOTE_UNAVAILABLE": "暂时拿不到可成交的汇率，请稍后再试",
    "FEATURE_UNAVAILABLE": "这项功能当前不可用（未开通或已在功能页关闭）",
    "CAPABILITY_VIOLATION": "你接入的银行没有提供这项业务的接口", "BANK_CAPABILITY_UNAVAILABLE": "你接入的银行没有提供这项业务的接口",
    "INSUFFICIENT_BALANCE": "余额不足", "UNKNOWN_PAYEE": "收款人不在你的收款人列表中", "FROZEN_SOURCE": "付款账户已冻结", "ACCOUNT_PROTECTION_ACTIVE": "账户处于反诈保护期，对外转账已暂停（只能由银行安全人员核实后提前解除）",
    "STALE_PRECONDITION": "账户状态已变化，请重新发起", "BANKING_SECURITY_LOCKED": "账户已因安全原因锁定，请联系客服解锁",
    "RECIPIENT_AMBIGUOUS": "收款人没有唯一匹配，请提供全名或手机号", "POLICY_BLOCKED": "未通过安全规则校验",
    "BANKING_READ_UNCERTAIN": "银行数据读取异常，本次未执行任何操作", "CARD_NOT_ACTIVE": "卡片当前状态不支持该操作",
    "PRODUCT_INACTIVE": "该产品已下架", "BELOW_MINIMUM": "低于产品起购金额", "RISK_MISMATCH": "产品风险等级高于你的风险承受能力",
    "RISK_PROFILE_EXPIRED": "风险测评已过期，请先重新测评", "INSUFFICIENT_HOLDING": "持有份额不足",
    "NOT_REDEEMABLE_YET": "尚未到可赎回时间", "NOT_CANCELABLE": "该项目不支持在线取消", "ALREADY_CANCELLED": "该项目已取消",
    "DUPLICATE_SPLIT_RECIPIENT": "参与人重复", "INVALID_SCHEDULE": "执行时间无效",
    "WISH_FUND_EXISTS": "这个心愿金已经存在", "WISH_FUND_NOT_ACTIVE": "心愿金不存在或已释放", "WISH_FUND_INSUFFICIENT": "心愿金余额不足",
    "MERCHANT_UNAVAILABLE": "商户暂不可用", "MERCHANT_PRICE_MISMATCH": "商品价格与商户报价不一致", "INVALID_RELEASE_TIME": "释放时间无效",
    "CARD_STATE_INVALID": "卡片当前状态不支持该操作", "RISK_PROFILE_REQUIRED": "需要先完成风险测评",
    "SUITABILITY_RISK_MISMATCH": "产品风险等级高于你的风险承受能力", "BELOW_PRODUCT_MINIMUM": "低于产品起购金额",
    "HOLDING_PERIOD_NOT_MET": "尚未到可赎回时间",
    "PAYEE_PHONE_EXISTS": "这个手机号已经在你的收款人列表里", "PAYEE_ALIAS_EXISTS": "这个备注名已经被使用", "PAYEE_EXISTS": "收款人已存在",
}
from backend.banking.finance import LABELS as FINANCE_LABELS
OPERATIONS = {**FINANCE_LABELS, "RETAIL_PAY":"确认虚拟付款","RETAIL_REFUND":"确认原路退款", 
    "TRANSFER": "转账", "CARD_APPLY": "申请虚拟卡", "CARD_FREEZE": "冻结卡片", "CARD_UNFREEZE": "解冻卡片",
    "CARD_REPORT_LOST": "挂失卡片", "CARD_LIMIT_CHANGE": "调整卡片限额", "CARD_TRANSACTION_CONTROL": "设置交易开关",
    "SUBSCRIPTION_CANCEL": "取消订阅", "DIRECT_DEBIT_DISABLE": "关闭代扣",
    "INVESTMENT_PURCHASE": "申购理财", "INVESTMENT_REDEEM": "赎回理财",
    "CARD_CANCEL_LOST": "解除挂失", "CARD_PIN_RESET": "重置卡密码", "FUND_LOCK": "锁定心愿金", "FUND_RELEASE": "释放心愿金",
    "WISH_ORDER": "心愿金下单", "RISK_PROFILE_UPDATE": "更新风险等级", "ADD_PAYEE": "添加收款人", "PAYEE_MANAGEMENT": "添加收款人",
    "WISH_FUND_MANAGEMENT": "心愿金", "RISK_ASSESSMENT": "风险测评",
}
RISK_LABELS = {1: "保守型", 2: "稳健型", 3: "平衡型", 4: "成长型", 5: "进取型"}
TERMINAL_OK = {"COMPLETED", "SUCCEEDED", "SETTLED"}


REPORT_BASIS = {
    "BALANCE": [("核心账务实时余额", "BANK_DATA")],
    "SPENDING_SUMMARY": [("账户已入账交易流水（不含待入账）", "BANK_DATA"), ("商户类别分类规则", "RULE")],
    "ANNUAL_REPORT": [("全年已入账交易流水", "BANK_DATA"), ("商户类别分类规则", "RULE")],
    "SPENDING_QUERY": [("账户已入账交易流水（不含待入账）", "BANK_DATA"), ("商户类别分类规则", "RULE")],
    "PORTFOLIO": [("理财持仓登记（按申购本金，不含收益）", "BANK_DATA")],
    "PRODUCT_LIST": [("产品风险等级 ≤ 你的风险测评等级（适当性规则）", "RULE"), ("理财产品目录", "BANK_DATA")],
    "PRODUCT_COMPARE": [("理财产品目录（不预测收益）", "BANK_DATA")],
    "SUBSCRIPTIONS": [("订阅/代扣签约登记与周期扣款流水", "BANK_DATA")],
    "RENEWAL_REMINDERS": [("订阅/代扣下次扣费日", "BANK_DATA")],
    "WISH_FUNDS": [("心愿金子账户", "BANK_DATA")],
    "CARDS": [("银行卡状态、限额与交易开关", "BANK_DATA")],
    "SPLIT_REQUEST": [("收款人名册", "BANK_DATA"), ("整数分摊：余数按顺序分配，总额精确不变", "RULE")],
}


def report_sources(report_type, task_id):
    """“依据”: where every number on a report card came from. All bank data here is read by an Agent through a signed,
    audited request; the evidence bundle for task_id lets anyone re-verify it offline."""
    basis = [{"label": label, "kind": kind} for label, kind in REPORT_BASIS.get(report_type, [("银行系统查询结果", "BANK_DATA")])]
    return basis + [{"label": "签名证据包（可离线验证）", "kind": "EVIDENCE", "ref": task_id}]


def error_text(codes):
    codes = [c for c in codes or [] if c]
    if not codes:
        return "操作未完成"
    return "；".join(ERRORS.get(code, f"未通过安全校验（{code}）") for code in dict.fromkeys(codes))


def _date(stamp):
    return datetime.fromtimestamp(stamp, CHINA).strftime("%Y-%m-%d %H:%M")


class CardRenderer:
    def __init__(self, service, owner, catalog):
        self.s, self.owner, self.catalog = service, owner, catalog

    def _lookup(self, kind, item_id):
        for item in self.catalog.get(kind, []):
            if item["id"] == item_id:
                return item["display"]
        return mask_id(item_id)

    def _payee(self, recipient_id):
        try:
            rows = self.s.gateway.read("transaction", self.owner, "payee.resolve", recipient_id=recipient_id)
            return rows[0]["name"] if rows else "未知收款人"
        except Exception:
            return "未知收款人"

    def _verification(self, decision):
        if decision != "STRONG_AUTH":
            return []
        return ["TOTP", "SMS"] if self.s.safety.enabled else ["DEMO_SECRET"]

    # ---- confirmation ------------------------------------------------------------------------
    def action_fields(self, action, account_snapshot=None):
        kind = action["action_type"]
        fields = []
        if kind in FINANCE_LABELS:
            t=action['preconditions']['target'];post=action['expected_postconditions'];p=action['parameters']
            from backend.banking.finance import card_label
            fields=[{'label':'业务','value':FINANCE_LABELS[kind]},{'label':'本次金额','value':format_cents(p['amount']),'amount_minor':p['amount'],'currency':'CNY'},{'label':'银行卡','value':card_label(self.s.db,t['customer_id'],t['card_id'])},{'label':'完成后余额','value':format_cents(post['account']['balance'])}]
            from backend.banking import extensions,advanced
            if kind in advanced.LABELS:fields += advanced.fields(t)
            elif kind in extensions.LABELS:fields += extensions.fields(t)
            elif kind.startswith('GOLD_'):
                fields += [{'label':'重量','value':f"{t['units']/100:.2f} 克"},{'label':'成交参考价','value':format_cents(t['price'])+'/克'},{'label':'行情来源','value':t['quote']['source']+' · '+t['quote']['as_of']},{'label':'手续费','value':'0 元（虚拟交易）'}]
            else:
                pending=[x for x in t['schedule'] if not x['paid']]
                fee=pending[0]['fee'] if kind in {'CREDIT_REPAY','CREDIT_SETTLE'} and pending else sum(x['fee'] for x in pending)
                principal=t['remaining']-post['target']['remaining'] if kind in {'CREDIT_REPAY','CREDIT_SETTLE'} else t['amount']
                fields += [{'label':'本次本金','value':format_cents(principal)},{'label':'本期费用' if kind in {'CREDIT_REPAY','CREDIT_SETTLE'} else '计划总费用','value':format_cents(fee)},{'label':'完成后剩余本金','value':format_cents(post['target']['remaining'])},{'label':'规则','value':'贷款按剩余本金年息3.60%；分期每期按原本金0.50%。提前结清免未来期费用。'}]
            return fields+[{'label':'说明','value':'仅操作虚拟资金，不开立真实金融产品、不交割实物或外汇'}]
        if kind in {"RETAIL_PAY","RETAIL_REFUND"}:
            pre=action["preconditions"];t=pre["target"]
            import json
            card_name=json.loads(t.get("detail") or "{}").get("payment_card_name", "")
            card_label=(card_name+" · " if card_name else "")+t["card_id"]
            return [{"label":"订单","value":t["title"]},{"label":"金额","value":format_cents(t["amount"]),"amount_minor":t["amount"],"currency":"CNY"},{"label":"付款卡","value":card_label},{"label":"账户","value":t["account_id"]},{"label":"完成后余额","value":format_cents(action["expected_postconditions"]["account"]["balance"])},{"label":"说明","value":"仅虚拟资金，不向真实商户付款"}] + ([{"label":"关联计划","value":t["plan"]["name"]},{"label":"计划剩余预算","value":format_cents(t["plan"]["budget"]-t["plan"]["spent"])}] if t.get("plan") else [])
        if kind == "TRANSFER":
            fields.append({"label": "收款人", "value": self._payee(action["recipient_id"])})
            fields.append({"label": "金额", "value": format_cents(action["amount"], action["currency"]),
                           "amount_minor": action["amount"], "currency": action["currency"]})
        target = (action.get("target") or {}).get("id")
        params = action.get("parameters") or {}
        if kind.startswith("CARD_") and kind != "CARD_APPLY":
            fields.append({"label": "卡片", "value": self._lookup("cards", target)})
        if kind == "CARD_LIMIT_CHANGE":
            fields.append({"label": "新限额", "value": format_cents(params["spending_limit"]),
                           "amount_minor": params["spending_limit"], "currency": "CNY"})
        if kind == "CARD_TRANSACTION_CONTROL":
            on = lambda flag: "开启" if params.get(flag) else "关闭"
            fields.append({"label": "交易开关", "value": f"线上{on('online_enabled')} · 境外{on('international_enabled')} · 非接触{on('contactless_enabled')}"})
        if kind == "SUBSCRIPTION_CANCEL":
            fields.append({"label": "订阅", "value": self._lookup("subscriptions", target)})
        if kind == "DIRECT_DEBIT_DISABLE":
            fields.append({"label": "代扣", "value": self._lookup("direct_debits", target)})
        if kind in {"INVESTMENT_PURCHASE", "INVESTMENT_REDEEM"}:
            fields.append({"label": "产品", "value": self._lookup("products", target)})
            fields.append({"label": "金额", "value": format_cents(params["amount"]), "amount_minor": params["amount"], "currency": "CNY"})
        pre = action.get("preconditions") or {}
        if kind == "CARD_PIN_RESET":
            fields.append({"label": "方式", "value": "向预留手机号发送设置新密码的安全链接；密码不经过 AI"})
        if kind == "FUND_LOCK":
            fields += [{"label": "心愿金", "value": params["name"]},
                       {"label": "锁定金额", "value": format_cents(params["amount"]), "amount_minor": params["amount"], "currency": "CNY"},
                       {"label": "到期释放", "value": _date(params["release_at"])}]
        if kind == "FUND_RELEASE":
            balance = (pre.get("target") or {}).get("balance", 0)
            fields += [{"label": "心愿金", "value": (pre.get("target") or {}).get("name", "")},
                       {"label": "释放金额", "value": format_cents(balance), "amount_minor": balance, "currency": "CNY"}]
        if kind == "WISH_ORDER":
            catalogue = {m["id"]: m["display"] for m in self.catalog.get("merchants", [])}
            for item in params["items"]:
                fields.append({"label": "商品", "value": catalogue.get(item["merchant_id"], item["merchant_id"])})
            fields += [{"label": "合计", "value": format_cents(params["amount"]), "amount_minor": params["amount"], "currency": "CNY"},
                       {"label": "支付来源", "value": "心愿金“" + (pre.get("target") or {}).get("name", "") + "”"}]
            return fields
        if kind == "ADD_PAYEE":
            fields += [{"label": "姓名", "value": params["name"]},
                       {"label": "手机号", "value": params["phone"][:3] + "****" + params["phone"][-4:]}]
            if params["alias"]:
                fields.append({"label": "备注名", "value": params["alias"]})
            fields.append({"label": "提示", "value": "请确认对方身份。添加后首次转账也会单独确认"})
            return fields
        if kind == "RISK_PROFILE_UPDATE":
            level = params["risk_level"]
            old = (pre.get("target") or {}).get("risk_level") or 0
            fields += [{"label": "新风险等级", "value": f"R{level} {RISK_LABELS[level]}"},
                       {"label": "原风险等级", "value": f"R{old} {RISK_LABELS[old]}" if old else "无"},
                       {"label": "有效期至", "value": _date(params["expires_at"])[:10]}]
            return fields
        account = action.get("source_account_id")
        if account:
            label = self._lookup("accounts", account)
            if kind == "TRANSFER" and account_snapshot and account_snapshot.get("balance") is not None:
                label += f"，转后余额 {format_cents(account_snapshot['balance'] - action['amount'])}"
            if kind == "FUND_LOCK" and pre.get("account"):
                label += f"，锁定后可用 {format_cents(pre['account']['balance'] - params['amount'])}"
            fields.append({"label": "付款账户" if kind in {"TRANSFER", "INVESTMENT_PURCHASE", "FUND_LOCK"} else
                           "退回账户" if kind == "FUND_RELEASE" else "账户", "value": label})
        return fields

    def confirm_card(self, task, action, authorization, policy, step=None, schedule=None, account_snapshot=None):
        kind = action["action_type"]
        fields = self.action_fields(action, account_snapshot)
        if schedule:
            fields.insert(2 if kind == "TRANSFER" else 1, {"label": "执行时间", "value": _date(schedule)})
        title = OPERATIONS.get(kind, kind)
        if kind == "TRANSFER":
            title = ("定时转账给 " if schedule else "转账给 ") + fields[0]["value"]
        return {"type": "confirm", "card_id": "c_" + uid()[:8], "task_id": task["task_id"],
                "action_id": action["action_id"], "action_hash": authorization["action_hash"],
                "action_type": kind, "title": title, "fields": fields,
                "risk_level": policy.get("risk_level", "YELLOW"),
                "risk_reasons": [RISK_REASONS[r] for r in policy.get("reason_codes", []) if r in RISK_REASONS],
                "required_verification": self._verification(policy.get("decision")),
                "expires_at": authorization.get("expires_at"),
                "evidence": {"source_spans": (step or {}).get("spans", {})}}

    def pending_actions(self, task):
        """Uniform view over legacy transfer tasks and Phase-5 action_records."""
        items = []
        if "action_records" in task:
            for entry in task["action_records"]:
                if entry["status"] == "WAITING_USER" and entry.get("authorization"):
                    items.append((entry["action"], entry["authorization"], entry["policy"]))
        elif task.get("status") == "WAITING_USER" and task.get("authorization") and task.get("proposed_actions"):
            items.append((task["proposed_actions"][0], task["authorization"], task["policy_decision"]))
        return items

    def task_cards(self, task, step=None):
        cards, texts = [], []
        schedule = (step or {}).get("normalized", {}).get("execute_at")
        snapshot = (task.get("context") or {}).get("balance")
        pending = self.pending_actions(task)
        for action, authorization, policy in pending:
            cards.append(self.confirm_card(task, action, authorization, policy, step, schedule, snapshot))
        if pending:
            if task.get("task_type") != "CROSS_SCENARIO":
                texts.append("请核对下面的信息，确认后我再执行。" if len(pending) == 1 else f"共有 {len(pending)} 项操作，请逐项确认。")
        status = task.get("status")
        if status == "BLOCKED" or "action_records" in task and any(e["status"] == "BLOCKED" for e in task["action_records"]):
            codes = list(task.get("errors") or [])
            for entry in task.get("action_records", []):
                if entry["status"] == "BLOCKED":
                    codes += (entry.get("policy") or {}).get("reason_codes", []) + [entry.get("error")]
            codes = [c for c in codes if c and c not in RISK_REASONS]
            texts.append("这项操作没有通过校验：" + error_text(codes) + "。")
        elif status == "FAILED":
            texts.append("操作没有完成：" + error_text(task.get("errors")) + "。没有发生扣款。")
        elif status == "WAITING_USER" and not pending and task.get("clarification"):
            cards.append({"type": "clarify", "card_id": "c_" + uid()[:8], "slot": "other",
                          "question": task["clarification"], "options": [], "free_text_allowed": True})
        if task.get("result") and (status == "COMPLETED" or task.get("task_type") == "CROSS_SCENARIO"):
            report = self.report_card(task)
            if report:
                report["sources"] = report_sources(report.get("report_type"), task["task_id"])
                cards.append(report)
                texts.append(report.pop("_text"))
        return cards, texts

    # ---- results / receipts -------------------------------------------------------------------
    def result_cards(self, task, action_id):
        """After a decision: one result card for the decided action."""
        records, entry = task.get("action_records"), None
        if records is not None:
            entry = next((e for e in records if e["action"]["action_id"] == action_id), None)
            if not entry:
                return []
            status, action, receipt = entry["status"], entry["action"], entry.get("receipt")
        else:
            action = (task.get("proposed_actions") or [{}])[0]
            status, receipt = task["status"], task.get("receipt")
        label = OPERATIONS.get(action.get("action_type"), "操作")
        mapping = {"COMPLETED": "SUCCEEDED", "DENIED": "CANCELLED", "CANCELLED": "CANCELLED", "SCHEDULED": "SCHEDULED",
                   "WAITING_TIME": "SCHEDULED", "MANUAL_REVIEW": "MANUAL_REVIEW", "BLOCKED": "FAILED", "FAILED": "FAILED"}
        result = mapping.get(status, status)
        if result == "SUCCEEDED":
            params = action.get("parameters") or {}
            if action.get("action_type") == "TRANSFER":
                message = f"已向 {self._payee(action['recipient_id'])} 转账 {format_cents(action['amount'], action['currency'])}，银行核验通过。"
            elif action.get("action_type") == "FUND_LOCK":
                message = f"已把 {format_cents(params['amount'])} 锁定到心愿金“{params['name']}”，银行核验通过。"
            elif action.get("action_type") == "WISH_ORDER":
                message = f"已用心愿金下单，共 {format_cents(params['amount'])}，银行核验通过。"
            elif action.get("action_type") == "FUND_RELEASE":
                message = "心愿金剩余资金已退回活期，银行核验通过。"
            elif action.get("action_type") == "ADD_PAYEE":
                message = f"已添加收款人 {params['name']}，现在可以说“给{params['name']}转多少钱”。"
            elif action.get("action_type") == "RISK_PROFILE_UPDATE":
                message = f"风险等级已更新为 R{params['risk_level']} {RISK_LABELS[params['risk_level']]}，有效期一年。"
            else:
                message = f"{label}已完成，银行核验通过。"
        elif result == "SCHEDULED":
            message = f"{label}已登记，将在约定时间执行；执行前你可以随时说“取消”。"
        elif result == "CANCELLED":
            message = f"已取消{label}，没有执行任何操作。"
        elif result == "MANUAL_REVIEW":
            message = "结果待银行核实，请勿重复操作。我们会继续跟进，核实后通知你。"
        else:
            message = f"{label}未完成：" + error_text((task.get("errors") or []) + [(entry or {}).get("error")]) + "。"
        receipt_id = (receipt or {}).get("receipt_id")
        return [{"type": "result", "card_id": "c_" + uid()[:8], "action_id": action_id, "status": result,
                 "receipt_id": receipt_id, "message": message,
                 "proof_url": f"/api/audit/proof?receipt_id={receipt_id}" if receipt_id else None}]

    def report_card(self, task):
        kind, result = task.get("task_type"), task["result"]
        card = {"type": "report", "card_id": "c_" + uid()[:8]}
        signed = {"BALANCE_QUERY": lambda: self.balance_card(result["balance"], self._lookup("accounts", result["balance"]["id"])),
                  "SPENDING_QUERY": lambda: self.spending_query_card(result),
                  "PORTFOLIO_QUERY": lambda: self.portfolio_card(result["positions"]),
                  "RENEWAL_QUERY": lambda: self.reminders_card(result)}
        if kind in signed:
            produced, text = signed[kind]()
            return produced | {"_text": text, "task_id": task["task_id"]}
        if kind == "BILL_ANALYSIS":
            summary = result["summary"]
            period = result.get("month") or f"{result.get('year')}年"
            breakdown = [{"category": c["label"], "amount_minor": c["net_spend"], "ratio": round(c["gross_share_bps"] / 10000, 4),
                          "count": c["count"]} for c in result["categories"]]
            anomalies = [{"transaction_id": f["transaction_id"], "merchant": f["merchant"], "amount_minor": f["amount"],
                          "reasons": f["reasons"]} for f in result["anomalies"].get("flags", [])]
            card |= {"report_type": "ANNUAL_REPORT" if result.get("year") else "SPENDING_SUMMARY", "period": period,
                     "summary": {"total_minor": summary["net_spend"], "currency": summary["currency"],
                                 "tx_count": summary.get("settled_count", 0)},
                     "breakdown": breakdown, "anomalies": anomalies, "chart_hint": "pie"}
            top = max(breakdown, key=lambda c: c["amount_minor"], default=None)
            text = f"{period}共消费 {format_cents(summary['net_spend'])}（{summary.get('settled_count', 0)} 笔）"
            if top:
                text += f"，{top['category']}最多，{format_cents(top['amount_minor'])}"
            text += f"。发现 {len(anomalies)} 笔需要留意的交易。" if anomalies else "。没有发现异常交易。"
            card["_text"] = text
        elif kind == "WEALTH_RECOMMENDATION":
            items = [{"product_id": r["product_id"], "name": r["name"], "risk_level": r["risk_level"],
                      "term_days": r["term_days"], "minimum_minor": r["minimum_amount"], "reasons": r["reasons"],
                      "risk_note": r["risk_note"]} for r in result["recommendations"]]
            card |= {"report_type": "PRODUCT_LIST", "customer_risk_level": result["customer_risk_level"], "items": items,
                     "disclaimer": result["limitations"]}
            card["_text"] = (f"按你的风险等级 R{result['customer_risk_level']}，有 {len(items)} 款产品适合你。理财有风险，以上不构成收益承诺。"
                             if items else "目前没有与你风险等级匹配的在售产品。")
        elif kind == "PRODUCT_COMPARE":
            items = [{"product_id": p["id"], "name": p["name"], "risk_level": p["risk_level"], "term_days": p["term_days"],
                      "minimum_minor": p["minimum_amount"], "risk_note": p["risk_note"]} for p in result["products"]]
            card |= {"report_type": "PRODUCT_COMPARE", "items": items, "guaranteed_return": False}
            card["_text"] = "对比如下：" + "；".join(f"{p['name']} 风险R{p['risk_level']}、期限{p['term_days']}天、起购{format_cents(p['minimum_minor'])}" for p in items) + "。"
        elif kind in {"SUBSCRIPTION_MANAGEMENT", "DIRECT_DEBIT_MANAGEMENT", "CARD_MANAGEMENT"} and "resources" in result:
            report_type = {"SUBSCRIPTION_MANAGEMENT": "SUBSCRIPTIONS", "DIRECT_DEBIT_MANAGEMENT": "SUBSCRIPTIONS",
                           "CARD_MANAGEMENT": "CARDS"}[kind]
            items = []
            for row in result["resources"]:
                if kind == "CARD_MANAGEMENT":
                    items.append({"id": row["id"], "display": self._lookup("cards", row["id"]), "status": row["status"],
                                  "limit_minor": row["spending_limit"], "online": bool(row["online_enabled"]),
                                  "international": bool(row["international_enabled"]), "contactless": bool(row["contactless_enabled"])})
                else:
                    items.append({"id": row["id"], "merchant": row["merchant"], "amount_minor": row["amount"],
                                  "frequency": row["frequency"], "next_charge_at": row["next_charge_at"], "status": row["status"],
                                  "kind": "direct_debit" if kind == "DIRECT_DEBIT_MANAGEMENT" else "subscription"})
            card |= {"report_type": report_type, "items": items}
            unresolved = [r["candidate"] for r in result.get("resolutions", []) if r["resolution"] != "RESOLVED"]
            if unresolved:
                card["suspected"] = [{"merchant": c["merchant"], "amount_minor": c["amount"]} for c in unresolved]
            if kind == "CARD_MANAGEMENT":
                card["_text"] = f"你有 {len(items)} 张卡。"
            else:
                active = [i for i in items if i["status"] == "ACTIVE"]
                total = sum(i["amount_minor"] for i in active)
                noun = "代扣" if kind == "DIRECT_DEBIT_MANAGEMENT" else "订阅"
                card["_text"] = f"你有 {len(active)} 个生效中的{noun}，每月合计 {format_cents(total)}。"
                if unresolved:
                    card["_text"] += f"另外从扣款记录里发现 {len(unresolved)} 个疑似订阅，未在银行登记，无法在线取消。"
        elif kind == "WISH_FUND_MANAGEMENT" and "resources" in result:
            items = [{"id": f["id"], "name": f["name"], "balance_minor": f["balance"], "target_minor": f["target_amount"],
                      "release_at": f["release_at"], "status": f["status"]} for f in result["resources"]]
            card |= {"report_type": "WISH_FUNDS", "items": items}
            active = [i for i in items if i["status"] == "ACTIVE"]
            card["_text"] = (f"你有 {len(active)} 个心愿金，共锁定 {format_cents(sum(i['balance_minor'] for i in active))}。"
                             if active else "你还没有心愿金。")
        elif kind == "CROSS_SCENARIO":
            summary = result["analysis"]["summary"]["summary"] if "summary" in result["analysis"]["summary"] else result["analysis"]["summary"]
            resolved = [r for r in result["resolutions"] if r["resolution"] == "RESOLVED"]
            card |= {"report_type": "SUBSCRIPTIONS", "items": [{"id": r["subscription_id"], "merchant": r["candidate"]["merchant"],
                                                                 "amount_minor": r["candidate"]["amount"], "status": "ACTIVE"} for r in resolved]}
            card["_text"] = (f"我识别出 {len(result['resolutions'])} 个周期扣款，其中 {len(resolved)} 个可以在线取消。"
                             "是否取消由你逐项决定，不需要的直接拒绝即可。")
        elif kind == "SPLIT_REQUEST" and "split_requests" in result:
            items = [{"payee": self._payee(r["payee_id"]), "amount_minor": r["amount"], "status": r["status"]} for r in result["split_requests"]]
            card |= {"report_type": "SPLIT_REQUEST", "total_minor": result["total"], "self_share_minor": result.get("self_share", 0),
                     "items": items, "financial_write": False}
            mine = f"你自己承担 {format_cents(result['self_share'])}，" if result.get("self_share") else ""
            card["_text"] = (f"AA 总额 {format_cents(result['total'])}，{mine}需要向 "
                             + "、".join(f"{i['payee']} 收 {format_cents(i['amount_minor'])}" for i in items)
                             + "。这只是收款请求，不会从任何人的账户扣款，对方付款后才到账。")
        else:
            return None
        return card

    @staticmethod
    def balance_card(balance, display):
        card = {"type": "report", "card_id": "c_" + uid()[:8], "report_type": "BALANCE",
                "items": [{"account_id": balance["id"], "display": display, "balance_minor": balance["balance"],
                           "currency": balance["currency"]}]}
        return card, f"{display} 可用余额 {format_cents(balance['balance'], balance['currency'])}。"

    @staticmethod
    def reminders_card(data):
        items = [{"merchant": r["merchant"], "amount_minor": r["amount"], "next_charge_at": r["next_charge_at"],
                  "status": r["status"], "kind": r["kind"]} for r in data["reminders"]]
        card = {"type": "report", "card_id": "c_" + uid()[:8], "report_type": "RENEWAL_REMINDERS", "items": items}
        soon = [i for i in items if i["status"] in {"DUE", "OVERDUE"}]
        text = f"共有 {len(items)} 项周期扣费，" + (f"其中 {len(soon)} 项三天内扣费。" if soon else "近三天没有扣费。")
        if items:
            first = min(items, key=lambda i: i["next_charge_at"])
            text += f"最近一笔是 {first['merchant']} {format_cents(first['amount_minor'])}，{_date(first['next_charge_at'])[:10]}。"
        return card, text

    @staticmethod
    def spending_query_card(result):
        months = result["months"]
        period = months[0] if len(months) == 1 else f"{months[0]} 至 {months[-1]}"
        filters = result["filters"]
        scope = (filters["category_label"] or "") + (f"在“{filters['merchant']}”" if filters["merchant"] else "")
        total = result["summary"]["net_spend"]
        card = {"type": "report", "card_id": "c_" + uid()[:8], "report_type": "SPENDING_QUERY", "period": period,
                "filters": filters, "summary": {"total_minor": total, "currency": result["summary"]["currency"],
                                                 "tx_count": result["summary"]["settled_count"]},
                "by_month": result["by_month"], "breakdown": result["categories"], "top_merchants": result["top_merchants"],
                "comparison": result.get("comparison"), "chart_hint": "bar" if len(months) > 1 else "pie"}
        text = f"{period}{scope}共消费 {format_cents(total)}（{result['summary']['settled_count']} 笔）"
        if result["top_merchants"] and not filters["merchant"]:
            top = result["top_merchants"][0]
            text += f"，花得最多的是{top['merchant']} {format_cents(top['net_spend'])}"
        comparison = result.get("comparison")
        if result["summary"]["settled_count"] == 0 and comparison and comparison["previous_net_spend"] > 0:
            # nothing posted yet this period (e.g. the first days of a month): say so plainly and show the last one
            previous = "、".join(comparison["previous_months"])
            text = (f"{period}{scope}还没有已入账的消费。上一期（{previous}）共消费 {format_cents(comparison['previous_net_spend'])}，"
                    "可以说“上个月花了多少”看明细")
            return card, text + "。"
        if comparison and comparison["previous_net_spend"] > 0:
            change = comparison["net_change"]
            text += f"；比之前同样长的时段{'多' if change > 0 else '少'}花了 {format_cents(abs(change))}"
            if comparison["net_change_bps"] is not None:
                text += f"（{abs(comparison['net_change_bps']) / 100:.1f}%）"
        elif comparison:
            text += "；之前同样长的时段没有消费记录，无法对比"
        return card, text + "。"

    @staticmethod
    def portfolio_card(positions):
        held = [p for p in positions if p["amount_cents"] > 0]
        card = {"type": "report", "card_id": "c_" + uid()[:8], "report_type": "PORTFOLIO",
                "items": [{"product_id": p["product_id"], "name": p["name"], "amount_minor": p["amount_cents"],
                           "redeemable_at": p["redeemable_at"], "risk_level": p["risk_level"]} for p in held]}
        if not held:
            return card, "你目前没有理财持仓。"
        total = sum(p["amount_cents"] for p in held)
        return card, f"你持有 {len(held)} 款理财，合计本金 {format_cents(total)}（按申购金额计，不含收益）。"

    @staticmethod
    def risk_card(hit, audit_event_id=None):
        return {"type": "risk_alert", "card_id": "c_" + uid()[:8], "category": hit["category"], "severity": hit["severity"],
                "message": hit["message"], "blocking": hit["blocking"], "cooldown_seconds": hit.get("cooldown_seconds", 0),
                "audit_event_id": audit_event_id}

    @staticmethod
    def clarify_card(slot, question, options=None):
        return {"type": "clarify", "card_id": "c_" + uid()[:8], "slot": slot, "question": question,
                "options": [{"label": o, "value": o} for o in (options or [])], "free_text_allowed": True}
