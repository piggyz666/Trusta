"""Grounded answers to "how does the bank work" questions.

The model may only pick a *topic*; every sentence and number below is produced from the live configuration
and rule tables (never from model text), and each answer cites where the rule lives ("依据"). Adding a topic
means adding a builder here -- the model cannot introduce a new fact.
"""
from backend.chat.amounts import format_cents
from backend.security.banking_controls import BankingControls
from backend.security.permission_matrix import MATRIX, TIERS

TOPICS = ["PERMISSION_TIERS", "TRANSFER_LIMITS", "VERIFICATION", "CONFIRMATION_EXPIRY", "UNDO_POLICY",
          "WISH_FUND", "CARD_LOSS", "INVESTMENT_SUITABILITY", "DATA_PRIVACY", "ANTI_FRAUD", "HUMAN_SUPPORT"]

TITLES = {"PERMISSION_TIERS": "操作权限分级", "TRANSFER_LIMITS": "转账限额与风控", "VERIFICATION": "验证方式",
          "CONFIRMATION_EXPIRY": "确认卡有效期", "UNDO_POLICY": "哪些操作可以撤销", "WISH_FUND": "心愿金是什么",
          "CARD_LOSS": "冻结和挂失的区别", "INVESTMENT_SUITABILITY": "理财适当性规则", "DATA_PRIVACY": "AI 能看到哪些数据",
          "ANTI_FRAUD": "防诈骗提示", "HUMAN_SUPPORT": "人工服务"}

REVERSIBLE = ["冻结 ↔ 解冻", "挂失 ↔ 解除挂失", "限额调整（改回原值）", "交易开关（恢复原设置）", "心愿金锁定（释放）", "理财申购 ↔ 赎回"]


def _src(label, ref, kind="RULE"):
    return {"label": label, "kind": kind, "ref": ref}


def answer(topic, config, irreversible=()):
    if topic not in TOPICS:
        raise KeyError(topic)
    minutes = max(1, config.action_ttl // 60)
    if topic == "PERMISSION_TIERS":
        count = {t: sum(1 for _, tier, _ in MATRIX if tier == t) for t in TIERS}
        text = "；".join(f"{v['label']}（{v['strategy']}）{count[k]} 类操作" for k, v in TIERS.items())
        text = (f"操作按风险分三级：{text}。查询类直接执行；资金和卡片操作需要你在确认卡上逐笔确认；"
                "大额、超当日累计、新收款人等情形再加验证码。分级由银行规则引擎决定，AI 无法调整。")
        return text, [_src("权限分级矩阵", "backend/security/permission_matrix.py"), _src("策略引擎", "backend/policy.py")]
    if topic == "TRANSFER_LIMITS":
        text = (f"人民币转账当日累计超过 {format_cents(config.daily_transfer_limit)} 需要强验证；"
                f"{BankingControls.VELOCITY_WINDOW // 60} 分钟内已有 {BankingControls.VELOCITY_LIMIT} 笔转账时，下一笔需要强验证；"
                f"24 小时内新添加的收款人单笔 ≥ {format_cents(BankingControls.NEW_PAYEE_LIMIT)} 需要强验证；外币转账一律强验证。")
        return text, [_src("银行风控规则", "backend/security/banking_controls.py#risk"),
                      _src("当日限额配置", "TRUSTA_DAILY_TRANSFER_CENTS", "CONFIG")]
    if topic == "VERIFICATION":
        text = (f"强验证可以用认证器动态码（TOTP）或短信验证码。短信验证码 {BankingControls.SMS_TTL // 60} 分钟内有效、"
                f"最多尝试 {BankingControls.SMS_MAX_ATTEMPTS} 次，且只对这一笔操作有效；连续验证失败 "
                f"{config.authentication_failure_limit} 次会安全锁定。验证码只在确认卡里输入，不要在聊天里发送。")
        return text, [_src("强验证与短信验证码", "backend/security/banking_controls.py#sms"),
                      _src("失败锁定配置", "TRUSTA_AUTH_FAILURE_LIMIT", "CONFIG")]
    if topic == "CONFIRMATION_EXPIRY":
        text = (f"确认卡和它绑定的授权 {minutes} 分钟内有效，只对卡上显示的那一笔（金额、对象、账户）有效。"
                "过期或信息有变化需要重新生成；执行前银行还会核对余额等前置条件，不一致会拒绝执行。")
        return text, [_src("授权有效期配置", "backend/config.py#action_ttl", "CONFIG"), _src("执行合同与校验", "backend/execution/gateway.py")]
    if topic == "UNDO_POLICY":
        text = "可以生成反向操作（仍需你确认）的有：" + "、".join(REVERSIBLE) + "。"
        if irreversible:
            text += "不能直接撤销的有：" + "、".join(irreversible) + "。"
        return text, [_src("回退规则", "backend/chat/service.py#_undo_steps")]
    if topic == "WISH_FUND":
        text = ("心愿金是从活期划出、单独锁定的子账户，用来为某个目标（如纪念日、旅行）存钱。锁定的钱不能被普通转账花掉；"
                "可以设置到期日和自动下单计划，每一步到时仍需你确认，随时可以释放回活期。")
        return text, [_src("心愿金账户", "backend/banking/extended_bank.py#wish_funds")]
    if topic == "CARD_LOSS":
        text = ("冻结是临时止付，找到卡后可以自己解冻；挂失用于卡片丢失或被盗，挂失后卡片停止使用，"
                "确认找回可以解除挂失，否则需要补办新卡。不确定时建议先冻结。")
        return text, [_src("卡片操作", "backend/banking/actions.py")]
    if topic == "INVESTMENT_SUITABILITY":
        text = ("只会向你推荐风险等级不高于你测评结果的产品；没有有效测评时需要先完成风险测评。"
                "产品介绍不预测收益，历史业绩不代表未来表现，理财非存款，投资需谨慎。")
        return text, [_src("适当性规则", "backend/agents/wealth.py"), _src("风险测评", "backend/chat/risk.py")]
    if topic == "DATA_PRIVACY":
        text = ("AI 理解模块只看到你的原话、日期和资源名称（如收款人姓名、产品名），看不到余额、流水和卡号；"
                "长串数字（卡号、证件号）发送前会被遮盖。所有数字都由银行系统查询后填入模板，不由 AI 生成。")
        return text, [_src("模型边界", "backend/chat/llm.py"), _src("输出校验", "backend/chat/validator.py")]
    if topic == "ANTI_FRAUD":
        text = ("银行不会要求你把钱转到“安全账户”，也不会索要验证码或密码；刷单返利、先交费再放款、冒充客服或公检法都是常见诈骗。"
                "遇到这类要求请停止转账，可以说“转人工”联系我们核实。")
        return text, [_src("反诈识别规则", "backend/chat/guard.py")]
    text = "随时说“转人工”即可转接人工客服，人工接入期间 AI 不会执行任何操作；说“回到智能助手”可以切回。"
    return text, [_src("人工接管", "backend/chat/service.py#_open_handoff")]


# navigate: deep links into the APP, a fixed list. The model picks a page id; the link is built here.
PAGES = {"transfer": "转账", "payees": "收款人管理", "bills": "账单分析", "wealth": "理财", "cards": "银行卡",
         "subscriptions": "订阅与代扣", "wish_funds": "心愿金", "security": "安全设置", "audit": "操作记录"}


def navigate_card(page, card_id):
    return {"type": "navigate", "card_id": card_id, "page": page, "title": PAGES[page],
            "deep_link": f"trusta://app/{page}", "note": "只打开页面，不会发起任何交易。"}
