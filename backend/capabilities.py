"""Deployment capability registry (OpenClaw-style “configure first, then features light up”).

The operator picks a language model, a bank adapter and channels (``.env`` / ``python -m scripts.setup``). Every feature
declares what it needs; the registry computes, from what is actually configured *and verified*:

  AVAILABLE    everything it needs is present
  DEGRADED     works with reduced quality (e.g. no model → rule grammar only)
  UNAVAILABLE  something essential is missing; the reason and the fix are given

Enforcement is fail-closed in three places: unavailable tools are removed from the model's tool list (the model never
sees them), the chat validator refuses them, and the bank gateway independently refuses any capability the adapter does
not declare. The current state is hashed into a *capability snapshot*; a change is written to the audit chain and every
request handed to the six-agent runtime carries the snapshot hash inside its signed provenance, so afterwards one can prove
which features were switched on when an action happened.
"""
import os
import time
from backend.domain import Rejected, canonical, digest
from backend.bank_sdk.discovery import is_external

# requirement id -> (label, how to enable)
REQUIREMENTS = {
    "llm.tools": ("支持工具调用的大模型", "在 .env 配置 DEEPSEEK_API_KEY（或兼容 OpenAI 接口的模型），并运行 python -m scripts.setup --probe 验证"),
    "bank.payments": ("银行接口：转账", "选择支持转账写入的银行适配器（本地沙箱银行，或开启 transfer_profile 的 Revolut 沙箱）"),
    "bank.payees": ("银行接口：收款人", "银行适配器需提供收款人查询"),
    "bank.payees.create": ("银行接口：新增收款人", "银行适配器需支持新增收款人"),
    "bank.transactions": ("银行接口：交易流水", "银行适配器需提供交易流水"),
    "bank.balance": ("银行接口：余额", "银行适配器需提供余额查询"),
    "bank.wealth": ("银行接口：理财申赎", "银行适配器需支持理财产品申购 / 赎回"),
    "bank.risk": ("银行接口：风险测评", "银行适配器需支持风险等级更新"),
    "bank.cards": ("银行接口：卡片管理", "银行适配器需支持卡片冻结 / 挂失 / 限额 / 交易开关"),
    "bank.card_controls": ("银行接口：交易开关", "银行适配器需支持卡片交易开关"),
    "bank.subscriptions": ("银行接口：订阅与代扣", "银行适配器需支持订阅 / 代扣查询与取消"),
    "bank.savings": ("银行接口：心愿金子账户", "银行适配器需支持储蓄目标（锁定 / 释放 / 支付）"),
    "bank.sandbox": ("内置沙箱银行", "定期存款、外币、基金份额、黄金、贷款与分期、信用账单是内置沙箱银行的扩展业务，尚未定义真实银行接口；"
                                     "接入真实银行时自动关闭，需要时可按 docs/bank-integration-sdk.md 为其补充标准接口"),
    "bank.feed": ("银行入账推送", "当前仅本地沙箱银行提供入账推送；真实银行需接入其 webhook"),
    "channel.feishu": ("飞书机器人", "运行 python -m scripts.setup 填写 FEISHU_APP_ID / FEISHU_APP_SECRET（默认长连接，无需公网），"
                                    "pip install lark-oapi，再用 python -m scripts.pair --feishu 绑定账户"),
    "security.strong_auth": ("强验证（TOTP / 短信）", "TRUSTA_SAFETY_PROFILE=competition，或设置 TRUSTA_STRONG_AUTH_SECRET"),
}
BANK_TOOLS = {
    "bank.payments": {"transfer.create"}, "bank.payees": {"payee.resolve"}, "bank.payees.create": {"payee.add"},
    "bank.transactions": {"transaction.list"}, "bank.balance": {"account.get_balance"},
    "bank.wealth": {"wealth.list_products", "investment.purchase", "investment.redeem", "portfolio.get"},
    "bank.risk": {"risk_profile.update"},
    "bank.cards": {"card.list", "card.freeze", "card.unfreeze", "card.report_lost", "card.limit_change"},
    "bank.card_controls": {"card.transaction_control"},
    "bank.subscriptions": {"subscription.list", "subscription.cancel", "direct_debit.list", "direct_debit.disable"},
    "bank.savings": {"wish_fund.lock", "wish_fund.release", "wish_fund.order"},
}

# feature id, title, group, required, degraded-if-missing, tools
FEATURES = [
    ("transfer", "智能转账（姓名 / 手机号 / 称呼 / 定时 / 周期 / 备注）", "六大场景", ["bank.payments", "bank.payees"], [],
     ["propose_transfer"]),
    ("payee_add", "新增收款人", "六大场景", ["bank.payees.create"], [], ["propose_add_payee"]),
    ("split", "AA 分摊与收款请求", "六大场景", ["bank.payees"], [], ["propose_split"]),
    ("bills", "账单分析 / 消费查询 / 异常识别", "六大场景", ["bank.transactions"], [], ["analyze_spending", "query_spending"]),
    ("balance", "余额查询", "六大场景", ["bank.balance"], [], ["get_balance"]),
    ("wealth", "理财推荐 / 对比 / 申赎 / 持仓 / 按消费配置", "六大场景", ["bank.wealth"], [],
     ["recommend_products", "compare_products", "propose_investment", "get_portfolio"]),
    ("risk_assessment", "风险测评", "六大场景", ["bank.risk"], [], ["start_risk_assessment"]),
    ("cards", "卡片管理", "六大场景", ["bank.cards"], ["bank.card_controls"], ["propose_card_operation", "list_cards"]),
    ("subscriptions", "订阅 / 代扣管理、周期扣费自动识别与续费提醒", "六大场景", ["bank.subscriptions"], [],
     ["list_subscriptions", "list_direct_debits", "propose_subscription_cancel", "propose_direct_debit_disable",
      "renewal_reminders", "review_subscriptions"]),
    ("wish_fund", "心愿金（跨场景：锁定 → 到期下单 → 释放）", "跨场景联动", ["bank.savings"], [],
     ["propose_wish_plan", "list_wish_funds", "propose_fund_release"]),
    ("automation", "联动规则：时间 / 余额 / 预算 / 大额触发", "跨场景联动", ["bank.transactions", "bank.balance"], [],
     ["propose_automation", "list_automations", "cancel_automation"]),
    ("automation_income", "联动规则：工资到账触发", "跨场景联动", ["bank.feed"], [], []),
    ("automation_tighten", "联动规则：自动冻结卡 / 关闭交易开关（预先授权，只收紧）", "跨场景联动", ["bank.cards", "bank.card_controls"], [], []),
    ("trip", "出行管家（余额 → 赎回 → 境外开关 → 返程关闭 → 预算提醒）", "跨场景联动", ["bank.card_controls", "bank.transactions"],
     ["bank.wealth"], ["propose_trip_plan"]),
    ("sandbox_extensions", "扩展业务：定期存款 / 外币 / 基金份额 / 黄金与定投 / 贷款与分期 / 信用账单（仅内置沙箱银行）", "扩展业务",
     ["bank.sandbox"], [], ["get_extended", "propose_extension", "get_finance", "propose_finance"]),
    ("life", "生活服务：机票 / 火车 / 酒店 / 演出 / 缴费（带条件跳转官方平台，不交易）", "跨场景联动", [], [], ["life_service"]),
    ("income", "入账明细与外部文本隔离", "安全", ["bank.feed"], [], ["list_income"]),
    ("protection", "反诈冷静期（转账保护）", "安全", [], [], ["enable_protection"]),
    ("strong_auth", "红色操作强验证", "安全", ["security.strong_auth"], [], []),
    ("nlu", "口语理解 / 多意图 / 多轮补全", "智能", ["llm.tools"], [], []),
    ("knowledge", "规则问答（附依据）与页面导航", "智能", [], [], ["knowledge", "navigate"]),
    ("feishu", "飞书渠道（黄色操作可在飞书确认，红色回到 APP）", "渠道", ["channel.feishu"], [], []),
]
# nlu degrades to the rule grammar instead of disappearing
SOFT = {"nlu"}
# Safety baseline: a customer cannot switch these off (they protect the customer against coercion and fraud).
LOCKED = {"strong_auth": "红色操作的强验证是安全底线，不能关闭",
          "protection": "反诈冷静期是安全底线：被诱导时需要随时可用",
          "income": "外部文本隔离保护你不被带毒备注攻击，不能关闭",
          "knowledge": "规则问答只读、无风险，始终开启"}
PREFS_SCHEMA = "CREATE TABLE IF NOT EXISTS feature_prefs(owner TEXT NOT NULL, feature TEXT NOT NULL, enabled INTEGER NOT NULL, " \
               "updated_at REAL NOT NULL, PRIMARY KEY(owner, feature))"


class CapabilityRegistry:
    def __init__(self, service, chat=None):
        self.s, self.chat = service, chat
        self._probe = {}
        self._last_hash = None
        with service.db.transaction() as conn:
            conn.execute(PREFS_SCHEMA)

    # ---- facts ---------------------------------------------------------------------------------
    def _bank_tools(self):
        try:
            return set(self.s.gateway._adapter.manifest)
        except AttributeError:
            return set()

    def facts(self):
        tools = self._bank_tools()
        manifest = getattr(getattr(self.s.gateway._adapter, "provider", None), "manifest", None)
        llm = getattr(self.chat, "llm", None)
        llm_state = "NOT_CONFIGURED"
        if llm is not None and getattr(llm, "available", False):
            llm_state = self._probe.get("llm", {}).get("status", "CONFIGURED")
            if time.time() < getattr(self.chat, "_breaker_open_until", 0):
                llm_state = "CIRCUIT_OPEN"
        from backend.channels.feishu import FeishuConfig
        feishu_config = FeishuConfig.from_env()
        import importlib.util
        feishu = feishu_config.enabled and (feishu_config.mode != "longconn" or importlib.util.find_spec("lark_oapi") is not None)
        safety = getattr(self.s, "safety", None)
        have = {req: tools >= needed for req, needed in BANK_TOOLS.items()}
        have.update({"llm.tools": llm_state in {"CONFIGURED", "VERIFIED"},
                     "bank.feed": getattr(self.s, "feed", None) is not None,
                     "bank.sandbox": not is_external(self.s),
                     "channel.feishu": feishu and self._probe.get("feishu", {}).get("status") != "FAILED",
                     "security.strong_auth": bool(safety and safety.enabled) or bool(self.s.config.strong_auth_secret)})
        return {"have": have,
                "llm": {"provider": ("deepseek-compatible" if hasattr(llm, "endpoint") else type(llm).__name__) if llm_state != "NOT_CONFIGURED" else None,
                        "model": getattr(llm, "model", None) if llm_state != "NOT_CONFIGURED" else None, "state": llm_state,
                        "probe": {k: v for k, v in self._probe.get("llm", {}).items() if k != "detail"}},
                "bank": {"provider": manifest.provider if manifest else None, "environment": manifest.environment if manifest else None,
                         "tools": sorted(tools)},
                "channels": {"app": True, "web": True, "feishu": have["channel.feishu"],
                             "feishu_mode": feishu_config.mode if feishu_config.enabled else None,
                             "access_mode": getattr(getattr(self.s, "access", None), "mode", "local")},
                "safety_profile": "competition" if safety and safety.enabled else "legacy"}

    # ---- features ------------------------------------------------------------------------------
    def prefs(self, owner):
        if not owner:
            return {}
        return {r["feature"]: bool(r["enabled"]) for r in self.s.db.rows("SELECT feature, enabled FROM feature_prefs WHERE owner=?", (owner,))}

    def set_pref(self, owner, feature, enabled):
        """A customer's own switch. Switching on only works when the deployment supports the feature."""
        if type(enabled) is not bool or feature not in {f[0] for f in FEATURES}:
            raise Rejected("INVALID_INPUT")
        if feature in LOCKED and not enabled:
            raise Rejected("FEATURE_LOCKED", LOCKED[feature])
        current = next(f for f in self.features(owner=owner) if f["id"] == feature)
        if enabled and current["status"] == "UNAVAILABLE":
            raise Rejected("FEATURE_UNAVAILABLE", current["reason"] or "当前配置不支持")
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO feature_prefs VALUES(?,?,?,?) ON CONFLICT(owner, feature) DO UPDATE SET enabled=excluded.enabled, "
                         "updated_at=excluded.updated_at", (owner, feature, int(enabled), time.time()))
            self.s.audit.append_in(conn, None, None, "FEATURE_PREFERENCE_CHANGED", "deployment",
                                   {"owner": owner, "feature": feature, "enabled": enabled})
        return next(f for f in self.features(owner=owner) if f["id"] == feature)

    def features(self, facts=None, owner=None):
        prefs = self.prefs(owner)
        result = self._features(facts)
        for f in result:
            f["locked"] = f["id"] in LOCKED
            f["lock_reason"] = LOCKED.get(f["id"])
            f["enabled"] = f["status"] != "UNAVAILABLE" and (f["locked"] or prefs.get(f["id"], True))
            if f["status"] != "UNAVAILABLE" and not f["enabled"]:
                f["status"], f["reason"] = "OFF", "你已在功能页关闭"
        return result

    def _features(self, facts=None):
        facts = facts or self.facts()
        have, result = facts["have"], []
        # Operator switch: a bank can turn features off for this deployment (e.g. TRUSTA_DISABLED_FEATURES=wealth,trip).
        switched_off = {x.strip() for x in os.environ.get("TRUSTA_DISABLED_FEATURES", "").split(",") if x.strip()}
        for fid, title, group, required, soft, tools in FEATURES:
            if fid in switched_off:
                result.append({"id": fid, "title": title, "group": group, "status": "UNAVAILABLE", "tools": tools,
                               "reason": "运营方已在本部署关闭该功能", "how_to_enable": ["从 TRUSTA_DISABLED_FEATURES 中移除 " + fid]})
                continue
            missing = [r for r in required if not have.get(r)]
            weak = [r for r in soft if not have.get(r)]
            if missing and fid in SOFT:
                status = "DEGRADED"
            elif missing:
                status = "UNAVAILABLE"
            else:
                status = "DEGRADED" if weak else "AVAILABLE"
            gaps = missing or weak
            note = {"nlu": "未接入大模型：使用规则引擎，只能识别标准说法"}.get(fid) if status == "DEGRADED" and fid in SOFT else None
            result.append({"id": fid, "title": title, "group": group, "status": status, "tools": tools,
                           "reason": note or ("缺少：" + "、".join(REQUIREMENTS[r][0] for r in gaps) if gaps else None),
                           "how_to_enable": [REQUIREMENTS[r][1] for r in gaps]})
        return result

    def disabled_tools(self, owner=None):
        return {tool: f for f in self.features(owner=owner) if f["status"] in {"UNAVAILABLE", "OFF"} for tool in f["tools"]}

    def tool_enabled(self, name, owner=None):
        feature = self.disabled_tools(owner).get(name)
        return (feature is None), feature

    # ---- snapshot ------------------------------------------------------------------------------
    def snapshot(self):
        facts = self.facts()
        features = self.features(facts)
        body = {"llm": {k: facts["llm"][k] for k in ("provider", "model", "state")}, "bank": facts["bank"],
                "channels": facts["channels"], "safety_profile": facts["safety_profile"],
                "features": {f["id"]: f["status"] for f in features}}
        value = digest(body)
        if value != self._last_hash:
            self._last_hash = value
            self.s.audit.append(None, None, "CAPABILITY_SNAPSHOT", "deployment", {"snapshot_hash": value, "snapshot": body})
        return value, body, features, facts

    def public(self, owner=None):
        value, body, _, facts = self.snapshot()
        features = self.features(facts, owner)
        return {"snapshot_hash": value, "generated_at": time.time(), "llm": facts["llm"], "bank": facts["bank"],
                "channels": facts["channels"], "safety_profile": facts["safety_profile"], "features": features,
                "summary": {s: sum(f["status"] == s for f in features) for s in ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "OFF")}}

    # ---- probes (explicit, never on the request path) --------------------------------------------
    def probe_llm(self):
        from backend.chat.llm import LLMUnavailable
        llm = getattr(self.chat, "llm", None)
        if llm is None or not getattr(llm, "available", False):
            self._probe["llm"] = {"status": "NOT_CONFIGURED", "at": time.time()}
            return self._probe["llm"]
        started = time.time()
        try:
            result = llm.complete("你是连通性检测。请调用 smalltalk 工具，kind=GREETING。", [{"role": "user", "content": "你好"}])
            ok = any(c.get("name") == "smalltalk" for c in result["calls"])
            self._probe["llm"] = {"status": "VERIFIED" if ok else "NO_TOOL_CALLING", "at": time.time(),
                                  "latency_ms": int((time.time() - started) * 1000)}
        except LLMUnavailable as exc:
            self._probe["llm"] = {"status": "FAILED", "code": exc.code, "detail": exc.detail, "at": time.time()}
        return self._probe["llm"]

    def probe_feishu(self):
        if not (os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET")):
            self._probe["feishu"] = {"status": "NOT_CONFIGURED", "at": time.time()}
            return self._probe["feishu"]
        from backend.channels.feishu import FeishuConfig, FeishuSender
        try:
            FeishuSender(FeishuConfig.from_env()).token()
            self._probe["feishu"] = {"status": "VERIFIED", "at": time.time()}
        except Exception as exc:  # network / credentials; the platform's own error text (never the secret) is kept for diagnosis
            self._probe["feishu"] = {"status": "FAILED", "code": getattr(exc, "code", type(exc).__name__),
                                     "detail": str(exc)[:160], "at": time.time()}
        return self._probe["feishu"]

    def probe_bank(self):
        """A read the adapter declares: proves the bank answers with the configured credentials."""
        try:
            owner = os.environ.get("TRUSTA_PROBE_CUSTOMER", "demo")
            from backend.bank_sdk.discovery import primary_account
            account = primary_account(self.s, owner)  # asks the bank itself for the account list
            if account and "account.get_balance" in self.s.gateway._adapter.manifest:
                self.s.gateway.read("transaction", owner, "account.get_balance", account_id=account["id"])
            self._probe["bank"] = {"status": "VERIFIED", "at": time.time()}
        except Exception as exc:
            self._probe["bank"] = {"status": "FAILED", "code": getattr(exc, "code", type(exc).__name__), "at": time.time()}
        return self._probe["bank"]
