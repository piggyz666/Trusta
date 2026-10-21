# 本地 HTTP API

Base URL: `http://127.0.0.1:8000`。除 `/health` 和静态页面外均需要 `Authorization: Bearer <local-token>`。身份固定映射到演示客户 `demo`，不接受客户端 `user_id`。POST 使用 `Content-Type: application/json`。金额均为账户币种的整数最小单位（CNY 为分），不隐式换汇。

| 接口 | 用途 |
|---|---|
| `GET /health` | 服务存活；不包含银行事实 |
| `POST /api/tasks` | 创建任务，body: `{"text":"给张三转 2000 元"}`；可选 `source_account_id` |
| `GET /api/tasks` | 当前客户的任务摘要 |
| `GET /api/tasks/{id}` | Blackboard 任务状态、DAG、授权及证据 |
| `POST /api/tasks/{id}/authorize` | body: `{"action_hash":"…","approve":true,"strong_secret":"…"}`；非强认证可省略口令 |
| `POST /api/tasks/{id}/renew` | body: `{}`；仅过期授权，且动作仍有效 |
| `GET /api/balance` | 当前演示账户余额与 version |
| `GET /api/bank` | 当前客户的账户目录、币种、可用能力与受限付款状态 |
| `GET /api/payees` | 当前客户可用的收款路由；只读银行 profile 返回空列表 |
| `GET /api/balance?account_id=…` | 选择账户的原生币种余额；校验账户归属 |
| `GET /api/transactions?account_id=…&month=YYYY-MM` | 指定账户/月的规范交易记录；校验归属与读取完整性 |
| `POST /api/tasks/{id}/reconcile` | 空 body；Revolut 原已授权 MANUAL_REVIEW 转账的只读补充核验，不重发付款 |
| `GET /api/audit` | `{"valid":true}` 或 false |

Phase 2 沿用 `POST /api/tasks`：`{"text":"分析2026-09消费"}` 创建 BILL_ANALYSIS；`{"text":"推荐适合我的理财产品"}` 创建 WEALTH_RECOMMENDATION。支持“查看2026-09账单”“分析本月消费”“按我的风险等级推荐理财产品”。月份按 Asia/Shanghai 解析。

只读任务成功为 COMPLETED，报告在 `result`；DAG、读取快照和审计引用可查询。它们没有金融授权、合约、执行或回执。调用 authorize/renew 会拒绝，购买/赎回接口不存在。客户端不能指定客户身份、风险等级或task_type，账户参数仍需客户归属检查。

创建返回 HTTP 201 和任务；授权返回 HTTP 200 和最新任务。HTTP 成功并不代表资金执行成功，必须检查 `task.status`、`verification.status` 和回执。

`WAITING_USER`：等待确认或补全信息；`COMPLETED`：已结算且权威状态核验通过；`BLOCKED`：规则或安全关卡阻止；`CANCELLED`：用户拒绝；`FAILED`：已知执行前/银行事务失败；`MANUAL_REVIEW`：可能已执行，需人工核查，禁止重复转账。

输入或安全校验失败返回 400，身份/来源/归属拒绝返回 403，未知接口返回 404，未分类异常返回 500。500 后应刷新原任务，不得据此再次发起资金写入。

没有客户端直接银行写接口，没有供 Agent 使用的执行端点，没有自动金融写重试。

## Phase 3.2 Conversation

身份、Host / Origin 与字段检查沿用原接口。

| 接口 | 请求与用途 |
|---|---|
| `POST /api/conversations` | body `{}`；返回已认证客户的新会话与 revision |
| `GET /api/conversations/{id}` | 所属会话、槽位、轮次、当前任务 ID；已关闭 / 过期会话可查看但不可继续提交 |
| `POST /api/conversations/{id}/turns` | body `{turn_id, revision, text}`；可选 source_account_id；多轮补充与幂等结果 |
| `POST /api/conversations/{id}/close` | body `{}`；关闭会话并取消关联未执行草稿；不撤销已结算交易 |
| `GET /api/llm-calls/{id}` | 当前客户的调用元数据；不返回原始提示、响应或密钥 |

`turn_id` 为客户端生成的 1–64 字符字母、数字、下划线或横线 ID，可用 UUID；`revision` 为 GET / 上次回复提供的整数。例：

```json
{"turn_id":"turn-1","revision":0,"text":"帮我给张三转账"}
```

回复包含 conversation_id、turn_id、最新 revision、status、message、task_id、task；澄清时 task 可为 null。下一轮使用回复的 revision 与新 turn_id，发送“2000元”。不同文本或源账户复用同一 ID 拒绝；同一请求重试返回原轮结果，并读取该任务最新状态，不再次创建银行动作。

会话未确定状态 UNKNOWN 不属于任务类型。客户端不得传入模型、结构化 intent、approve、risk_level 或用户身份。澄清成功也不等于授权。聊天“我确认”只提示原授权卡；转账依然调用 `/api/tasks/{id}/authorize` 并绑定 action_hash。

400 包括 CONVERSATION_STALE、CONVERSATION_BUSY、TURN_IN_PROGRESS、TURN_ID_REUSED、CONVERSATION_EXPIRED、CONVERSATION_LIMIT；跨所有者访问为 403。未知网络失败后保留原 turn_id、文本与账户重试，先查看会话 / 任务状态，禁止自行重建金融任务。

## Phase 3.3 Memory 与安全状态

所有接口继续由本地 Bearer 身份及 Host / Origin 约束；客户端不能指定 user_id、kind、source_ref、信任等级或金融字段。

| 接口 | 请求与用途 |
|---|---|
| `GET /api/memory` | 所属有效记录；访问时清理过期项 / 已关闭会话引用 |
| `POST /api/memory/preferences` | `{key,value,consent:true}`；可选 ttl_seconds；同一键更新 revision |
| `POST /api/memory/conversation-reference` | `{conversation_id,consent:true}`；可选 ttl_seconds；仅所属有效会话，保存引用 |
| `POST /api/memory/risk-reference` | `{consent:true}`；可选 ttl_seconds；服务读取银行风险档案，只存引用 |
| `POST /api/memory/{id}/forget` | body `{}`；删除所属 Memory 项 |
| `POST /api/memory/clear` | body `{}`；仅清空当前客户 Memory |
| `GET /api/platform/security` | 五个 Agent 的银行 / 推理 / Memory 权限、V2 消息密钥版本元数据、扩展关闭状态 |

偏好白名单：`explanation_style=brief|detailed`、`finance_focus=monthly_summary|anomaly_summary`。自由文本、risk_level、余额、金额、账户、收款人、授权等均拒绝。默认 TTL 604800 秒，允许 1–2592000 的整数，会话 / 风险引用不得延长源对象有效期。

```json
{"key":"explanation_style","value":"detailed","consent":true,"ttl_seconds":604800}
```

保存返回 201 与记录；查询、忘记和清空返回 200。缺少明确同意、无效枚举 / TTL 或未知额外字段返回 400；跨所有者删除 / 引用访问为 403。忘记不改变银行数据，不删除历史任务、Conversation 原文或审计。

安全状态接口不返回根密钥、派生密钥、根绑定值或配置秘密；没有客户可调用的密钥轮换、权限更新或扩展启用端点。V2 历史运维见 [Phase 3.3](phase3.3-memory-communication.md)，当前 V3 运维见 [Phase 4](phase4-cryptographic-trust.md)。

## Phase 4 单记录审计证明

正常 Config.local 启动默认 V3。`GET /api/platform/security` 按当前 profile 返回真实状态：V3 的公钥身份、指纹、密钥版本 / 状态、能力承诺、ECDSA 与 Merkle 已实现、本地锚定可用；DID / 真正 Blockchain / ZKP 未实现。上面的 V2 描述为历史配置。

`GET /api/audit/proof?receipt_id=<uuid>`，也可使用 `audit_event_id` 或 `transaction_id`。必须恰好一个非空参数；重复参数、未知字段或混用参数返回 400。继续要求 Bearer、Host / Origin 和当前客户任务归属；其他客户或全局维护事件返回 403 / AUDIT_ACCESS_DENIED。没有客户可调用的 identity/key 管理或 worker 触发端点。

返回 schema_version=1 的独立 proof bundle：audit_event_id、event_hash、event_sequence、leaf_index、merkle_root、event、inclusion_proof、签名 checkpoint、signature_status、signer_key_status、signer_public_key 和 anchor 状态；回执查询附原 Receipt 和 receipt_schema_version=1，不更改旧回执。anchor_status 为顶层可变字段，不放入已签 checkpoint。服务核对本地记录并返回 record_verified / anchor_verified；这些布尔值不能替代独立验签。

未生成批次返回 400 / AUDIT_CHECKPOINT_PENDING，页面显示等待后台；查询不会同步创建检查点或触发银行操作。非法 / 损坏证据失败关闭。LocalDevelopmentAnchor 的 CONFIRMED 是本地核对，is_blockchain=false、externally_verified=false。未实现外部 adapter 不报告链上成功。

独立 CLI 使用预先可信的公钥重新核验原事件、Receipt、checkpoint 签名和包含路径。proof 自带公钥与 mutable status 不构成信任源；下载的文件不能仅因为 record_verified=true 就被信任。完整示例及外部信任限制见 [Phase 4](phase4-cryptographic-trust.md)。

## Phase 5 六场景与独立动作

正常启动banking_version=5，要求V3。客户/Host/Origin/closed fields规则不变。原转账与只读接口兼容。

`POST /api/tasks`仍接收`{text,source_account_id?}`。新请求示例：`冻结card-demo`、`解冻card-demo`、`card-demo限额设为1000元`、`设置card-demo交易控制线上关闭境外关闭非接触关闭`、`查询订阅`、`检测订阅`、`取消订阅sub-stream`、`查询代扣`、`关闭代扣dd-electric`、`比较sandbox-r1和sandbox-r2`、`购买sandbox-r1 1000元`、`赎回sandbox-r1 400元`、`AA100.01元给张三、李四`、`分析2026-09消费，找出可能的订阅，我逐项确认后取消`。

定时示例`下周五09:00给张三转1000元`或`在YYYY-MM-DD HH:MM:SS给张三转100元`；明确Asia/Shanghai、未来一年以内。缺少时刻会澄清，不猜测执行时间。姓名/手机号/alias必须匹配当前客户唯一登记收款人；自然语言账户可使用`从acct-savings给张三转100元`。AA仅生成分摊请求，不替其他参与者付款。

新业务任务返回action_records[]及聚合集合：每条包含action、prefix、policy、authorization、execution、verification、receipt、status；另有candidates、scheduled_job_refs。`GET /api/tasks/{id}`还读取经封印校验的scheduled_jobs[]。多条金融动作必须逐项选择。

| 接口 | 请求/结果 |
|---|---|
| `POST /api/tasks/{task}/actions/{action}/authorize` | `{action_hash,approve:true或false,strong_secret?}`；绑定所属任务、具体action_id及完整hash，仅WAITING_USER条目可决定 |
| `POST /api/tasks/{task}/actions/{action}/resume` | `{}`；仅未执行、非MANUAL_REVIEW的动作，重新读取权威资源，新动作/hash/授权，旧动作SUPERSEDED |

```json
{"action_hash":"填写该action的authorization.action_hash","approve":true}
```

不能传approve_all、结构化IR、risk_level、用户身份、执行结果或资源快照。不同动作共享hash/authorization、重复审批、额外字段/布尔字符串均拒绝。普通聊天“我确认”不执行资金或服务状态操作。解冻/提高限额/启用开关/高风险金额需要沙箱strong_secret。

WAITING_USER包括澄清和逐项审批；WAITING_TIME表示已批准但未到期。到期授权失效、风险提高或前置状态变化，暂停并产生需重新确认的新动作；Job为REAUTH_REQUIRED。长期调度不延长授权300秒TTL。不确定写结果为MANUAL_REVIEW，禁止resume或重发。任务COMPLETED可能包含用户拒绝的DENIED分支，客户端应展示各动作状态与真实成功回执。

新领域回执仍采用原结构，`/api/audit/proof`继续按receipt_id或bank_transaction_id提供证明。TIME触发是本地worker，**没有**客户可调用的tick、Card/投资直写、权限管理或自动批准端点。完整状态机、运行约束和事实边界见 [Phase5交付](phase5-full-banking.md)。
