# TRUSTA 对话协议 v1（后端 ↔ 前端 / IM 契约）

> 状态：草案 v1 · 2026-10-21
> 范围：统一对话入口 `/api/chat`、流式事件、卡片数据结构、LLM 工具 schema、风险分级映射。
> 不改变：现有执行链 BankActionIR → Policy → Authorization → Execution Contract → Gateway → 核验 → 回执。本协议只在它前面增加"理解—规划—对话"层。

---

## 0. 设计原则（前后端都要遵守）

1. **LLM 只提案，不授权。** 模型输出的是"工具调用候选"，必须经过后端校验器；任何写操作都落到现有的 `action_records`，由用户在确认卡上逐笔决定。
2. **风险等级由后端 Policy 决定，模型和前端都不能指定。** 前端只负责渲染 `risk_level` 和 `required_verification`。
3. **金融字面量必须有出处。** 金额、收款人、日期、产品等关键字段，模型必须给出它在用户原话里的片段（`span`），后端核对后才接受；缺失就追问，绝不让模型猜。
4. **展示的数字只来自工具结果。** 回复里出现的余额、金额、笔数，后端会核对是否出现在本轮工具结果中（防幻觉）。
5. **聊天里说"确认"不等于授权。** 授权只能通过确认卡的决定接口（带 `action_hash`）。
6. **APP 和 IM 共用同一套会话服务**，只是渠道适配不同（第 7 节）。

---

## 1. 总体流程

```
用户消息
  │
  ▼
[A] 输入安全检查  ── 注入 / 诱导转账 / 信息套取 ──► risk.alert（拒绝或强提示）
  │
  ▼
[B] Supervisor(LLM)：意图识别 + 槽位抽取 ──► 缺字段 ──► clarify 卡（追问）
  │
  ▼
[C] Planner(LLM)：拆成子任务 DAG ──► TaskGraphValidator 校验
  │
  ▼
[D] 工具调用校验器：schema / span 出处 / 归属 / 资源存在性
  │
  ├─ 只读工具（绿）──► 直接执行 ──► report 卡 + 文本总结
  │
  └─ 提案工具 ──► 现有 BankActionIR + Policy
                     ├─ YELLOW ──► confirm 卡（用户确认）
                     └─ RED    ──► confirm 卡（+ TOTP/短信验证）
                                      │
                                      ▼
                         决定接口 ──► 执行 ──► 核验 ──► result 卡（回执）
```

---

## 2. 接口一览

Base URL 与鉴权沿用现有约定（Bearer 本地令牌、Host/Origin 校验、身份由服务端确定，客户端不能传 `user_id`）。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/chat/sessions` | 新建会话 |
| GET | `/api/chat/sessions/{sid}` | 读取会话：历史消息、当前计划、待确认卡片（断线重连用） |
| POST | `/api/chat/sessions/{sid}/messages` | 发送一条用户消息；返回 SSE 流或一次性 JSON |
| POST | `/api/chat/sessions/{sid}/actions/{action_id}/decision` | 对确认卡做决定（同意/拒绝 + 验证码） |
| POST | `/api/chat/sessions/{sid}/interrupt` | 中断当前计划（未执行的节点全部取消） |
| POST | `/api/chat/sessions/{sid}/close` | 关闭会话 |
| POST | `/api/security/otp/sms` | 为某个待确认动作下发模拟短信验证码 |
| POST | `/api/channels/{channel}/webhook` | IM 渠道入口（飞书 / 企业微信），见第 7 节 |

### 2.1 新建会话

`POST /api/chat/sessions`

```json
// 请求
{ "channel": "app", "default_account_id": "acct-demo" }

// 响应 201
{ "session_id": "cs_8f3a…", "revision": 0, "state": "IDLE",
  "accounts": [{ "account_id": "acct-demo", "display": "活期 ****0001", "currency": "CNY" }],
  "suggestions": ["我这个月花了多少钱", "给张三转500元", "我的卡丢了"] }
```

### 2.2 发送消息

`POST /api/chat/sessions/{sid}/messages`

```json
{
  "message_id": "m_01J…",       // 客户端生成，1–64 位；同一 ID 重试返回原结果（幂等）
  "revision": 3,                // 上一次响应里的 revision；不一致返回 CHAT_STALE
  "text": "给我妈转两千，备注生活费",
  "account_id": "acct-demo"     // 可选，不传用会话默认账户
}
```

- 请求头 `Accept: text/event-stream` → SSE 流式（APP 推荐）。
- 请求头 `Accept: application/json` → 等整轮处理完，一次性返回 `ChatTurn`（IM 和测试脚本用）。

一次性返回的 `ChatTurn`：

```json
{
  "session_id": "cs_8f3a…",
  "message_id": "m_01J…",
  "revision": 4,
  "state": "AWAITING_CONFIRMATION",
  "reply": {
    "text": "已为你准备一笔转账，请核对后确认。",
    "cards": [ /* 第 3 节的卡片 */ ]
  },
  "plan": { /* 第 3.6 节，可为 null */ },
  "trace_id": "tr_…"           // 对应审计链，答辩演示可展示决策链路
}
```

### 2.3 确认卡决定

`POST /api/chat/sessions/{sid}/actions/{action_id}/decision`

```json
{
  "action_hash": "sha256:…",          // 必须与卡片上的完全一致
  "approve": true,
  "verification": {                   // 仅 RED 级别需要
    "method": "TOTP",                 // TOTP | SMS
    "code": "123456"
  }
}
```

后端内部转调现有的 `POST /api/tasks/{task}/actions/{action}/authorize`，保持原有的 hash 绑定、过期、重放规则不变。响应为最新的 `ChatTurn`（通常带 `result` 卡，或下一张待确认卡）。

### 2.4 中断

`POST /api/chat/sessions/{sid}/interrupt` body `{ "revision": 5 }`
未授权的节点 → `CANCELLED`；已授权未执行的定时任务 → 取消；已执行的不回滚，会在回复里列出来。

### 2.5 模拟短信验证码

`POST /api/security/otp/sms` body `{ "session_id": "...", "action_id": "..." }`
→ `{ "sent_to": "138****0001", "expires_in": 300, "dev_code": "482913" }`
`dev_code` 只在 `TRUSTA_SAFETY_PROFILE=competition` 的演示模式返回，方便评委操作；生产配置下不返回。验证码绑定 `action_hash`，一次有效。

---

## 3. 卡片（`reply.cards[]`）

所有卡片都有 `type` 和 `card_id`。前端按 `type` 渲染，遇到未知类型降级成纯文本。

### 3.1 `clarify`：追问

```json
{ "type": "clarify", "card_id": "c1",
  "slot": "recipient",
  "question": "你通讯录里有两个“王芳”，要转给哪一位？",
  "options": [
    { "label": "王芳（138****1234）", "value": "payee-12" },
    { "label": "王芳（139****5678）", "value": "payee-31" }
  ],
  "free_text_allowed": true }
```

用户点选项时，前端把 `value` 当作下一条消息的 `text` 发送，并带上 `"reply_to": "c1"`。

### 3.2 `confirm`：确认卡（写操作）

```json
{ "type": "confirm", "card_id": "c2",
  "task_id": "t_…", "action_id": "a_…",
  "action_hash": "sha256:…",
  "action_type": "TRANSFER",
  "title": "转账给 妈妈",
  "fields": [
    { "label": "收款人", "value": "李秀英（妈妈）· 工商银行 ****6621" },
    { "label": "金额",   "value": "¥2,000.00", "amount_minor": 200000, "currency": "CNY" },
    { "label": "备注",   "value": "生活费" },
    { "label": "付款账户", "value": "活期 ****0001，转后余额 ¥7,950.00" }
  ],
  "risk_level": "RED",
  "risk_reasons": ["今日累计转账将超过 ¥1,000"],
  "required_verification": ["TOTP", "SMS"],   // 任选其一；YELLOW 时为空数组
  "expires_at": 1792560000,
  "evidence": { "source_spans": { "amount": "两千", "recipient": "我妈", "memo": "生活费" } } }
```

`fields` 是后端从**银行权威数据**生成的展示字段，前端不自己拼。`evidence.source_spans` 用来在 UI 上高亮"我从你的哪句话里理解到这个"。

### 3.3 `report`：只读结果（账单 / 理财 / 订阅等）

```json
{ "type": "report", "card_id": "c3", "report_type": "SPENDING_SUMMARY",
  "period": "2026-09",
  "summary": { "total_minor": 523400, "currency": "CNY", "tx_count": 47 },
  "breakdown": [ { "category": "餐饮", "amount_minor": 182000, "ratio": 0.348 } ],
  "anomalies": [ { "transaction_id": "tx-…", "reason": "DUPLICATE_WITHIN_5MIN", "amount_minor": 39900 } ],
  "chart_hint": "pie" }
```

`report_type` 枚举：`BALANCE`、`TRANSACTIONS`、`SPENDING_SUMMARY`、`ANNUAL_REPORT`、`ANOMALIES`、`PRODUCT_LIST`、`PRODUCT_COMPARE`、`RISK_PROFILE`、`SUBSCRIPTIONS`、`CARDS`、`RENEWAL_REMINDERS`。

### 3.4 `risk_alert`：风险提示

```json
{ "type": "risk_alert", "card_id": "c4",
  "category": "FRAUD_SUSPECTED",          // FRAUD_SUSPECTED | PROMPT_INJECTION | INFO_PROBING | ACCOUNT_LOCKED
  "severity": "HIGH",
  "message": "“转到安全账户”是常见诈骗话术，银行和公安不会要求你转账到所谓的安全账户。",
  "blocking": true,                       // true = 本轮不会生成任何确认卡
  "cooldown_seconds": 0,
  "audit_event_id": "ae_…" }
```

### 3.5 `result`：执行结果

```json
{ "type": "result", "card_id": "c5", "action_id": "a_…",
  "status": "SUCCEEDED",                  // SUCCEEDED | FAILED | MANUAL_REVIEW | CANCELLED | SCHEDULED
  "receipt_id": "rc_…",
  "message": "已向 李秀英 转账 ¥2,000.00，银行核验通过。",
  "proof_url": "/api/audit/proof?receipt_id=rc_…" }
```

`MANUAL_REVIEW` 的文案必须是"结果待核实，请勿重复转账"，前端不提供"重试"按钮。

### 3.6 `plan`：多步任务 DAG（放在 `ChatTurn.plan`）

```json
{ "plan_id": "p_…", "goal": "爱人生日：锁定 1000 元，生日前 2 天订花和蛋糕",
  "nodes": [
    { "id": "n1", "title": "锁定活期 ¥1,000", "kind": "WRITE",  "status": "AWAITING_CONFIRMATION", "action_id": "a_1" },
    { "id": "n2", "title": "11-03 订鲜花",    "kind": "WRITE",  "status": "PENDING", "depends_on": ["n1"], "scheduled_at": 1793664000 },
    { "id": "n3", "title": "11-03 订蛋糕",    "kind": "WRITE",  "status": "PENDING", "depends_on": ["n1"], "scheduled_at": 1793664000 }
  ] }
```

节点状态：`PENDING` / `RUNNING` / `AWAITING_CONFIRMATION` / `SCHEDULED` / `SUCCEEDED` / `FAILED` / `CANCELLED` / `MANUAL_REVIEW` / `SKIPPED`。

---

## 4. SSE 事件流

每个事件为 `event: <name>` + `data: <json>`。事件顺序保证；每轮最后一定是 `turn.completed` 或 `turn.failed`。

| 事件 | data | 说明 |
|---|---|---|
| `turn.started` | `{message_id, revision}` | |
| `guard.result` | `{passed, category?}` | 输入安全检查结果（拦截时紧跟 `card`） |
| `intent.recognized` | `{intents:[{type, confidence}]}` | 可在 UI 上显示"正在帮你查账单…" |
| `plan.created` / `plan.updated` | `plan` 对象 | |
| `tool.started` / `tool.finished` | `{node_id, tool, status}` | 用于进度展示，不含敏感数据 |
| `message.delta` | `{text}` | 回复文本增量 |
| `card` | 卡片对象 | |
| `turn.completed` | 完整 `ChatTurn` | 前端以这个为最终状态 |
| `turn.failed` | `{code, message}` | |

断线重连：`GET /api/chat/sessions/{sid}` 拿到最新状态即可，不需要事件回放。

---

## 5. 会话状态机

```
IDLE ─消息─► UNDERSTANDING ─┬─缺字段──► CLARIFYING ─补充─► UNDERSTANDING
                            ├─只读────► ANSWERED ─────────► IDLE
                            ├─写操作──► AWAITING_CONFIRMATION ─决定─► EXECUTING ─► ANSWERED
                            ├─定时────► SCHEDULED（仍可中断）
                            └─拦截────► BLOCKED ─────────► IDLE
任意非终态 ── interrupt / “算了” ──► IDLE（未执行节点取消）
连续失败或可疑 ──► LOCKED（需安全解锁，沿用现有熔断）
```

对话中改主意：在 `AWAITING_CONFIRMATION` 时用户说"改成 800"，后端作废旧动作（`SUPERSEDED`）并生成新的确认卡，新卡带新的 `action_hash`。

---

## 6. LLM 工具 schema（Supervisor / Planner 使用）

模型通过 function calling 使用以下工具。**模型从来不直接调用银行**：每个工具调用都先过校验器，再映射到现有的 Gateway 读接口或 BankActionIR 提案。

### 6.1 公共约定

- 金额：`{"value": "2000.00", "span": "两千"}`。`value` 是十进制字符串，单位为账户币种的元；`span` 必须是用户本会话原话里的子串，后端用中文数字解析器复核 `span` 和 `value` 一致。
- 收款人：`{"by": "name|phone|alias|relation", "value": "妈妈", "span": "我妈"}`，后端用 `payee.resolve` 解析：0 个 → 追问或拒绝；多个 → `clarify` 卡。
- 时间：`{"iso": "2026-11-05T09:00:00+08:00", "span": "下周三早上九点"}`；只接受未来 366 天内。
- 任何关键字段没有 `span` → 校验器把这次调用改成 `ask_user`。

### 6.2 只读工具（GREEN，直接执行）

| 工具 | 参数 | 映射 |
|---|---|---|
| `get_balance` | `account_id?` | `account.get_balance` |
| `list_transactions` | `period{from,to}`, `category?`, `counterparty?`, `min_amount?` | `transaction.list` |
| `analyze_spending` | `period`, `group_by: category\|merchant\|month` | Finance Agent |
| `detect_anomalies` | `period` | Finance Agent |
| `annual_report` | `year` | Finance Agent |
| `list_subscriptions` | `include_direct_debits: bool` | `subscription.list` + `direct_debit.list` + `recurring_history` |
| `renewal_reminders` | — | EventPlans.reminders |
| `list_cards` | — | `card.list` |
| `get_risk_profile` | — | `customer.get_risk_profile` |
| `list_products` | `risk_max?`, `term?`, `sort_by?` | `wealth.list_products` |
| `compare_products` | `product_ids[2..4]` | Wealth Agent |
| `find_payee` | `payee_ref` | `payee.resolve`（只返回打码后的信息） |

### 6.3 提案工具（生成确认卡，等级由 Policy 决定）

| 工具 | 参数 | 典型等级 | 状态 |
|---|---|---|---|
| `propose_transfer` | `payee_ref`, `amount`, `memo?`, `schedule?{once_at \| recurrence{freq:monthly\|weekly, day, time, until?}}` | 日累计 ≤ ¥1000 为 YELLOW，否则 RED | 备注、周期**需新增** |
| `propose_split` | `total`, `participants[]`, `method: EQUAL\|CUSTOM`, `values?` | YELLOW | 已有 |
| `propose_investment` | `op: PURCHASE\|REDEEM`, `product_id`, `amount` | RED | 已有 |
| `start_risk_assessment` | — | GREEN（问卷，多轮） | **需新增** |
| `propose_card_operation` | `op: APPLY_VIRTUAL\|FREEZE\|UNFREEZE\|REPORT_LOST\|CANCEL_LOST\|LIMIT_CHANGE\|CONTROLS\|CHANGE_PIN`, `card_id?`, `params?` | 申请虚拟卡、冻结为 YELLOW；解冻、挂失、解挂、改密码、提额为 RED | 解挂、改密码**需新增** |
| `propose_subscription_cancel` | `ids[]`（逐项生成确认卡） | YELLOW | 已有 |
| `propose_direct_debit_disable` | `ids[]` | YELLOW | 已有 |
| `propose_fund_lock` | `amount`, `until`, `purpose` | YELLOW | **需新增**（活期内锁定一部分资金） |
| `propose_event_plan` | `event{type, date, person?}`, `steps[]` | 由每个子步骤决定 | **需新增**（替代 BIRTHDAY_TRANSFER，支持多步） |

### 6.4 控制工具

| 工具 | 用途 |
|---|---|
| `ask_user` | `{slot, question, options?}` → `clarify` 卡 |
| `plan_tasks` | Planner 输出 DAG：`{nodes:[{id, tool, args, depends_on[], at?}]}` → `TaskGraphValidator` |
| `out_of_scope` | `{reason}` → 礼貌说明能力范围并给出可用建议 |
| `flag_risk` | `{category, evidence_span}` → `risk_alert`（模型可以报告风险，但不能解除风险） |

### 6.5 校验器规则（后端，确定性）

1. 工具名在白名单内，参数通过 JSON Schema（禁止额外字段）。
2. 所有 `span` 必须是本会话用户原话的子串；数值和 `span` 的解析结果一致。
3. 账户、卡、产品、订阅等 ID 必须属于当前用户（Gateway 读取验证）。
4. 单轮写提案 ≤ 5 个，DAG ≤ 32 个节点（沿用现有上限）。
5. 模型输出里出现 `approve`、`risk_level`、`strong_secret`、`skip`、`executor` 等字段 → 整轮作废，记 `PROMPT_INJECTION`。
6. 回复文本里的金额和数字必须能在本轮工具结果里找到，否则改用模板回复。

---

## 7. 渠道适配

| 渠道 | 入口 | 流式 | 确认卡 | 强验证 |
|---|---|---|---|---|
| APP / Web | `/api/chat/*` | SSE | 原生卡片 | TOTP / 短信 |
| 飞书机器人 | `/api/channels/feishu/webhook` | 否（整轮回复） | 交互式消息卡片，按钮回调到 webhook | 只发链接，跳转 APP 完成验证 |
| 企业微信 | `/api/channels/wecom/webhook` | 否 | 模板卡片 | 同上 |

IM 规则：IM 用户需要先和 TRUSTA 账户绑定；**RED 级别的动作在 IM 里不能完成**，只能跳转 APP 验证（防止群聊或会话被劫持后直接大额转账）。webhook 要校验平台签名，并对事件 ID 去重。

---

## 8. 错误码（新增）

| HTTP | code | 含义 / 前端处理 |
|---|---|---|
| 400 | `CHAT_STALE` | revision 落后，先 GET 会话再重发 |
| 409 | `CHAT_BUSY` | 本会话上一轮还在处理 |
| 400 | `MESSAGE_ID_REUSED` | 同一 ID 不同内容 |
| 400 | `ACTION_HASH_MISMATCH` | 卡片已更新，刷新会话 |
| 400 | `ACTION_EXPIRED` | 授权超时（300 秒），可让用户说"重新确认" |
| 401 | `VERIFICATION_FAILED` | 验证码错误；累计次数会触发锁定 |
| 423 | `ACCOUNT_LOCKED` | 已熔断，显示联系客服 / 安全解锁 |
| 503 | `LLM_UNAVAILABLE` | 模型不可用，后端自动降级到规则解析，前端提示"能力受限" |

现有错误码（`POLICY_BLOCKED`、`RECIPIENT_AMBIGUOUS` 等）原样透传，放在 `turn.failed.code` 或卡片的 `risk_reasons` 里。

---

## 9. 后端实施拆分

| 序号 | 模块 | 新增 / 改动 | 依赖 |
|---|---|---|---|
| B1 | `backend/chat/` 会话服务、状态机、幂等 | 新增；复用 `conversation/store.py` 的 revision 和幂等机制 | — |
| B2 | `backend/chat/sse.py` + server 路由 | 新增 | B1 |
| B3 | `backend/llm/tools.py` 工具 schema + function calling 适配（DeepSeek / OpenAI 兼容） | 新增；复用 `llm/gateway.py`、Provider | — |
| B4 | `backend/chat/validator.py` span 出处、归属、注入字段检查 | 新增；复用 `TaskGraphValidator` | B3 |
| B5 | Supervisor / Planner 改为 LLM 驱动，规则解析降级为兜底 | 改 `agents/supervisor.py`、`agents/planner.py` | B3 B4 |
| B6 | 输入安全检查：注入、反诈话术、信息套取 | 新增 `security/input_guard.py` | — |
| B7 | 回复生成与数字核对 | 改 `llm/explanations.py` | B3 |
| B8 | 业务补齐：转账备注、周期转账、解挂、改密码、风险测评、资金锁定、多步事件计划 | 改 `banking/*`、`runtime/event_plans.py` | — |
| B9 | 模拟短信验证码 | 改 `security/banking_controls.py` | — |
| B10 | IM 渠道适配（先做飞书） | 新增 `backend/channels/` | B1 |
| B11 | 评测集：100+ 口语 / 攻击样本，自动统计完成率、轮次、拦截率 | 新增 `benchmarks/chat_eval.py` | B5 |

建议顺序：B1 → B3 → B4 → B5 → B2 → B6 → B7 → B8 → B9 → B11 → B10。

前端在 B1、B2 完成前可以先用本文的 JSON 示例做 mock 开发。

---

## 10. 待定问题

1. LLM 主力用 DeepSeek 还是同时保留 Gemini 作为回退？（影响 B3 的 Provider 配置）
2. "资金锁定"是在活期账户里冻结一部分余额，还是转入一个单独的"心愿金"子账户？（影响 B8 的数据模型和演示效果）
3. IM 先做飞书还是企业微信？
4. 演示环境的短信验证码直接在响应里返回（`dev_code`），还是要在前端单独显示一个"模拟手机"？
