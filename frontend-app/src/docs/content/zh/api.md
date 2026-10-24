# HTTP API

TRUSTA 的网页、终端客户端和飞书都通过同一套本地 HTTP API 工作。你也可以用它接入自己的界面。

## 基本约定

- 地址：`http://127.0.0.1:8000`。
- 除 `/health` 和静态页面外，所有接口都需要 `Authorization: Bearer <token>`。token 在启动时打印，默认保存在 `data/api.token`；配对过的手机使用自己的设备令牌。
- 服务端会校验 `Host` 和 `Origin`；客户身份由服务端决定，客户端**不能**传 `user_id`。
- POST 使用 `Content-Type: application/json`。
- 金额一律是账户币种的**整数最小单位**（人民币为分），不做隐式换汇。

## 对话

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/chat/sessions` | 新建会话 |
| GET | `/api/chat/sessions/{sid}` | 读取会话：历史消息、当前计划、待确认卡片（断线重连用） |
| POST | `/api/chat/sessions/{sid}/messages` | 发送一条消息 |
| POST | `/api/chat/sessions/{sid}/actions/{action_id}/decision` | 对确认卡做决定 |
| POST | `/api/chat/sessions/{sid}/actions/{action_id}/sms` | 为红色卡片发送模拟短信验证码 |
| POST | `/api/chat/sessions/{sid}/interrupt` | 中断当前计划 |
| POST | `/api/chat/sessions/{sid}/close` | 关闭会话 |

### 发送消息

```json
POST /api/chat/sessions/{sid}/messages
{
  "message_id": "m_01J…",
  "revision": 3,
  "text": "给我妈转两千，备注生活费"
}
```

- `message_id` 由客户端生成（1–64 位）；同一个 ID 重试返回原结果，不会重复处理。
- `revision` 是上一次响应里的版本号；不一致会返回 `CHAT_STALE`。
- 请求头 `Accept: text/event-stream` 返回 SSE 流；`Accept: application/json` 等整轮处理完一次性返回。

SSE 事件包括 `turn.started`、`guard.result`、`intent.recognized`、`plan.created` / `plan.updated`、`tool.started` / `tool.finished`、`message.delta`、`card`、`turn.completed`、`turn.failed`。以 `turn.completed` 里的完整结果为准。

### 对确认卡做决定

```json
POST /api/chat/sessions/{sid}/actions/{action_id}/decision
{
  "action_hash": "sha256:…",
  "approve": true,
  "verification": { "method": "SMS", "code": "123456" }
}
```

`action_hash` 必须和卡片上的完全一致；`verification` 只有红色操作需要（`TOTP` 或 `SMS`）。

## 任务（底层）

对话层下面是任务接口，每个资金动作都落到这里，保持哈希绑定、过期和防重放规则：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/tasks` | 创建任务，例如 `{"text":"给张三转 2000 元"}` |
| GET | `/api/tasks` | 当前客户的任务摘要 |
| GET | `/api/tasks/{id}` | 任务状态、任务图、授权及证据 |
| POST | `/api/tasks/{id}/authorize` | 授权，`{"action_hash":"…","approve":true}` |
| GET | `/api/balance` | 账户余额 |
| GET | `/api/transactions?account_id=…&month=YYYY-MM` | 指定账户和月份的交易记录 |
| GET | `/api/audit` | 审计链是否完整：`{"valid": true}` |
| GET | `/api/audit/proof` | 审计记录的 Merkle 包含证明 |

HTTP 成功并不代表资金执行成功，必须检查任务状态：

| 状态 | 含义 |
|---|---|
| `WAITING_USER` | 等待确认或补全信息 |
| `COMPLETED` | 已结算，并且银行侧状态核验通过 |
| `BLOCKED` | 被规则或安全关卡阻止 |
| `CANCELLED` | 用户拒绝 |
| `FAILED` | 执行前或银行事务失败 |
| `MANUAL_REVIEW` | 可能已执行，需要人工核查，**禁止重复转账** |

## 错误

| 状态码 | 含义 |
|---|---|
| 400 | 输入或安全校验失败 |
| 403 | 身份、来源或归属被拒绝 |
| 404 | 未知接口 |
| 500 | 未分类异常；应刷新原任务状态，不要据此再次发起资金操作 |

没有让客户端直接写银行的接口，没有给 Agent 使用的执行接口，也没有自动的资金写入重试。

完整协议见仓库中的 `docs/chat-protocol-v1.md` 和 `docs/api.md`。
