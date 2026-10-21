import re
from backend.domain import Rejected, cents
from backend.business import CHINA, month_window
from datetime import datetime


NUMBER = r"[0-9零〇一二两三四五六七八九十百千万点.]+"
NAME = r"[\u4e00-\u9fffA-Za-z·]{1,20}"
PREFIX = r"(?:(?:请(?:帮我)?|麻烦(?:你)?|帮我|能(?:帮我)?)\s*)?"
SUFFIX = r"\s*(?:可以吗|好吗|吧|吗)?[。.!！?？]?"
TRANSFER_PATTERNS = [
    PREFIX + rf"(?:给|向)\s*(?P<recipient>{NAME})\s*(?:转账|转|汇款|汇)\s*(?P<amount>{NUMBER})\s*(?:元|人民币|块钱?)" + SUFFIX,
    PREFIX + rf"(?:转账|转|汇款|汇)\s*(?P<amount>{NUMBER})\s*(?:元|人民币|块钱?)\s*(?:给|到|至)\s*(?P<recipient>{NAME})" + SUFFIX,
]


def amount_cents(text):
    """Explicit numeral grammar. No rounding, model conversion or inferred currency."""
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return cents(text)
    if not re.fullmatch(r"[零〇一二两三四五六七八九十百千万]+(?:点[零〇一二两三四五六七八九]{1,2})?", text):
        raise Rejected("INVALID_AMOUNT", "请用数字金额，最多两位小数。")
    digits = {c: n for n, chars in enumerate(("零〇", "一", "二两", "三", "四", "五", "六", "七", "八", "九")) for c in chars}
    integer, _, fraction = text.partition("点")
    total, section, number, last_unit, explicit_zero, had_unit = 0, 0, None, 10000, False, False
    for char in integer:
        if char in digits:
            if number not in (None, 0):
                raise Rejected("INVALID_AMOUNT", "请用数字明确金额。")
            number = digits[char]
            if number == 0:
                explicit_zero = True
        else:
            had_unit = True
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            if unit == 10000:
                if total or section + (number or 0) == 0:
                    raise Rejected("INVALID_AMOUNT")
                total = (section + (number or 0)) * unit
                section, number, last_unit = 0, None, 10000
                explicit_zero = False
            else:
                if unit >= last_unit or (number is None and unit != 10):
                    raise Rejected("INVALID_AMOUNT")
                section += (1 if number is None else number) * unit
                number, last_unit = None, unit
                explicit_zero = False
    if had_unit and number and last_unit >= 100 and not explicit_zero:
        raise Rejected("INVALID_AMOUNT", "金额存在简写歧义，请用数字明确金额。")
    value = total + section + (number or 0)
    decimal = "".join(str(digits[c]) for c in fraction)
    return cents(str(value) + ("." + decimal if decimal else ""))


def month_value(text):
    if text in {"本月", "这个月"}:
        return datetime.now(CHINA).strftime("%Y-%m")
    if text in {"上月", "上个月"}:
        return month_window(datetime.now(CHINA).strftime("%Y-%m"))[2]
    match = re.fullmatch(r"([0-9]{4})年\s*([0-9]{1,2})月", text)
    month = f"{match[1]}-{int(match[2]):02}" if match else text
    month_window(month)
    return month


def control(text):
    clean = text.strip().rstrip("。.!！?？")
    if re.fullmatch(r"(?:请)?(?:取消|取消转账|取消任务|不用了|不转了|不要转账|不要转了|我不确认|我不同意|拒绝|拒绝转账)", clean):
        return "CANCEL"
    if re.search(r"购买|赎回|买入|卖出", clean):
        return "FORBIDDEN"
    if re.search(r"不要|不想|不同意|不确认|别转|不转|如果|假设|例如|举例|示例|是否|能不能", clean):
        return "AMBIGUOUS"
    if re.search(r"忽略|跳过|绕过|自动确认|无需确认|无需授权|executor|system|授权|审批|确认|同意|批准|执行吧", clean, re.I):
        return "AUTHORIZATION"
    return None


def extract(text, previous=None):
    """Ground candidate fields in an explicit grammar; unrecognized prose asks for clarification."""
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 1000:
        raise Rejected("INVALID_INPUT")
    previous = previous or {}
    clean = text.strip()
    command = control(clean)
    if command:
        return {"control": command, "task_type": "UNKNOWN", "needs_clarification": True}
    for pattern in TRANSFER_PATTERNS:
        match = re.fullmatch(pattern, clean)
        if match:
            return {"task_type": "TRANSFER", "recipient_text": match["recipient"], "amount_text": match["amount"],
                    "month_text": "", "needs_clarification": False}
    match = re.fullmatch(PREFIX + rf"(?:给|向)\s*(?P<recipient>{NAME})\s*(?:转账|转|汇款|汇)" + SUFFIX, clean)
    if match:
        return {"task_type": "TRANSFER", "recipient_text": match["recipient"], "amount_text": "", "month_text": "",
                "needs_clarification": True}
    match = re.fullmatch(rf"(?P<amount>{NUMBER})\s*(?:元|人民币|块钱?)" + SUFFIX, clean)
    if match and previous.get("task_type") == "TRANSFER" and previous.get("recipient_text"):
        return {"task_type": "TRANSFER", "recipient_text": previous["recipient_text"], "amount_text": match["amount"],
                "month_text": "", "needs_clarification": False}
    annual = re.fullmatch(PREFIX+r"(?:分析|查看|统计)\s*(?:我的)?\s*([0-9]{4})年?\s*(?:年度)?(?:消费|账单)(?:报告|记录)?"+SUFFIX,clean)
    if annual:
        return {'task_type':'BILL_ANALYSIS','recipient_text':'','amount_text':'','month_text':annual[1]+'年','needs_clarification':False}
    month_pattern = r"(?:[0-9]{4}-[0-9]{2}|[0-9]{4}年\s*[0-9]{1,2}月|[0-9]{4}年|本月|这个月|上月|上个月)"
    match = re.fullmatch(PREFIX + rf"(?:分析|查看|看看|看一下|统计|总结)\s*(?:我的)?\s*(?P<month>{month_pattern})?\s*(?:的)?\s*(?:消费|账单)(?:记录)?" + SUFFIX, clean)
    if match:
        return {"task_type": "BILL_ANALYSIS", "recipient_text": "", "amount_text": "", "month_text": match["month"] or "",
                "needs_clarification": not bool(match["month"])}
    if re.fullmatch(month_pattern + SUFFIX, clean) and previous.get("task_type") == "BILL_ANALYSIS":
        return {"task_type": "BILL_ANALYSIS", "recipient_text": "", "amount_text": "",
                "month_text": clean.rstrip("。.!！?？"), "needs_clarification": False}
    if re.fullmatch(PREFIX + r"(?:按我的风险等级|根据我的风险等级)?(?:推荐一下|推荐|挑选)\s*(?:一些|几个)?\s*(?:适合我(?:的)?|适合我的风险等级的)?\s*理财(?:产品)?" + SUFFIX, clean):
        return {"task_type": "WEALTH_RECOMMENDATION", "recipient_text": "", "amount_text": "", "month_text": "",
                "needs_clarification": False}
    return {"task_type": "UNKNOWN", "recipient_text": "", "amount_text": "", "month_text": "", "needs_clarification": True}


def normalize_fields(fields):
    kind = fields["task_type"]
    if kind == "TRANSFER":
        if not fields.get("recipient_text") or not fields.get("amount_text"):
            raise Rejected("INTENT_INCOMPLETE")
        return {"task_type": kind, "recipient_name": fields["recipient_text"], "amount": amount_cents(fields["amount_text"]),
                "currency": "CNY", "needs_clarification": False}
    if kind == "BILL_ANALYSIS":
        if not fields.get("month_text"):
            raise Rejected("INTENT_INCOMPLETE")
        if re.fullmatch(r'[0-9]{4}年',fields['month_text']):
            year=int(fields['month_text'][:4])
            if not 1900<=year<=9998: raise Rejected('INVALID_YEAR')
            return {'task_type':kind,'year':year,'needs_clarification':False}
        return {"task_type": kind, "month": month_value(fields["month_text"]), "needs_clarification": False}
    if kind == "WEALTH_RECOMMENDATION":
        return {"task_type": kind, "needs_clarification": False}
    raise Rejected("INTENT_INCOMPLETE")
