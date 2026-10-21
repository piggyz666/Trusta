# 多 Agent 可信通信：检查结论与本轮修正（2026-10-21）

> 结论先行：项目原有的 V3 可信通信设计扎实、可演示、有测试和攻击基准支撑，**可以作为主要创新点**。
> 本次检查发现的主要问题是：**新加的对话入口有一部分绕过了它**。这一问题已修复，并补上了“意图来源”证明和证据导出接口，让评委能当场看到、当场验证。

---

## 1. 现有机制（逐项读代码确认）

```
用户原话 ──► Supervisor ──REQUEST──► Planner ──PLAN──► 业务 Agent ──READ_ONLY_REPORT / ACTION_PROPOSED──► Supervisor
               │            （每条消息：发送方 ECDSA P-256 签名 + 运行时核验 + 防重放 + 只允许计划内的边）
               ▼
        Blackboard（权威任务状态）
               │ 每条被接受的消息 → 运行时审计签名人再签一份“交接证据”（指向前序证据的哈希 = 因果链）
               ▼
        只追加审计哈希链 → 签名 Merkle 检查点 → 单条记录包含证明 → 可离线验证
```

| 机制 | 代码位置 | 防什么 |
|---|---|---|
| 每个 Agent 独立的 ECDSA P-256 身份（URN + did:key），私钥加密落盘 | `security/crypto_registry.py`、`crypto.py`、`did_key.py` | 冒充其他 Agent |
| V3 消息信封：域分隔签名、规范化 JSON、负载哈希、负载版本（= 任务 revision）、5 分钟有效期、持久化 nonce、接收方绑定 | `security/message_v3.py`、`replay_protection.py` | 篡改、重放、过期消息、错发 |
| **能力快照哈希**：消息里带发送方当前权限承诺，与注册表不一致即拒绝 | `message_v3.py`、`security/registry.py` | Agent 越权、权限被悄悄放大 |
| **通信边约束**：每种任务只允许计划内的（发送方、接收方、消息类型、步骤）组合 | `service._validate_message_context` | 例如 Finance 冒出一条“转账提案” |
| 运行时交接证据：审计签名人对每次交接再签名，并用前序证据哈希串成因果链 | `runtime/handoff_evidence.py` | 事后否认、顺序被调换 |
| 审计哈希链 + 签名 Merkle 检查点 + 包含证明 + 离线验证脚本 | `audit.py`、`audit_trust/*`、`scripts/verify_handoff_evidence.py` | 日志被改、被删 |
| 密钥轮换（宽限期）、吊销、身份暂停；能力承诺过期则拒绝启动 | `crypto_registry.py` | 密钥泄露后的处置 |
| Agent 只能提案，执行由确定性执行器 + HMAC 封印的执行合约完成 | `execution/contract.py`、`gateway.py` | 被攻破的 Agent 直接动钱 |
| （可选）Finance 运行在独立的 Windows AppContainer 进程，通过窄口代理通信 | `runtime/finance_transport.py` | 单个 Agent 进程被攻破后的横向移动 |

**攻击基准**（`python -m benchmarks.security_benchmark --banking-version 5`，本轮重跑）：伪造身份、无效签名、负载篡改、重放、过期、吊销密钥、宽限期外旧密钥、越权、能力快照篡改、未授权工具调用、授权哈希不符、重复执行、审计篡改、Merkle 叶子 / 证明 / 检查点 / 锚定根篡改、签名算法降级，共 **18 类 × 5 次，全部拦截，0 笔意外交易**。

## 2. 本次发现的问题与修正

| # | 问题 | 影响 | 修正 |
|---|---|---|---|
| 1 | 对话入口的**只读查询**（余额、消费查询、持仓、续费提醒）直接调用 Agent 方法，不建任务、不发签名消息 | 这些回答没有 Agent 签名，也没有交接证据，和“全链路可信通信”的说法不一致 | 新增 4 种只读任务（`BALANCE_QUERY` / `SPENDING_QUERY` / `PORTFOLIO_QUERY` / `RENEWAL_QUERY`），走完整的 REQUEST → PLAN → 签名 READ_ONLY_REPORT 链路 |
| 2 | “理解”这一步（模型 / 规则把原话变成结构化意图）没有出现在签名链里 | 无法证明某个提案确实来自用户的哪句话、经过哪种理解方式 | 意图里新增 `origin`：渠道、会话、消息 id、**原话哈希**、理解方式（deepseek / rules）、模型名、**工具调用记录哈希**。它在 Supervisor 签名的 REQUEST 负载里，因此被签名覆盖 |
| 3 | 交接证据只能用离线脚本导出，演示时看不到 | 创新点不可见 | ① `GET /api/chat/sessions/{sid}/trace/{message_id}` 的每个任务新增 `agent_messages`：逐条列出发送方、接收方、类型、身份 URN、密钥 id、负载哈希，并**当场重新验签**（消息签名、证据签名、因果父节点）；② 新增 `GET /api/chat/sessions/{sid}/evidence/{task_id}`：导出证据包 + 公钥注册表，可用 `python -m scripts.verify_handoff_evidence` 离线验证 |

新增测试 `tests/test_trusted_chat_comm.py`（5 项）：转账链路三条消息全部验签通过且因果相连；REQUEST 的负载哈希正好等于带来源证明的意图；只读回答由对应 Agent 签名；离线验证通过，**篡改负载、冒充发送方、用轮换后的公钥验证都会失败**；不能导出别的会话或别的用户的证据。

## 3. 诚实的边界（答辩时请按这个口径讲）

1. **单进程信任域**：默认所有 Agent 的私钥由同一个后端进程持有（加密存储）。签名证明的是“运行时确认是哪个逻辑 Agent 产出了这条内容，且之后没被改过”，**不等于**各 Agent 部署在互不信任的独立机器上。被攻破的宿主进程理论上可以冒充任意 Agent 签名；缓解措施是 Finance 的可选进程隔离、执行器与 Agent 的权限分离、以及外部可验证的审计证据。生产化需要每个 Agent 独立进程 + 独立密钥保管（HSM / KMS）。
2. **系统主体不发签名消息**：Policy、授权、执行器、核验器属于确定性系统组件，它们的结果写入审计哈希链和 Merkle 检查点，并以哈希形式进入交接证据，但它们本身不是“Agent 消息”。
3. **执行合约用 HMAC 封印**，只能由持有密钥的后端验证，不能由第三方离线验证；对外可验证的是审计记录和交接证据。
4. **证据证明的是过程，不是银行结果本身**：银行侧结果由硬核验（重新读取权威状态）和回执保证。

## 4. 演示建议（30 秒）

1. 对话：“给张三转 100” → 确认。
2. 打开该轮的 trace：展示 `supervisor → planner → transaction → supervisor` 三条消息，每条 `message_signature_valid: true`，以及 REQUEST 里的 `origin`（原话哈希、模型名）。
3. 导出 evidence，命令行离线验证 → `verified: true`；手动改一个字节再验证 → `false`。
