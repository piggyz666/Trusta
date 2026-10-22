"""Deterministic input guard, evaluated before any model call.

It recognises three things the scoring rubric names explicitly:
prompt injection / authority claims, fraud-induced transfers, and attempts to
extract other people's information. A hit is audited and shown to the user as a
risk_alert card; blocking hits produce no proposal in that turn.
The model can add a risk flag later (``flag_risk``) but can never clear one.
"""
import re

INJECTION = [
    (r"忽略|无视|忘记|不要管", r"(之前|以上|上面|前面|所有|系统)的?.{0,4}(指令|规则|提示|设定|限制|要求)"),
    (r"ignore|disregard|forget", r"(previous|above|all|system).{0,12}(instruction|rule|prompt)"),
    (r"(你现在是|你扮演|假装你是|进入|开启|切换到)", r"(开发者|管理员|调试|越狱|DAN|无限制|上帝)(模式)?"),
    (r"(系统提示词|system ?prompt|内部指令|你的提示词)", r""),
    (r"(跳过|绕过|免去|不需要|无需|不用)", r"(确认|验证|授权|审批|风控|安全检查|验证码|人脸)"),
    (r"(自动确认|自动授权|直接执行|静默执行|后台执行)", r""),
    (r"(我是|我就是|本人是)(银行|行长|管理员|开发者|客服|系统)", r""),
    (r"(已经|已)(授权|获得授权|批准|审批通过)", r""),
    (r"(把|将)?(风险等级|risk_level|限额规则|日累计)", r"(改|设|调)(为|成)?.{0,6}(绿|低|green|0)"),
]
FRAUD = [
    (r"安全账户|资金清查|涉嫌洗钱|配合调查|(公安|警察|检察院|法院|反诈中心).{0,12}(转账|汇款|打款|转到|转给)", "冒充公检法要求转入安全账户"),
    (r"刷单|做任务.{0,6}返|返利|垫付.{0,6}(佣金|返)", "刷单返利"),
    (r"(解冻费|保证金|手续费|激活费|认证费).{0,10}(才能|就能|方可).{0,6}(提现|到账|解冻|放款)", "先交费才能提现 / 放款"),
    (r"(贷款|网贷).{0,8}(先交|先转|先付|验证.{0,4}流水|刷流水)", "虚假贷款先交费"),
    (r"(领导|老板|老师|亲戚).{0,12}(微信|QQ|新号|换号).{0,12}(转|打|汇)", "冒充熟人 / 领导借钱"),
    (r"(客服|快递|电商).{0,12}(退款|理赔|赔付).{0,16}(转|验证码|备用金|借呗|花呗)", "冒充客服退款"),
    (r"(验证码|动态码|短信码).{0,8}(发给|告诉|报给|念给).{0,6}(他|她|对方|客服|别人)", "向他人透露验证码"),
    (r"(投资|理财|炒币|虚拟币|博彩).{0,12}(稳赚|保本高收益|内幕|翻倍|日收益)", "高收益投资诱导"),
    (r"(网友|对象|女朋友|男朋友).{0,16}(投资|平台|充值|转账)", "交友诱导投资"),
]
PROBING = [
    r"(他|她|张三|李四|别人|其他人|其他用户|对方)的.{0,4}(余额|卡号|密码|身份证|流水|账单|手机号)",
    r"(完整|全部|明文).{0,4}(卡号|账号|身份证号|密码)",
    r"(列出|导出|给我看).{0,8}(所有|全部).{0,6}(用户|客户|账户)",
    r"(我的)?(密码|支付密码|取款密码)(是多少|是什么|告诉我)",
    r"(数据库|sql|SQL|select|SELECT|drop table|DROP TABLE)",
]
# 情绪施压 / 被操控的转账: no single phrase is fraud, but pressure + secrecy + "everything" + a stranger's account is
# the classic pattern of a victim being coached on the phone. Scored, so "快帮我给张三转500" alone never trips it.
PRESSURE = [
    (r"(立刻|马上|立即|赶紧|赶快|快点|十万火急|来不及)", 1),
    (r"(别问|不要问|别管|没时间解释|来不及解释|不要告诉|别告诉|保密|不能告诉)", 2),
    (r"(全部|所有|全都|一次性).{0,6}(赎回|转出|取出|转走|打过去|钱)", 1),
    (r"(陌生|新的?|对方给的|他给的|这个)(账户|账号|卡号|银行卡)|卡号[0-9]{4,}|[0-9]{12,19}", 1),
    (r"(不然|否则|要不然).{0,10}(坐牢|冻结|逮捕|出事|来不及|完了|违法)", 2),
]
PRESSURE_THRESHOLD = 3
MONEY_VERB = r"转|汇|打款|打钱|赎回|付款"


def pressure(text):
    """Score of pressure markers in a money request; returns (score, evidence)."""
    if not re.search(MONEY_VERB, text):
        return 0, ""
    score, evidence = 0, []
    for pattern, weight in PRESSURE:
        found = re.search(pattern, text)
        if found:
            score += weight
            evidence.append(found.group(0))
    return score, "、".join(evidence)


MESSAGES = {
    "PROMPT_INJECTION": "这条请求试图改变我的安全规则或冒用授权，我不能执行。所有资金操作都需要你本人在确认卡上逐笔确认，大额还需要验证。",
    "FRAUD_SUSPECTED": "注意：你的描述符合“{pattern}”的常见诈骗特征。银行、公安和正规平台都不会要求你这样转账。"
                       "本次不会生成转账。如果确实需要办理，请先与对方通过官方渠道核实；如有疑问可拨打 96110 反诈专线。",
    "INFO_PROBING": "出于隐私和安全，我不能提供他人的账户信息、完整卡号或密码类信息。你可以查询自己账户的打码信息。",
}


def _hit(pairs, text):
    for head, tail in pairs:
        found = re.search(head, text, re.I)
        if found and (not tail or re.search(tail, text[found.start():], re.I)):
            return found.group(0)
    return None


def inspect(text):
    """Return None or {category, severity, blocking, message, evidence}."""
    if not isinstance(text, str):
        return None
    compact = re.sub(r"\s+", "", text)
    evidence = _hit(INJECTION, compact)
    if evidence:
        return {"category": "PROMPT_INJECTION", "severity": "HIGH", "blocking": True,
                "message": MESSAGES["PROMPT_INJECTION"], "evidence": evidence}
    for pattern, label in FRAUD:
        found = re.search(pattern, compact, re.I)
        if found:
            return {"category": "FRAUD_SUSPECTED", "severity": "HIGH", "blocking": True,
                    "message": MESSAGES["FRAUD_SUSPECTED"].format(pattern=label), "evidence": found.group(0)}
    score, evidence = pressure(compact)
    if score >= PRESSURE_THRESHOLD:
        return {"category": "FRAUD_SUSPECTED", "severity": "HIGH", "blocking": True,
                "message": MESSAGES["FRAUD_SUSPECTED"].format(pattern="催促、要求保密并转出大额资金"), "evidence": evidence}
    for pattern in PROBING:
        found = re.search(pattern, compact, re.I)
        if found:
            return {"category": "INFO_PROBING", "severity": "MEDIUM", "blocking": True,
                    "message": MESSAGES["INFO_PROBING"], "evidence": found.group(0)}
    return None


def mask_phone(phone):
    return phone[:3] + "****" + phone[-4:] if isinstance(phone, str) and len(phone) >= 7 else "****"


def mask_id(value):
    return "****" + value[-4:] if isinstance(value, str) and len(value) > 4 else value
