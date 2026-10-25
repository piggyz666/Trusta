"""Grounded answers to "how does the bank work" questions.

The model may only pick a *topic*; every sentence and number below is produced from the live configuration
and rule tables (never from model text), and each answer cites where the rule lives ("依据"). Adding a topic
means adding a builder here -- the model cannot introduce a new fact.
"""
from backend.chat.amounts import format_cents
from backend.security.banking_controls import BankingControls
from backend.security.permission_matrix import MATRIX, TIERS

TOPICS = ["PERMISSION_TIERS", "TRANSFER_LIMITS", "VERIFICATION", "CONFIRMATION_EXPIRY", "UNDO_POLICY",
          "WISH_FUND", "CARD_LOSS", "INVESTMENT_SUITABILITY", "DATA_PRIVACY", "ANTI_FRAUD", "HUMAN_SUPPORT", "AUTOMATION", "PROTECTION",
          "NO_STOCK_PICKS", "MARKET_QUOTES", "UNKNOWN_CHARGE", "THIRD_PARTY_MEMBERSHIP", "WEALTH_PLAN"]

TITLES = {"PERMISSION_TIERS": "操作权限分级", "TRANSFER_LIMITS": "转账限额与风控", "VERIFICATION": "验证方式",
          "CONFIRMATION_EXPIRY": "确认卡有效期", "UNDO_POLICY": "哪些操作可以撤销", "WISH_FUND": "心愿金是什么",
          "CARD_LOSS": "冻结和挂失的区别", "INVESTMENT_SUITABILITY": "理财适当性规则", "DATA_PRIVACY": "AI 能看到哪些数据",
          "ANTI_FRAUD": "防诈骗提示", "HUMAN_SUPPORT": "人工服务",
          "AUTOMATION": "联动规则怎么用", "PROTECTION": "转账保护（冷静期）",
          "NO_STOCK_PICKS": "为什么不推荐个股", "MARKET_QUOTES": "黄金与汇率行情", "UNKNOWN_CHARGE": "不认识的周期扣费怎么办",
          "THIRD_PARTY_MEMBERSHIP": "开通第三方会员", "WEALTH_PLAN": "按你的消费做的理财配置"}

REVERSIBLE = ["冻结 ↔ 解冻", "挂失 ↔ 解除挂失", "限额调整（改回原值）", "交易开关（恢复原设置）", "心愿金锁定（释放）", "理财申购 ↔ 赎回"]


def _src(label, ref, kind="RULE"):
    return {"label": label, "kind": kind, "ref": ref}


def answer(topic, config, irreversible=(), market=None, plan=None):
    if topic not in TOPICS:
        raise KeyError(topic)
    if topic == "WEALTH_PLAN":
        if not plan:
            return "需要先完成风险测评，我才能按你的情况做配置。可以说“做一下风险测评”。", []
        lines = []
        for b in plan["buckets"]:
            items = "、".join(f"{i['name']}（{i['issuer']}，R{i['risk_level']}）{format_cents(i['amount_minor'])}" for i in b["items"])
            lines.append(f"{b['name']} {format_cents(b['amount_minor'])}：{items}")
        text = ("；".join(plan["reasons"]) + "。建议这样放：" + "；".join(lines) + "。"
                + plan["note"] + "想买哪一款，打开“理财”页面点“去申购”，或直接对我说。")
        return text, [_src("配置规则", "backend/chat/pages.py#plan"), _src("消费数据", "backend/agents/finance.py", "DATA")]
    if topic == "NO_STOCK_PICKS":
        text = ("我不推荐具体股票：荐股属于证券投资咨询，需要专门资质，银行助手也无法替你判断个股涨跌。"
                "我能做的是按你的风险测评结果，从本行在售的理财、黄金积存、基金类产品里挑选合适的，并说明每款的期限、流动性和风险。"
                "想看的话可以说“推荐适合我的理财”，或打开“理财”页面查看黄金和汇率行情。")
        return text, [_src("适当性规则", "backend/agents/wealth.py"), _src("理财页产品货架", "backend/chat/pages.py")]
    if topic == "THIRD_PARTY_MEMBERSHIP":
        text = ("音乐、视频、网盘这类会员需要在对应的 App 里开通，银行助手不能替你开通或登录第三方账号。"
                "开通后如果是每月自动扣费，扣满 3 次我会在“账单与订阅”里识别出来，你确认后每次扣费前我都会提醒；"
                "以后不想续了，银行登记过的订阅可以直接对我说“取消××订阅”。")
        return text, [_src("周期扣费识别", "backend/agents/finance.py#recurring")]
    if topic == "UNKNOWN_CHARGE":
        text = ("这类扣费没有登记为订阅或代扣，银行不能直接替你“取消”，但可以先止损：① 说“冻结我的卡”，冻结后新的扣款会被拒绝，找到原因后可自己解冻；"
                "② 说“转人工”，由客服核实商户并发起争议或拒付流程；③ 检查常用 App 里的自动续费，从源头关掉。"
                "如果确认是自己开通的，可以在“账单与订阅”里点“是我的订阅”，以后扣费前我会提醒你。")
        return text, [_src("周期扣费识别", "backend/agents/finance.py#recurring"), _src("卡片冻结", "backend/banking/actions.py")]
    if topic == "MARKET_QUOTES":
        view = market.view() if market else None
        if not view:
            return "暂时取不到行情，请稍后在“理财”页面刷新。", []
        parts = [f"{q['name']} {q['price']:,.2f} {q['unit']}" if q["symbol"] == "XAU" else f"{q['name']} {q['price']:.4f} {q['unit']}"
                 for q in view["quotes"]]
        times = sorted({(q["source"], q["as_of"][:16].replace("T", " ")) for q in view["quotes"]})
        text = ("、".join(parts) + "。" + "；".join(f"来源：{src}，数据时间 {at}" for src, at in times) + "。"
                + ("（目前未联网，以上是内置快照，不是实时价格。）" if not view["live"] else "")
                + "行情仅供参考，不是本行买卖报价，也不构成投资建议。")
        return text, [_src("行情数据源", "backend/market.py", "DATA")]
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
    if topic == "AUTOMATION":
        text = ("可以用一句话设置“当……就……”的规则：触发条件有到点、入账、余额低于、某类消费超预算、出现大额消费；"
                "动作有提醒、转账、存心愿金、申购理财、冻结卡、关闭交易开关、转人工。规则要你确认后才生效；触发时涉及资金的操作仍会逐笔请你确认，"
                "只有冻结卡、关闭交易开关这类只收紧风险的操作可以在你预先同意后自动执行。")
        return text, [_src("联动规则引擎", "backend/automation/engine.py"), _src("权限分级矩阵", "backend/security/permission_matrix.py")]
    if topic == "PROTECTION":
        text = ("开启转账保护后，保护期内对外转账、新增收款人、心愿金下单和理财申购都会被暂停，银行在审批和执行前各拦截一次。"
                "保护期内只能由银行安全人员核实身份后提前解除，任何人让你“先解除再转账”都是诈骗。")
        return text, [_src("反诈冷静期", "backend/security/banking_controls.py#protection")]
    text = "随时说“转人工”即可转接人工客服，人工接入期间 AI 不会执行任何操作；说“回到智能助手”可以切回。"
    return text, [_src("人工接管", "backend/chat/service.py#_open_handoff")]


# navigate: deep links into the APP, a fixed list. The model picks a page id; the link is built here.
PAGES = {"transfer": "转账", "payees": "收款人管理", "bills": "账单与订阅", "wealth": "理财", "cards": "银行卡",
         "subscriptions": "账单与订阅", "wish_funds": "心愿金", "security": "安全设置", "audit": "操作记录"}


def navigate_card(page, card_id):
    return {"type": "navigate", "card_id": card_id, "page": page, "title": PAGES[page],
            "deep_link": f"trusta://app/{page}", "note": "只打开页面，不会发起任何交易。"}
