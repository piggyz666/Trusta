"""Deterministic gate between untrusted model tool calls and the banking runtime.

Rules (see docs/chat-protocol-v1.md section 6.5):
1. tool name whitelisted, arguments match the declared schema (no extra fields);
2. every financial literal carries a ``span`` copied from the user's own words,
   and the independently parsed span agrees with the normalized value;
3. resource ids come from the caller's own catalogue;
4. authority-like fields anywhere in the arguments void the whole turn.
A failed check never guesses a value: it becomes a clarification question.
"""
import re
from datetime import datetime, timedelta
from backend.business import CHINA
from backend.chat.amounts import format_cents, parse_amount_span, parse_amount_value
from backend.chat.tools import TOOLS

FORBIDDEN_KEYS = {"approve", "approved", "approval", "authorization", "authorize", "authorized", "risk_level", "risk",
                  "strong_secret", "secret", "otp", "code", "skip", "bypass", "executor", "execute", "confirmed",
                  "confirm", "policy", "user_id", "customer_id", "action_hash", "force"}
RELATIONS = {"妈妈": ("妈", "母亲", "老娘"), "爸爸": ("爸", "父亲", "老爹"), "老婆": ("老婆", "媳妇", "妻子", "爱人"),
             "老公": ("老公", "丈夫", "爱人"), "房东": ("房东",), "同事": ("同事",)}
AMOUNT_PIECE = re.compile(r"[0-9０-９]+(?:\.[0-9]+)?\s*(?:万|千|k|K|w|W)?[0-9一二两三四五六七八九]?(?:块钱|块|元|圆)?"
                          r"|[零〇一二两三四五六七八九十百千万]+(?:块钱|块|元|圆)")
TIME_MARKERS = re.compile(r"[0-9０-９]{1,2}\s*[:：点时]|[一二两三四五六七八九十]+点|早上|上午|中午|下午|晚上|傍晚|凌晨|半夜|整点")
MAX_SCHEDULE_SECONDS = 366 * 86400


class Clarify(Exception):
    """A missing / unverifiable field. Converted into a clarify card, never a guess."""
    def __init__(self, code, slot, question, options=None):
        super().__init__(code)
        self.code, self.slot, self.question, self.options = code, slot, question, options or []


class InjectionDetected(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


def _norm(text):
    text = re.sub(r"\s+", "", text or "")
    return text.translate(str.maketrans("０１２３４５６７８９：，。", "0123456789:,."))


def _check_schema(value, schema, path="$"):
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(path)
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(path + ":extra")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(path + ":missing:" + key)
        for key, item in value.items():
            if key in props:
                _check_schema(item, props[key], f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(path)
        for i, item in enumerate(value):
            _check_schema(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10000):
            raise ValueError(path)
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(path)
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(path)
    elif kind == "integer":
        if type(value) is not int or not schema.get("minimum", -10**18) <= value <= schema.get("maximum", 10**18):
            raise ValueError(path)
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError(path)


def _normalize(value, schema):
    """Forgive harmless shape slips before the strict check (run *after* the forbidden-field scan):
    null optional fields are treated as absent, "true"/"false" strings become booleans, and integers sent as
    strings become integers. Nothing else is coerced; anything still off fails the strict schema."""
    kind = schema.get("type")
    if (kind == "object" and isinstance(value, (str, int, float)) and not isinstance(value, bool)
            and set(schema.get("required", [])) == {"value", "span"}):
        # "payee": "房东" / "amount": "3000" → {value, span}; the span is still checked against the user's words.
        return {"value": str(value), "span": str(value)}
    if kind == "object" and isinstance(value, dict):
        props, required = schema.get("properties", {}), set(schema.get("required", []))
        return {k: _normalize(v, props.get(k, {})) for k, v in value.items() if not (v is None and k not in required)}
    if kind == "array" and isinstance(value, list):
        return [_normalize(v, schema.get("items", {})) for v in value]
    if kind == "boolean" and value in ("true", "false"):
        return value == "true"
    if kind == "integer" and isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        return int(value)
    if kind == "string" and isinstance(value, (int, float)) and not isinstance(value, bool) and "enum" not in schema:
        return str(value)
    return value


def _scan_forbidden(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise InjectionDetected("FORBIDDEN_TOOL_FIELD", str(key))
            _scan_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden(item)


FORWARDED_MESSAGE = "你转来的内容里提到了要办理的操作。那是别人写的文字，我不会照着办理；如果确实是你本人要办，请用自己的话直接告诉我。"
# Operations that carry no quoted span of their own: when the latest message contains forwarded text, the customer's own
# words must name the operation.
OWN_OPERATION_WORDS = {"FREEZE": r"冻结|锁|停用", "UNFREEZE": r"解冻|解锁|恢复", "REPORT_LOST": r"挂失|丢|偷|掉了",
                       "CANCEL_LOST": r"解除挂失|解挂|找到", "PIN_RESET": r"密码", "APPLY_VIRTUAL": r"虚拟卡|申请|办.{0,2}卡"}


class ToolCallValidator:
    def __init__(self, corpus, catalog, resolve_payee, now=None, default_account_id="acct-demo"):
        """corpus: the user's own utterances in this session (newest last).
        catalog: {"cards":[...], "subscriptions":[...], "direct_debits":[...], "products":[...], "accounts":[...]}.
        resolve_payee: value -> list of payee rows owned by the caller."""
        from backend.chat.quoted import own_words
        self.corpus = _norm("\n".join(corpus))
        self.latest = _norm(corpus[-1] if corpus else "")
        # Proposals are grounded in the customer's own words only, never in text they pasted or forwarded.
        self.own = _norm("\n".join(own_words(t) for t in corpus))
        self.forwarded = bool(corpus) and own_words(corpus[-1]) != corpus[-1]
        self.latest_own = _norm(own_words(corpus[-1]) if corpus else "")
        self.proposing = False
        self.catalog = catalog
        self.resolve_payee = resolve_payee
        self.now = now if now is not None else datetime.now(CHINA).timestamp()
        self.account_id = default_account_id

    # ---- primitives --------------------------------------------------------------------------
    def span(self, span, slot, label):
        if not isinstance(span, str) or not _norm(span) or _norm(span) not in self.corpus:
            raise Clarify("SPAN_NOT_GROUNDED", slot, f"我没能在你的话里确认{label}，请再说一下{label}。")
        if self.proposing and _norm(span) not in self.own:
            raise Clarify("FORWARDED_TEXT", slot, FORWARDED_MESSAGE)
        return span

    def amount(self, value, slot="amount", label="金额"):
        self.span(value["span"], slot, label)
        span = _norm(value["span"])
        # "一些 / 一点 / 一下" are not amounts: the span must occur at least once as a standalone number.
        if not any(self.corpus[m.end():m.end() + 1] not in "些点下会儿" or m.end() == len(self.corpus)
                   for m in re.finditer(re.escape(span), self.corpus)):
            raise Clarify("AMOUNT_NOT_GROUNDED", slot, f"请确认{label}是多少元？")
        expected = parse_amount_value(value["value"])
        parsed = parse_amount_span(value["span"])
        if parsed is None and expected is not None:
            # Models often quote a little context ("超过2000", "2000的消费"). Accept it only when exactly one amount-like
            # piece inside the quoted span parses to the stated value.
            pieces = {parse_amount_span(m.group(0)) for m in AMOUNT_PIECE.finditer(value["span"])} - {None}
            parsed = expected if pieces == {expected} else None
        if expected is None or parsed is None or parsed != expected:
            raise Clarify("AMOUNT_NOT_GROUNDED", slot, f"请确认{label}是多少元？")
        return expected

    def payee(self, value):
        self.span(value["span"], "payee", "收款人")
        name, span = value["value"].strip(), value["span"].strip()
        related = (name == span or name in span or span in name
                   or any(name == canon and any(alias in span for alias in aliases) for canon, aliases in RELATIONS.items()))
        if not related:
            raise Clarify("PAYEE_NOT_GROUNDED", "payee", "请确认收款人是谁？")
        rows = self.resolve_payee(name) if name else []
        if not rows and span != name:
            rows, name = self.resolve_payee(span), span
        if not rows:
            raise Clarify("PAYEE_NOT_FOUND", "payee",
                          f"通讯录里没有找到“{value['value']}”。如果是新收款人，请告诉我对方的姓名和手机号，我先帮你添加（需要验证）。")
        if len(rows) > 1:
            raise Clarify("PAYEE_AMBIGUOUS", "payee", f"找到多个“{value['value']}”，要转给哪一位？",
                          [row["name"] for row in rows][:6])
        return rows[0]["name"], rows[0]

    def ref(self, value, kind, label):
        self.span(value["span"], kind, label)
        items = {item["id"]: item for item in self.catalog.get(kind + "s", [])}
        if value["id"] not in items:
            options = [item["display"] for item in items.values()][:6]
            raise Clarify("RESOURCE_NOT_IN_CATALOG", kind, f"没有找到你说的{label}，是下面哪一个？", options)
        return items[value["id"]]

    def when(self, value):
        self.span(value["span"], "when", "执行时间")
        if not TIME_MARKERS.search(value["span"]):
            raise Clarify("TIME_OF_DAY_MISSING", "when", "请告诉我具体几点执行，例如“周五上午9点”。")
        try:
            moment = datetime.fromisoformat(value["iso"])
        except ValueError:
            raise Clarify("WHEN_INVALID", "when", "请再说一下执行日期和时间。") from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=CHINA)
        stamp = moment.timestamp()
        if not self.now < stamp <= self.now + MAX_SCHEDULE_SECONDS:
            raise Clarify("WHEN_OUT_OF_RANGE", "when", "执行时间需要在未来一年以内，请重新说一下时间。")
        return stamp, moment

    def _single_card(self, lost=False):
        cards = [c for c in self.catalog.get("cards", [])
                 if ((c.get("status") == "LOST") if lost else c.get("status") not in {"LOST", "CLOSED"})]
        if len(cards) == 1:
            return cards[0]
        raise Clarify("CARD_REQUIRED", "card", "要操作哪张卡？", [c["display"] for c in cards][:6])

    # ---- entry -------------------------------------------------------------------------------
    def validate(self, name, arguments):
        if name not in TOOLS:
            raise InjectionDetected("UNKNOWN_TOOL", name)
        if not isinstance(arguments, dict):
            raise Clarify("ARGUMENTS_INVALID", "other", "我没理解清楚，能换个说法再说一次吗？")
        _scan_forbidden(arguments)
        spec = TOOLS[name]
        schema = {"type": "object", "additionalProperties": False, "properties": spec["parameters"], "required": spec.get("required", [])}
        arguments = _normalize(arguments, schema)
        try:
            _check_schema(arguments, schema)
        except ValueError as exc:
            # The schema path goes into the decision trace so a failing model call can be diagnosed.
            raise Clarify("ARGUMENTS_INVALID:" + str(exc)[:80], "other", "我没理解清楚，能换个说法再说一次吗？") from None
        handler = getattr(self, "_" + name, None)
        self.proposing = spec["kind"] == "propose"
        steps = handler(arguments) if handler else [{"kind": spec["kind"], "tool": name, "args": arguments}]
        for step in steps:
            step.setdefault("kind", spec["kind"])
            step.setdefault("tool", name)
        return steps

    # ---- read tools --------------------------------------------------------------------------
    def _ask_user(self, a):
        # Never render arbitrary model prose via a clarification tool.
        questions={'amount':'请说明本次业务的金额。','payee':'请说明收款人。','when':'请说明办理日期和具体时间。','card':'请选择要使用的银行卡。','product':'请选择要办理的理财产品。','operation':'你要办理哪项本站业务？','other':'请补充本次银行业务的对象、金额或期数；生活服务可打开官方网站。'}
        return [{'kind':'control','tool':'ask_user','args':{'slot':a['slot'],'question':questions.get(a['slot'],questions['other'])}}]

    def _get_extended(self,a):
        return [{"local":"EXTENDED:"+a["page"]}]

    def _propose_extension(self,a):
        from backend.chat.extension_agent import validate
        return validate(self,a)

    def _get_finance(self, a):
        return [{'local':'FINANCE'}]

    def _propose_finance(self, a):
        op=a['operation']; n={'task_type':'FINANCE_MANAGEMENT','operation':op,'needs_clarification':False}
        words={'LOAN_DRAW':r'贷款|借款','INSTALLMENT_START':r'分期','CREDIT_REPAY':r'还款|还.*期','CREDIT_SETTLE':r'结清|提前还','GOLD_BUY':r'黄金|买金','GOLD_SELL':r'黄金|卖金'}
        if not re.search(words[op],self.corpus):raise Clarify('OPERATION_UNGROUNDED','operation','请明确说明你要办理的本站业务。')
        if op in {'CREDIT_REPAY','CREDIT_SETTLE'}:
            if 'contract' not in a:raise Clarify('CONTRACT_REQUIRED','other','请选择要还款的合同，可先查询贷款与分期。')
            n['target_id']=self.ref(a['contract'],'contract','融资合同')['id']
        else:
            card=self.ref(a['card'],'card','银行卡') if 'card' in a else self._single_card()
            draft={'card_id':card['id'],'type':{'LOAN_DRAW':'loan','INSTALLMENT_START':'installment','GOLD_BUY':'gold_buy','GOLD_SELL':'gold_sell'}[op]}
            if op in {'LOAN_DRAW','INSTALLMENT_START'}:
                if 'months' not in a:raise Clarify('MONTHS_REQUIRED','other','请选择 3、6 或 12 期。')
                m=a['months'];self.span(m['span'],'other','期数')
                if parse_amount_span(m['span'].replace('个月','').replace('期','')) != m['value']*100 or m['value'] not in (3,6,12):raise Clarify('MONTHS_UNGROUNDED','other','请重新说明期数：3、6 或 12 期。')
                draft['months']=m['value']
            field='grams' if op.startswith('GOLD_') else 'amount'
            if op=='INSTALLMENT_START':
                if 'purchase' not in a:raise Clarify('SOURCE_REQUIRED','other','请选择已入账的消费，可在贷款与分期页面查看。')
                draft['source_id']=self.ref(a['purchase'],'purchase','消费记录')['id']
            else:
                if field not in a:raise Clarify('AMOUNT_REQUIRED','amount','请说明交易重量（克）。' if field=='grams' else '请输入贷款金额。')
                value=dict(a[field])
                if field=='grams':
                    self.span(value['span'],'other','克数');value['span']=value['span'].replace('克','').strip()
                draft['units' if field=='grams' else 'amount']=self.amount(value)
            n['finance_draft']=draft
        return [{'normalized':n}]

    def _get_balance(self, a):
        account = a.get("account_id", self.account_id)
        if account not in {x["id"] for x in self.catalog.get("accounts", [])}:
            account = self.account_id
        return [{"normalized": {"task_type": "BALANCE_QUERY", "source_account_id": account, "needs_clarification": False}}]

    def _analyze_spending(self, a):
        if "year" in a:
            return [{"normalized": {"task_type": "BILL_ANALYSIS", "year": a["year"], "needs_clarification": False}}]
        month = a.get("month") or datetime.fromtimestamp(self.now, CHINA).strftime("%Y-%m")
        return [{"normalized": {"task_type": "BILL_ANALYSIS", "month": month, "needs_clarification": False}}]

    def _query_spending(self, a):
        from backend.business import month_window
        if "months" in a:
            months = sorted(set(a["months"]))
        else:
            count = a.get("recent_months", 1)
            months = [datetime.fromtimestamp(self.now, CHINA).strftime("%Y-%m")]
            while len(months) < count:
                months.insert(0, month_window(months[0])[2])
        merchant = None
        if "merchant" in a:
            self.span(a["merchant"]["span"], "other", "商户")
            if _norm(a["merchant"]["text"]) not in _norm(a["merchant"]["span"]):
                raise Clarify("MERCHANT_NOT_GROUNDED", "other", "是哪个商户？")
            merchant = a["merchant"]["text"].strip()
        return [{"normalized": {"task_type": "SPENDING_QUERY", "months": months, "category": a.get("category"), "merchant": merchant,
                                "compare": a.get("compare", True), "needs_clarification": False}}]

    def _get_portfolio(self, a):
        return [{"normalized": {"task_type": "PORTFOLIO_QUERY", "needs_clarification": False}}]

    def _recommend_products(self, a):
        return [{"normalized": {"task_type": "WEALTH_RECOMMENDATION", "needs_clarification": False}}]

    def _compare_products(self, a):
        ids = [self.ref(p, "product", "理财产品")["id"] for p in a["products"]]
        if len(set(ids)) != len(ids):
            raise Clarify("DUPLICATE_PRODUCT", "product", "请说出要对比的两个不同产品。")
        return [{"normalized": {"task_type": "PRODUCT_COMPARE", "product_ids": ids, "needs_clarification": False}}]

    def _list_subscriptions(self, a):
        return [{"normalized": {"task_type": "SUBSCRIPTION_MANAGEMENT", "operation": "DETECT" if a.get("detect") else "LIST",
                                "needs_clarification": False}}]

    def _list_direct_debits(self, a):
        return [{"normalized": {"task_type": "DIRECT_DEBIT_MANAGEMENT", "operation": "LIST", "needs_clarification": False}}]

    def _list_cards(self, a):
        return [{"normalized": {"task_type": "CARD_MANAGEMENT", "operation": "LIST", "needs_clarification": False}}]

    def _renewal_reminders(self, a):
        return [{"normalized": {"task_type": "RENEWAL_QUERY", "needs_clarification": False}}]

    # ---- proposal tools ----------------------------------------------------------------------
    def _propose_transfer(self, a):
        name, payee = self.payee(a["payee"])
        cents = self.amount(a["amount"])
        display = {"payee": name, "amount": format_cents(cents)}
        spans = {"payee": a["payee"]["span"], "amount": a["amount"]["span"]}
        memo = None
        if "memo" in a:
            self.span(a["memo"]["span"], "other", "备注")
            if _norm(a["memo"]["text"]) not in _norm(a["memo"]["span"]):
                raise Clarify("MEMO_NOT_GROUNDED", "other", "备注写什么？")
            memo = a["memo"]["text"].strip()
            spans["memo"] = a["memo"]["span"]
        if "recurrence" in a:
            self.span(a["recurrence"]["span"], "when", "重复频率")
            if "when" not in a:
                raise Clarify("FIRST_RUN_MISSING", "when", "第一次在哪天几点转？例如“每月1号上午9点”。")
            stamp, moment = self.when(a["when"])
            spans["when"] = a["when"]["span"]
            rec = a["recurrence"]
            plan = {"event_type": "RECURRING_TRANSFER", "execute_at": stamp,
                    "parameters": {"recipient_name": name, "amount": cents, "frequency": rec["frequency"], "remaining": rec["count"]}}
            label = {"MONTHLY": "每月", "WEEKLY": "每周"}[rec["frequency"]]
            return [{"kind": "plan", "events": [plan], "spans": spans, "memo": memo,
                     "display": display | {"when": moment.strftime("%Y-%m-%d %H:%M"), "frequency": label, "count": rec["count"]}}]
        if "when" in a:
            stamp, moment = self.when(a["when"])
            spans["when"] = a["when"]["span"]
            display["when"] = moment.astimezone(CHINA).strftime("%Y-%m-%d %H:%M")
            normalized = {"task_type": "SCHEDULED_TRANSFER", "recipient_name": name, "amount": cents, "currency": "CNY",
                          "execute_at": stamp, "timezone": "Asia/Shanghai", "needs_clarification": False}
        else:
            normalized = {"task_type": "TRANSFER", "recipient_name": name, "amount": cents, "currency": "CNY",
                          "needs_clarification": False}
        return [{"normalized": normalized, "spans": spans, "display": display, "memo": memo}]

    def _propose_add_payee(self, a):
        from backend.domain import uid
        name = self.span(a["name"]["span"], "payee", "收款人姓名") and a["name"]["value"].strip()
        if _norm(name) not in _norm(a["name"]["span"]):
            raise Clarify("PAYEE_NAME_NOT_GROUNDED", "payee", "收款人叫什么名字？")
        self.span(a["phone"]["span"], "payee", "手机号")
        if a["phone"]["value"] not in re.sub(r"[\s-]", "", _norm(a["phone"]["span"])):
            raise Clarify("PHONE_NOT_GROUNDED", "payee", "请再说一下对方的手机号。")
        alias = ""
        if "alias" in a:
            self.span(a["alias"]["span"], "payee", "备注名")
            alias = a["alias"]["value"].strip()
        return [{"normalized": {"task_type": "PAYEE_MANAGEMENT", "operation": "ADD_PAYEE", "target_id": "payee-n" + uid().replace("-", "")[:10],
                                "parameters": {"name": name, "phone": a["phone"]["value"], "alias": alias}, "needs_clarification": False},
                 "spans": {"payee": a["name"]["span"], "phone": a["phone"]["span"]}, "display": {"payee": name}}]

    def _propose_split(self, a):
        cents = self.amount(a["total"], "amount", "总金额")
        names = [self.payee(p)[0] for p in a["participants"]]
        if len(set(names)) != len(names):
            raise Clarify("DUPLICATE_PARTICIPANT", "payee", "参与人有重复，请再确认一下有哪些人？")
        include_self = a.get("include_me", True)
        if len(names) + include_self < 2:
            raise Clarify("SPLIT_NEEDS_PEOPLE", "payee", "AA 至少需要两个人，还有谁一起分摊？")
        if cents < len(names) + include_self:
            raise Clarify("SPLIT_TOO_SMALL", "amount", "总金额太小，无法分摊，请确认金额。")
        return [{"normalized": {"task_type": "SPLIT_REQUEST", "amount": cents, "participants": names, "currency": "CNY",
                                "include_self": include_self, "needs_clarification": False},
                 "spans": {"amount": a["total"]["span"]}, "display": {"amount": format_cents(cents), "participants": names}}]

    def _propose_investment(self, a):
        product = self.ref(a["product"], "product", "理财产品")
        cents = self.amount(a["amount"])
        kind = "INVESTMENT_PURCHASE" if a["op"] == "PURCHASE" else "INVESTMENT_REDEEM"
        return [{"normalized": {"task_type": kind, "operation": kind, "target_id": product["id"],
                                "parameters": {"amount": cents, "currency": "CNY"}, "needs_clarification": False},
                 "spans": {"amount": a["amount"]["span"], "product": a["product"]["span"]},
                 "display": {"product": product["display"], "amount": format_cents(cents)}}]

    def _propose_card_operation(self, a):
        op = a["op"]
        if self.forwarded and op in OWN_OPERATION_WORDS and not re.search(OWN_OPERATION_WORDS[op], self.latest_own):
            raise Clarify("FORWARDED_TEXT", "operation", FORWARDED_MESSAGE)
        if op == "APPLY_VIRTUAL":
            return [{"normalized": {"task_type": "CARD_MANAGEMENT", "operation": "CARD_APPLY", "target_id": self.account_id,
                                    "source_account_id": self.account_id, "parameters": {}, "needs_clarification": False},
                     "display": {"operation": "申请虚拟卡"}}]
        card = self.ref(a["card"], "card", "银行卡") if "card" in a else self._single_card(lost=op == "CANCEL_LOST")
        base = {"task_type": "CARD_MANAGEMENT", "target_id": card["id"], "needs_clarification": False}
        if op in {"FREEZE", "UNFREEZE", "REPORT_LOST", "CANCEL_LOST", "PIN_RESET"}:
            labels = {"FREEZE": "冻结", "UNFREEZE": "解冻", "REPORT_LOST": "挂失", "CANCEL_LOST": "解除挂失", "PIN_RESET": "重置密码"}
            return [{"normalized": base | {"operation": "CARD_" + op, "parameters": {}},
                     "display": {"operation": labels[op], "card": card["display"]}}]
        if op == "LIMIT_CHANGE":
            if "limit" not in a:
                raise Clarify("LIMIT_MISSING", "amount", "要把单日限额调整到多少元？")
            cents = self.amount(a["limit"], "amount", "限额")
            return [{"normalized": base | {"operation": "CARD_LIMIT_CHANGE", "parameters": {"spending_limit": cents}},
                     "spans": {"limit": a["limit"]["span"]},
                     "display": {"operation": "调整限额", "card": card["display"], "limit": format_cents(cents)}}]
        controls = a.get("controls") or {}
        if not controls:
            raise Clarify("CONTROLS_MISSING", "operation", "要开启还是关闭哪项交易：线上支付、境外交易、还是非接触支付？")
        self.span(a.get("controls_span", ""), "operation", "要调整的交易开关")
        current = card.get("controls", {})
        params = {"online_enabled": controls.get("online", bool(current.get("online"))),
                  "international_enabled": controls.get("international", bool(current.get("international"))),
                  "contactless_enabled": controls.get("contactless", bool(current.get("contactless")))}
        return [{"normalized": base | {"operation": "CARD_TRANSACTION_CONTROL", "parameters": params},
                 "display": {"operation": "交易开关", "card": card["display"]}}]

    def _propose_subscription_cancel(self, a):
        steps = []
        for item in a["subscriptions"]:
            sub = self.ref(item, "subscription", "订阅")
            steps.append({"normalized": {"task_type": "SUBSCRIPTION_MANAGEMENT", "operation": "SUBSCRIPTION_CANCEL",
                                         "target_id": sub["id"], "parameters": {}, "needs_clarification": False},
                          "display": {"operation": "取消订阅", "subscription": sub["display"]}})
        return steps

    def _propose_direct_debit_disable(self, a):
        steps = []
        for item in a["direct_debits"]:
            dd = self.ref(item, "direct_debit", "代扣")
            steps.append({"normalized": {"task_type": "DIRECT_DEBIT_MANAGEMENT", "operation": "DIRECT_DEBIT_DISABLE",
                                         "target_id": dd["id"], "parameters": {}, "needs_clarification": False},
                          "display": {"operation": "关闭代扣", "direct_debit": dd["display"]}})
        return steps

    def _review_subscriptions(self, a):
        month = a.get("month") or datetime.fromtimestamp(self.now, CHINA).strftime("%Y-%m")
        return [{"normalized": {"task_type": "CROSS_SCENARIO", "month": month, "needs_clarification": False}}]

    def _start_risk_assessment(self, a):
        return [{"op": "RISK_QUESTIONNAIRE"}]

    def _list_wish_funds(self, a):
        return [{"normalized": {"task_type": "WISH_FUND_MANAGEMENT", "operation": "LIST", "needs_clarification": False}}]

    def _propose_fund_release(self, a):
        fund = self.ref(a["fund"], "wish_fund", "心愿金")
        return [{"normalized": {"task_type": "WISH_FUND_MANAGEMENT", "operation": "FUND_RELEASE", "target_id": fund["id"],
                                "parameters": {}, "needs_clarification": False}}]

    def _propose_wish_plan(self, a):
        """Cross-scenario plan: lock now (confirm card) + dated proposal-only events (order, release)."""
        from datetime import timedelta
        from backend.domain import uid
        cents = self.amount(a["lock_amount"], "amount", "锁定金额")
        today = datetime.fromtimestamp(self.now, CHINA).date()
        if "event" in a:
            event = self.ref(a["event"], "life_event", "日子")
            from backend.banking.personal import next_occurrence
            day = next_occurrence(event['month'],event['day'],today)
            if (day-today).days>366:raise Clarify('DATE_OUT_OF_RANGE','when','下一个生日超过一年，请临近时再建立资金计划。')
            label = event["display"]
        elif "date" in a:
            self.span(a["date"]["span"], "when", "日期")
            try:
                day = datetime.fromisoformat(a["date"]["iso"]).date()
            except ValueError:
                raise Clarify("DATE_INVALID", "when", "是哪一天？请说具体日期。") from None
            if not today <= day <= today + timedelta(days=366):
                raise Clarify("DATE_OUT_OF_RANGE", "when", "日期需要在未来一年内，是哪一天？")
            label = day.strftime("%m月%d日")
        else:
            raise Clarify("WISH_DATE_MISSING", "when", "这笔心愿金是为哪一天准备的？")
        items = []
        for item in a.get("items", []):
            merchant = self.ref(item, "merchant", "商品")
            items.append({"merchant_id": merchant["id"], "amount": merchant["price"], "display": merchant["display"]})
        if len({i["merchant_id"] for i in items}) != len(items):
            raise Clarify("DUPLICATE_ITEM", "other", "商品有重复，请再确认一下要订哪些？")
        total = sum(i["amount"] for i in items)
        if total > cents:
            raise Clarify("ORDER_EXCEEDS_FUND", "amount",
                          f"要订的商品合计 {format_cents(total)}，超过锁定金额 {format_cents(cents)}。要锁定多少？")
        midnight = lambda d, hour=0, minute=0: datetime(d.year, d.month, d.day, hour, minute, tzinfo=CHINA).timestamp()
        end_day = self.date(a['end_date'], '结束日期') if 'end_date' in a else day
        if end_day < day:raise Clarify('DATE_INVALID','when','结束日期不能早于活动日期。')
        release_at = midnight(end_day + timedelta(days=1), 23, 59)
        fund_id = "wish-" + uid().replace("-", "")[:12]
        events, nodes = [], []
        if items:
            days = a.get("order_days_before", 2)
            order_at = max(midnight(day - timedelta(days=days), 10), self.now + 120)
            events.append({"event_type": "WISH_ORDER", "execute_at": order_at,
                           "parameters": {"wish_fund_id": fund_id, "items": [{"merchant_id": i["merchant_id"], "amount": i["amount"]} for i in items]}})
            nodes.append({"id": "n2", "title": "下单：" + "、".join(i["display"].split("（")[0] for i in items),
                          "kind": "WRITE", "status": "SCHEDULED", "depends_on": ["n1"], "scheduled_at": order_at, "amount_minor": total})
        events.append({"event_type": "WISH_RELEASE", "execute_at": release_at, "parameters": {"wish_fund_id": fund_id}})
        nodes.append({"id": f"n{len(nodes) + 2}", "title": "到期后剩余资金释放回活期", "kind": "WRITE", "status": "SCHEDULED",
                      "depends_on": ["n1"], "scheduled_at": release_at})
        name = a["name"].strip()
        lock = {"normalized": {"task_type": "WISH_FUND_MANAGEMENT", "operation": "FUND_LOCK", "target_id": fund_id,
                               "parameters": {"amount": cents, "currency": "CNY", "name": name, "release_at": release_at},
                               "needs_clarification": False},
                "spans": {"amount": a["lock_amount"]["span"]}, "has_plan": True}
        plan = {"kind": "plan", "events": events, "link_to_previous": True,
                "plan": {"goal": f"{label}：锁定 {format_cents(cents)}" + (f"，{day.month}月{day.day}日前下单" if items else ""),
                         "nodes": [{"id": "n1", "title": f"锁定心愿金 {format_cents(cents)}（{name}）", "kind": "WRITE",
                                    "status": "AWAITING_CONFIRMATION"}] + nodes}}
        return [lock, plan]

    # ---- 联动规则 / 出行管家 ----------------------------------------------------------------------
    def date(self, value, label="日期"):
        """A calendar day the user said (no time of day needed). Returns the Asia/Shanghai date."""
        self.span(value["span"], "when", label)
        try:
            day = datetime.fromisoformat(value["iso"][:10]).date()
        except ValueError:
            raise Clarify("DATE_INVALID", "when", f"{label}是哪一天？") from None
        today = datetime.fromtimestamp(self.now, CHINA).date()
        if not today <= day <= today + timedelta(days=366):
            raise Clarify("DATE_OUT_OF_RANGE", "when", f"{label}需要在未来一年内，是哪一天？")
        return day

    def _automation_action(self, item, trigger_type):
        kind = item["type"]  # an action-level "span" is descriptive only; amounts / payees carry their own grounded spans
        action = {"type": kind}
        if kind in {"TRANSFER", "WISH_LOCK", "INVEST"}:
            sources = [k for k in ("amount", "percent", "from_subscriptions") if k in item]
            if len(sources) != 1:
                raise Clarify("AUTOMATION_AMOUNT", "amount", "触发时要动用多少钱？请说一个金额。")
            if "amount" in item:
                action["amount"] = self.amount(item["amount"])
            elif "percent" in item:
                if trigger_type != "INCOME":
                    raise Clarify("AUTOMATION_PERCENT", "amount", "按比例只能用于“入账时”的规则，请说一个具体金额。")
                self.span(item["percent"]["span"], "amount", "比例")
                action["percent"] = item["percent"]["value"]
            else:
                action["from_subscriptions"] = [self.ref(x, "subscription", "订阅")["id"] for x in item["from_subscriptions"]]
        if kind == "TRANSFER":
            if "payee" not in item:
                raise Clarify("AUTOMATION_PAYEE", "payee", "要转给谁？")
            action["payee_name"] = self.payee(item["payee"])[0]
        if kind == "INVEST":
            if "product" not in item:
                raise Clarify("AUTOMATION_PRODUCT", "product", "要定投哪个产品？",
                              [p["display"] for p in self.catalog.get("products", [])][:6])
            action["product_id"] = self.ref(item["product"], "product", "理财产品")["id"]
        if kind == "WISH_LOCK":
            action["fund_name"] = (item.get("fund_name") or "储蓄").strip()[:20] or "储蓄"
        if kind in {"CARD_FREEZE", "CARD_CONTROL"}:
            action["card_id"] = (self.ref(item["card"], "card", "银行卡") if "card" in item else self._single_card())["id"]
        if kind == "CARD_CONTROL":
            if not item.get("controls"):
                raise Clarify("CONTROLS_MISSING", "operation", "触发时要关闭哪项交易：线上支付、境外交易还是非接触支付？")
            action["controls"] = dict(item["controls"])
        return action

    def _propose_automation(self, a):
        t = a["trigger"]
        self.span(t["span"], "other", "触发条件")
        trigger = {"type": t["type"]}
        if t["type"] == "SCHEDULE":
            if "first_at" not in t:
                raise Clarify("AUTOMATION_WHEN", "when", "从什么时候开始？请说具体日期和时间，例如“每月1号上午9点”。")
            trigger["first_at"], _ = self.when(t["first_at"])
            trigger["frequency"] = t.get("frequency", "ONCE")
        else:
            if "amount" not in t:
                raise Clarify("AUTOMATION_THRESHOLD", "amount", "触发的金额是多少？")
            trigger["amount"] = self.amount(t["amount"], "amount", "触发金额")
            if t["type"] == "SPEND_OVER" and "category" in t:
                trigger["category"] = t["category"]
        actions = [self._automation_action(item, t["type"]) for item in a["actions"]]
        spec = {"name": a.get("name") or "联动规则", "trigger": trigger, "actions": actions}
        if "max_runs" in a:
            spec["max_runs"] = min(a["max_runs"], 60)
        return [{"kind": "automation", "specs": [spec], "auto_tighten": a.get("auto_tighten", False)}]

    def _cancel_automation(self, a):
        return [{"kind": "control", "tool": "cancel_automation", "args": {"rule_id": self.ref(a["rule"], "automation", "联动规则")["id"]}}]

    def _list_automations(self, a):
        return [{"kind": "read", "tool": "list_automations", "local": "AUTOMATIONS"}]

    def _list_income(self, a):
        return [{"kind": "read", "tool": "list_income", "local": "INCOME"}]

    def _propose_trip_plan(self, a):
        start, end = self.date(a["start"], "出发日期"), self.date(a["end"], "返回日期")
        if end < start or (end - start).days > 60:
            raise Clarify("TRIP_DATES", "when", "返回日期需要在出发之后（60 天以内），请再确认一下日期。")
        budget = self.amount(a["budget"], "amount", "预算")
        if "destination" in a:
            self.span(a["destination"]["span"], "other", "目的地")
        card = self._single_card()
        midnight = lambda d, h=0, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=CHINA).timestamp()
        return [{"kind": "trip", "budget": budget, "start": midnight(start), "end": midnight(end, 23, 59),
                 "destination": (a.get("destination") or {}).get("text", "")[:20], "card_id": card["id"],
                 "international": a.get("international", True)}]

    def _flag_risk(self, a):
        # The model may raise risk, never lower it. Evidence must be the user's words.
        if _norm(a["evidence_span"]) not in self.corpus:
            a = a | {"evidence_span": ""}
        return [{"kind": "control", "tool": "flag_risk", "args": a}]
