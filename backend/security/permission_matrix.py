"""Declarative permission tiers (赛题“权限分级机制”).

This table is documentation *and* a test oracle: tests/test_permission_matrix.py asserts that the
runtime Policy engine produces exactly these tiers. Policy remains the single enforcement point; this
module never authorises anything.

GREEN  自动执行   read-only, executed once intent is understood, no confirmation
YELLOW 用户确认   a confirmation card bound to the action hash
RED    强验证     confirmation + TOTP or SMS one-time code bound to that action
"""

TIERS = {
    "GREEN": {"label": "绿色", "strategy": "自动执行", "verification": []},
    "YELLOW": {"label": "黄色", "strategy": "用户确认", "verification": ["CONFIRM"]},
    "RED": {"label": "红色", "strategy": "强验证", "verification": ["CONFIRM", "TOTP_OR_SMS"]},
}

# operation -> (tier, condition text). Conditional rows list the escalation rule.
MATRIX = [
    # ---- GREEN: reads ----
    ("BALANCE", "GREEN", "余额查询"),
    ("SPENDING_QUERY", "GREEN", "消费查询 / 账单分析 / 异常交易识别"),
    ("BILL_ANALYSIS", "GREEN", "月度 / 年度账单报告"),
    ("WEALTH_RECOMMENDATION", "GREEN", "理财推荐"),
    ("PRODUCT_COMPARE", "GREEN", "产品对比"),
    ("PORTFOLIO", "GREEN", "理财持仓查询"),
    ("CARD_LIST", "GREEN", "银行卡查询"),
    ("RENEWAL_REMINDERS", "GREEN", "扣费前的续费提醒（系统在回复后附带一句，不展示订阅清单）"),
    ("WISH_FUND_LIST", "GREEN", "心愿金查询"),
    ("RISK_QUESTIONNAIRE", "GREEN", "开始风险测评问卷（提交结果仍需确认）"),
    ("INCOME_LIST", "GREEN", "入账明细（他人填写的付款人 / 备注按不可信数据隔离显示）"),
    ("AUTOMATION_LIST", "GREEN", "查看联动规则"),
    # ---- YELLOW ----
    ("TRANSFER", "YELLOW", "人民币转账，当日累计 ≤ ¥1,000 且单笔 < 大额阈值"),
    ("SCHEDULED_TRANSFER", "YELLOW", "定时转账，规则同上；到期前授权失效会重新确认"),
    ("SPLIT_REQUEST", "GREEN", "AA 分摊计算与收款请求（不发生任何资金变动，不替他人付款）"),
    ("SUBSCRIPTION_LIST", "YELLOW", "订阅 / 代扣查询（含续费清单与可取消订阅识别）：先展示查询内容并确认，确认后 10 分钟内不重复确认"),
    ("SUBSCRIPTION_CANCEL", "YELLOW", "取消订阅"),
    ("DIRECT_DEBIT_DISABLE", "YELLOW", "关闭代扣"),
    ("CARD_APPLY", "YELLOW", "虚拟卡申请"),
    ("CARD_FREEZE", "YELLOW", "冻结卡片（收紧风险）"),
    ("CARD_LIMIT_CHANGE", "YELLOW", "调低限额"),
    ("CARD_TRANSACTION_CONTROL", "YELLOW", "关闭交易开关"),
    ("FUND_LOCK", "YELLOW", "锁定心愿金，金额 < 大额阈值"),
    ("FUND_RELEASE", "YELLOW", "释放心愿金回本人活期"),
    ("WISH_ORDER", "YELLOW", "心愿金下单，金额 < 大额阈值，价格取自商户目录"),
    ("RISK_PROFILE_UPDATE", "YELLOW", "根据问卷更新风险等级"),
    ("AUTOMATION_RULE", "YELLOW", "创建联动规则（规则本身不动钱；触发时资金操作仍逐笔按本表分级确认）"),
    ("AUTOMATION_AUTO_TIGHTEN", "YELLOW", "在规则上预先授权“只收紧”的卡片操作（冻结 / 关闭交易开关），触发时自动执行并通知"),
    ("ACCOUNT_PROTECTION_ON", "YELLOW", "开启反诈冷静期（只收紧，一键开启）"),
    # ---- RED ----
    ("TRANSFER", "RED", "当日累计 > ¥1,000、外币、单笔 ≥ 大额阈值、10 分钟内第 4 笔起，或向 24 小时内新加的收款人转 ≥ ¥500"),
    ("ADD_PAYEE", "RED", "添加新收款人"),
    ("CARD_UNFREEZE", "RED", "解冻卡片"),
    ("CARD_REPORT_LOST", "RED", "卡片挂失"),
    ("CARD_CANCEL_LOST", "RED", "解除挂失"),
    ("CARD_PIN_RESET", "RED", "密码修改（重置链接，密码不经过 AI）"),
    ("CARD_LIMIT_CHANGE", "RED", "调高限额"),
    ("CARD_TRANSACTION_CONTROL", "RED", "开启任一交易开关"),
    ("INVESTMENT_PURCHASE", "RED", "理财申购"),
    ("INVESTMENT_REDEEM", "RED", "理财赎回"),
    ("FUND_LOCK", "RED", "金额 ≥ 大额阈值"),
    ("WISH_ORDER", "RED", "金额 ≥ 大额阈值"),
]

# Escalations that apply to every tier (Policy / safety controls), listed for the self-assessment report.
CROSS_CUTTING = [
    "账户安全锁定（连续认证 / 执行失败、权威状态不一致）时，所有写操作被阻断",
    "会话内 3 次注入尝试 → 会话锁定 10 分钟，待确认操作全部取消",
    "短时间频繁发起转账 / 向新收款人大额转账 → 升级为红色并提示（转账频率风控）",
    "疑似诈骗话术 → 本轮不生成任何转账，并取消会话内待确认操作",
    "模型输出中出现授权类字段（approve、risk_level、otp …）→ 整轮作废并按注入处理",
    "IM 渠道中红色操作无法完成，必须回到 APP 验证",
    "反诈冷静期内：转账 / 新增收款人 / 心愿金下单 / 理财申购在策略层与执行前各拦截一次；只能由银行安全人员核实后提前解除",
    "一轮中有资金变动时，所有写操作串行：后一步在前一步执行后按最新账户状态生成，前一步被拒绝则后续自动取消",
    "部署能力注册表：当前配置不支持的功能不会出现在模型的工具列表中，校验层与银行网关再各拒绝一次",
]


def tier_of(policy_decision):
    """Map a Policy decision to a tier. BLOCK is not a tier: the action is refused."""
    return {"CONFIRM": "YELLOW", "STRONG_AUTH": "RED"}.get(policy_decision)


def public_matrix():
    rows = [{"operation": op, "tier": tier, **TIERS[tier], "condition": text} for op, tier, text in MATRIX]
    return {"tiers": TIERS, "rows": rows, "cross_cutting": CROSS_CUTTING,
            "enforced_by": "backend/policy.py + backend/security/banking_controls.py; verified by tests/test_permission_matrix.py"}
