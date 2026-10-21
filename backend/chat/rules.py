"""Deterministic fallback understanding (no model).

Produces the SAME tool calls the model would, with spans copied from the user's text, so the
result goes through the same ToolCallValidator, Policy and authorization. Used when the model is
unavailable, slow, or its circuit breaker is open. It never invents values: anything it cannot
read from the text is left out and the validator turns it into a question.
"""
import re
from datetime import datetime
from backend.business import CHINA

NUM = r"(?:[0-9]+(?:\.[0-9]+)?\s*[万萬wWkK千]?[0-9一二两三四五六七八九]?|[零〇一二两三四五六七八九十百千万]+(?![些点下会儿丢]))(?:块钱|块|元|圆)?(?:[0-9一二两三四五六七八九][毛角])?"
NAME = r"[一-龥A-Za-z0-9]{1,10}?"
PHONE = r"1[3-9][0-9]{9}"
VERB = r"(?:转账|转|打|汇)"
CATEGORY_WORDS = [(r"吃饭|餐饮|外卖|饭钱|餐厅|咖啡", "FOOD"), (r"打车|交通|地铁|公交|出行", "TRANSPORT"),
                  (r"网购|购物|买东西|衣服", "SHOPPING"), (r"超市|日用|买菜", "GROCERIES")]
CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十二": 12, "十一": 11}


def _amount_value(span):
    from backend.chat.amounts import parse_amount_span
    cents = parse_amount_span(span)
    if cents is None:
        return None
    return f"{cents // 100}.{cents % 100:02d}"


def _call(name, **arguments):
    return {"name": name, "arguments": arguments}


def _month_offset(months_back):
    now = datetime.now(CHINA)
    year, month = now.year, now.month - months_back
    while month <= 0:
        year, month = year - 1, month + 12
    return f"{year}-{month:02d}"


SYNONYMS = {"电费": "电力", "水费": "水务", "燃气费": "燃气", "话费": "通信", "网费": "宽带", "视频会员": "视频", "网盘": "云盘"}


def _catalog_match(items, text):
    """Catalogue items sharing a 2+ character word with the text (视频 -> 演示视频). The returned span is always
    the user's own word, so a synonym hit (电费 -> 电力) is grounded on 电费."""
    aliases = {canonical: word for word, canonical in SYNONYMS.items() if word in text}
    hits = []
    for item in items:
        name = re.split(r"[（( ]", item["display"])[0]
        for size in (4, 3, 2):
            for i in range(len(name) - size + 1):
                piece = name[i:i + size]
                if piece in {"演示", "每月"}:
                    continue
                if piece in text:
                    hits.append((item, piece))
                    break
                if piece in aliases:
                    hits.append((item, aliases[piece]))
                    break
            else:
                continue
            break
    return hits


TIME_WORDS = re.compile(r"明天|后天|下周|下个?月|每月|每周|每天|星期|周[一二三四五六日天]|[0-9一二三四五六七八九十]+[号日]|[0-9一二三四五六七八九十]+点|月底|月初|定时|以后")


def transfer_calls(text, pending_payee=None):
    if TIME_WORDS.search(text):
        # Dates and recurrence need real understanding. Never turn a scheduled request into an immediate one.
        return [_call("ask_user", slot="when",
                      question="定时或周期转账需要智能理解，我现在只能处理立即转账。请稍后再试，或说“下周五09:00给张三转1000元”这样的标准说法。")] \
            if not re.search(r"[0-9]{1,2}:[0-9]{2}|[0-9]{4}-[0-9]{2}-[0-9]{2}", text) else []
    change = re.search(rf"(?:改成|改为|换成|改到)\s*(?P<a>{NUM})", text)
    if change and pending_payee and _amount_value(change["a"]):
        return [_call("propose_transfer", payee={"value": pending_payee, "span": pending_payee},
                      amount={"value": _amount_value(change["a"]), "span": change["a"].strip()})]
    memo = re.search(r"备注(?:是|为|写)?[：:]?\s*([一-龥A-Za-z0-9]{1,10})", text)
    body = text[: memo.start()] if memo else text
    patterns = [rf"(?:给|向)\s*(?P<p>{PHONE}|{NAME})\s*{VERB}\s*(?:账)?\s*[，,]?\s*(?P<a>{NUM})",
                rf"{VERB}\s*(?P<a>{NUM})\s*(?:给|到)\s*(?P<p>{PHONE}|{NAME})(?:$|[，,。\s的])",
                rf"(?P<p>{NAME})\s*转账\s*(?P<a>{NUM})"]
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            amount = match["a"].strip()
            value = _amount_value(amount)
            payee = re.sub(r"^(我的|我)", "", match["p"]) or match["p"]
            args = {"payee": {"value": payee, "span": match["p"]}}
            if value:
                args["amount"] = {"value": value, "span": amount}
            if memo:
                args["memo"] = {"text": memo[1], "span": memo[0]}
            return [_call("propose_transfer", **args)] if value else [
                _call("ask_user", slot="amount", question=f"要给{payee}转多少钱？")]
    match = re.search(rf"(?:给|向)\s*(?P<p>{NAME})\s*{VERB}", body)
    if match:
        return [_call("ask_user", slot="amount", question=f"要给{match['p']}转多少钱？")]
    return []


def spending_calls(text):
    if not re.search(r"花了|花费|消费|支出|账单|开销|花在|钱都花|异常|可疑交易", text):
        return []
    if re.search(r"异常|可疑|重复扣", text):
        return [_call("analyze_spending", month=_month_offset(1 if "上个月" in text else 0))]
    if re.search(r"年度|全年|今年", text):
        return [_call("analyze_spending", year=datetime.now(CHINA).year)]
    args = {}
    recent = re.search(r"(?:最近|近)\s*([0-9]+|[一二两三四五六七八九十]+)\s*个?月", text)
    explicit = re.findall(r"(20[0-9]{2})[-年/]\s*([0-9]{1,2})\s*月?", text)
    if recent:
        count = int(recent[1]) if recent[1].isdigit() else CN_DIGIT.get(recent[1], 3)
        args["recent_months"] = max(1, min(count, 12))
    elif explicit:
        args["months"] = [f"{y}-{int(m):02d}" for y, m in explicit if 1 <= int(m) <= 12]
    elif "上个月" in text or "上月" in text:
        args["months"] = [_month_offset(1)]
    else:
        args["months"] = [_month_offset(0)]
    for pattern, code in CATEGORY_WORDS:
        if re.search(pattern, text):
            args["category"] = code
            break
    return [_call("query_spending", **args)]


def card_calls(text):
    if "卡" not in text and "密码" not in text and "限额" not in text and not re.search(r"境外|线上支付|网上支付|非接触", text):
        return []
    if re.search(r"解除挂失|解挂|卡.{0,4}找到了", text):
        return [_call("propose_card_operation", op="CANCEL_LOST")]
    if re.search(r"丢了|掉了|丢失|被偷|挂失", text):
        return [_call("propose_card_operation", op="REPORT_LOST")]
    if "解冻" in text:
        return [_call("propose_card_operation", op="UNFREEZE")]
    if "冻结" in text:
        return [_call("propose_card_operation", op="FREEZE")]
    if re.search(r"密码", text):
        return [_call("propose_card_operation", op="PIN_RESET")]
    if "虚拟卡" in text and re.search(r"申请|开|办", text):
        return [_call("propose_card_operation", op="APPLY_VIRTUAL")]
    limit = re.search(rf"限额.{{0,6}}?(?P<a>{NUM})", text)
    if limit and _amount_value(limit["a"]):
        return [_call("propose_card_operation", op="LIMIT_CHANGE", limit={"value": _amount_value(limit["a"]), "span": limit["a"]})]
    control = re.search(r"(关闭|关掉|关了|禁用|开启|打开|开通)\s*(境外|海外|线上|网上|非接触|闪付)", text) or \
        re.search(r"(境外|海外|线上|网上|非接触|闪付).{0,4}(关闭|关掉|关了|禁用|开启|打开|开通)", text)
    if control:
        words = control.group(0)
        on = bool(re.search(r"开启|打开|开通", words))
        key = "international" if re.search(r"境外|海外", words) else "online" if re.search(r"线上|网上", words) else "contactless"
        return [_call("propose_card_operation", op="CONTROLS", controls={key: on}, controls_span=words)]
    if re.search(r"我的卡|几张卡|哪些卡|卡片状态", text):
        return [_call("list_cards")]
    return []


def subscription_calls(text, catalog):
    if re.search(r"续费|要扣费|扣费提醒", text):
        return [_call("renewal_reminders")]
    cancel = re.search(r"(取消|退订|关掉|关闭|停掉|关了|停了|不要了|不想要)", text)
    if cancel and "代扣" in text:
        hits = _catalog_match(catalog.get("direct_debits", []), text)
        if hits:
            return [_call("propose_direct_debit_disable", direct_debits=[{"id": i["id"], "span": w} for i, w in hits])]
        return [_call("list_direct_debits")]
    if cancel:
        hits = _catalog_match(catalog.get("subscriptions", []), text)
        if hits:
            return [_call("propose_subscription_cancel", subscriptions=[{"id": i["id"], "span": w} for i, w in hits])]
    if "订阅" in text or "会员" in text:
        return [_call("list_subscriptions", detect=True)]
    if "代扣" in text:
        return [_call("list_direct_debits")]
    return []


def product_calls(text, catalog):
    products = _catalog_match(catalog.get("products", []), text)
    if not products:
        return []
    refs = [{"id": item["id"], "span": word} for item, word in products]
    amount = re.search(rf"(?P<a>{NUM})", re.sub(r"[0-9]+天", "", text))
    value = amount and _amount_value(amount["a"])
    if re.search(r"对比|比较|哪个好", text) and len(refs) >= 2:
        return [_call("compare_products", products=refs[:4])]
    if re.search(r"赎回|卖出|取出", text):
        return [_call("propose_investment", op="REDEEM", product=refs[0], amount={"value": value, "span": amount["a"].strip()})] if value else \
            [_call("ask_user", slot="amount", question="要赎回多少钱？")]
    if re.search(r"买|申购|购买|投", text):
        return [_call("propose_investment", op="PURCHASE", product=refs[0], amount={"value": value, "span": amount["a"].strip()})] if value else \
            [_call("ask_user", slot="amount", question="要买多少钱？")]
    return []


def wealth_calls(text):
    if re.search(r"风险测评|风险评估|测评", text):
        return [_call("start_risk_assessment")]
    if re.search(r"理财|基金|收益", text) and re.search(r"推荐|买点|有什么|适合", text):
        return [_call("recommend_products")]
    if re.search(r"持仓|我的理财|买了哪些理财|理财.{0,4}(多少|还有)", text):
        return [_call("get_portfolio")]
    return []


def rule_calls(text, catalog, pending_payee=None):
    """Return tool calls or [] when nothing is recognised."""
    text = text.strip()
    if re.fullmatch(r"(你好|您好|hi|hello|嗨)[！!。.~]*", text, re.I):
        return [_call("smalltalk", kind="GREETING")]
    if re.fullmatch(r"(谢谢|多谢|感谢)[你您]?[！!。.~]*", text):
        return [_call("smalltalk", kind="THANKS")]
    if re.search(r"你能做什么|你会什么|有什么功能", text):
        return [_call("smalltalk", kind="CAPABILITIES")]
    calls = []
    for clause in re.split(r"[，,；;。]?\s*(?:然后|再|顺便|并且|接着)\s*|[；;]", text):
        if not clause:
            continue
        if re.search(r"余额|还有多少钱|剩多少钱|账上有多少", clause):
            calls.append(_call("get_balance"))
            continue
        if "心愿金" in clause:
            calls.append(_call("list_wish_funds"))
            continue
        for producer in (lambda c: transfer_calls(c, pending_payee) if (re.search(VERB, c) or re.search(r"改成|改为|换成", c))
                         and not re.search(r"代扣|订阅", c) else [],
                         card_calls, spending_calls, lambda c: product_calls(c, catalog), wealth_calls,
                         lambda c: subscription_calls(c, catalog)):
            found = producer(clause)
            if found:
                calls.extend(found)
                break
    return calls[:5]
