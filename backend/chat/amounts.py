"""Deterministic money / count parsing for span grounding.

The LLM reports both a normalized value and the exact span of user text it was
derived from. This module independently parses the span; the call is accepted
only when both agree. It never guesses: anything it cannot parse returns None.
"""
from decimal import Decimal, InvalidOperation
import re

DIGITS = {"零": 0, "〇": 0, "一": 1, "幺": 1, "壹": 1, "二": 2, "两": 2, "贰": 2, "俩": 2, "三": 3, "叁": 3,
          "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9}
UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
BIG = {"万": 10000, "萬": 10000, "w": 10000, "W": 10000, "k": 1000, "K": 1000, "亿": 100000000}
CURRENCY_WORDS = ("人民币", "元整", "块钱", "元钱", "rmb", "RMB", "¥", "￥", "元", "块", "圆")
MAX_CENTS = 10**12


def _chinese_int(text):
    """Parse a pure Chinese numeral below 10,000 (e.g. 三千五百, 二十, 十五, 三千五)."""
    if not text:
        return None
    total, digit, last_unit, zero = 0, None, None, False
    for ch in text:
        if ch in "零〇" and last_unit is not None and digit is None:
            zero = True
            continue
        if ch in DIGITS:
            if digit is not None:  # "一二" style digit strings are not amounts here
                return None
            digit = DIGITS[ch]
        elif ch in UNITS:
            unit = UNITS[ch]
            if last_unit is not None and unit >= last_unit:
                return None
            total += (1 if digit is None else digit) * unit
            digit, last_unit = None, unit
        else:
            return None
    if digit is not None:
        # Colloquial abbreviation: 三千五 = 3500, 两万三 handled by caller, 一百二 = 120
        total += digit * (last_unit // 10 if last_unit and last_unit >= 10 and not zero else 1)
    return total


def _number(text):
    """Parse an arabic or Chinese number possibly with 万/千/k multipliers. Returns Decimal yuan."""
    text = text.strip().replace(",", "").replace("，", "")
    if not text:
        return None
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return Decimal(text)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([万萬wWkK千百亿])(.*)", text)
    if match:
        head = Decimal(match[1]) * (BIG.get(match[2]) or UNITS[match[2]])
        rest = match[3].strip()
        if not rest:
            return head
        # 1万5 = 15000 ; 2k5 is not standard, reject
        if match[2] in "万萬" and re.fullmatch(r"[0-9一二两三四五六七八九]", rest):
            digit = int(rest) if rest.isdigit() else DIGITS[rest]
            return head + digit * 1000
        tail = _number(rest)
        return None if tail is None else head + tail
    for big_char, big_value in (("亿", 100000000), ("万", 10000), ("萬", 10000)):
        if big_char in text:
            head_text, _, tail_text = text.partition(big_char)
            head = _chinese_int(head_text) if head_text else 1
            if head is None:
                return None
            value = Decimal(head) * big_value
            if not tail_text:
                return value
            if len(tail_text) == 1 and tail_text in DIGITS:  # 两万三 = 23000
                return value + DIGITS[tail_text] * big_value // 10
            tail = _chinese_int(tail_text.lstrip("零"))
            return None if tail is None else value + tail
    value = _chinese_int(text)
    return None if value is None else Decimal(value)


def parse_amount_span(span):
    """Return integer cents for a money span such as '两千', '2000元', '1.5万', '三千五百块', '200块5毛'."""
    if not isinstance(span, str):
        return None
    text = span.strip().replace(" ", "")
    if not 0 < len(text) <= 32:
        return None
    for prefix in ("¥", "￥"):
        text = text.removeprefix(prefix)
    jiao = fen = 0
    # Trailing 毛/角/分 parts: 200块5毛, 3元2角5分
    sub = re.fullmatch(r"(.+?)(?:块|元)(?:([0-9一二两三四五六七八九])(?:毛|角))?(?:([0-9一二两三四五六七八九])分)?(?:钱)?", text)
    if sub and (sub[2] or sub[3]):
        text = sub[1]
        if sub[2]:
            jiao = int(sub[2]) if sub[2].isdigit() else DIGITS[sub[2]]
        if sub[3]:
            fen = int(sub[3]) if sub[3].isdigit() else DIGITS[sub[3]]
    else:
        for word in CURRENCY_WORDS:
            if text.endswith(word):
                text = text[: -len(word)]
                break
        # "块" alone after a bare number: "200块" handled above; "两千块" too.
    if not text:
        return None
    try:
        value = _number(text)
    except (InvalidOperation, KeyError, ValueError):
        return None
    if value is None:
        return None
    cents = value * 100 + jiao * 10 + fen
    if cents != cents.to_integral_value() or not 0 < cents <= MAX_CENTS:
        return None
    return int(cents)


def parse_amount_value(value):
    """Normalized model value '2000.00' -> 200000 cents; at most 2 decimals."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", value.strip()):
        return None
    cents = int(Decimal(value.strip()) * 100)
    return cents if 0 < cents <= MAX_CENTS else None


def format_cents(cents, currency="CNY"):
    symbol = {"CNY": "¥", "GBP": "£", "EUR": "€"}.get(currency, currency + " ")
    sign = "-" if cents < 0 else ""
    return f"{sign}{symbol}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"
