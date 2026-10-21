# TRUSTA V1 架构

当前状态以 [后端最终能力矩阵](final-backend/TRUSTA_Final_Capability_Matrix.md) 为准。ECDSA V3、did:key 验证、真实 Groth16 本地证明与验证、签名 Merkle 检查点已实现；真实外部区块链部署与独立验收仍受签名批准及第二 RPC 阻塞。原 Hash Chain 和金融执行链保留，锚定在独立 worker 内。下文为历史 V1 设计，后续阶段扩展了密码能力，未改变金融执行边界。

本文记录V1冻结架构。Phase 2只读场景在相同Runtime/Blackboard/Gateway中扩展，见 `phase2-scenarios.md`；金融写执行边界不变。

## 用户确认的调整

1. 不创建独立 `trusted/` 模块。可信职责放入 `security/{identity,signature,capability,replay_protection}` 与 `execution/{contract,gateway}`。
2. Channel/API 后增加非 Agent 的 Intent Normalization。
3. 顺序：领域模型与 Virtual Bank → Agent Runtime → Trusted Execution Pipeline → End-to-End Transfer → Frontend。
4. 可信通信仅实现 Agent 身份、消息签名、payload hash 与 nonce 防重放。不扩展 DID、区块链、Merkle。

## 信任边界

```text
Browser / HTTP Channel
  → Intent Normalization (non-agent)
  → Supervisor → Planner → Transaction Agent
     ↕ Blackboard / Reference Message Bus / Deterministic Scheduler
  → BankActionIR
  → Schema Validator → Capability Manager
  → Policy → Authorization → Execution Contract
  → Tool Gateway → VirtualBankAdapter → SQLite Virtual Bank
  → Hard Verifier (reads through Tool Gateway)
  → Receipt / append-only Audit Hash Chain
```

金融写入唯一业务调用位置为 `ToolGateway.execute`，只有确定性 executor 的能力授权及有效持久化合约才能调用 Adapter 的 `create_transfer`。五个 Agent 的代码没有执行或 Adapter 调用。银行自己的数据库记账属于银行内部实现；初始化演示数据属于启动过程，不是用户金融写接口。

## 能力映射

Agent 权限 `transfer.propose` 仅准许生成提案。IR 的 `requested_capability=banking.transfer` 是提案请求的能力，不是发起 Agent 持有的能力。执行主体 executor 持有 `banking.transfer`，合约将它授权给网关规范能力 `transfer.create`。

读取映射：`account.get → account.read`、`account.get_balance → balance.read`、`payee.resolve → payee.read`、`transaction.get → transaction.read`。网关检查能力、适配器 manifest 和客户归属。

## 持久化与一致性

SQLite WAL + `BEGIN IMMEDIATE`：银行在单个事务中完成幂等查找、账户归属与冻结检查、余额/version 前置条件检查、扣款、结算入账、双边 LedgerEntry、SETTLED 交易和幂等结果发布。相同有效键及动作哈希返回原交易；键相同但动作不同拒绝。

任务、步骤、提案、规则、授权、合约、回执、审计、nonce 都持久化。Blackboard 的步骤与独立步骤表同事务更新。进程内锁序列化任务工作流，银行数据库唯一约束防止并发重复扣款。

Bus 是进程内 reference bus，不承担消息恢复；恢复依据持久化 Blackboard。关键 Agent 交接消息先保存结构化结果引用，再签名、校验 payload hash 并持久化消费 nonce，审计记录消息哈希。

## 授权与核验

所有 IR 字段通过规范 JSON 序列化和 SHA-256 形成动作哈希。金额为整数分。每笔写入均要求确认；高风险要求沙箱口令。合约绑定动作、policy、authorization、幂等键、前置条件及有效期，并用独立用途的 HMAC 密钥封装。

核验重新读取交易及余额，检查金额、账户、收款人、币种、SETTLED、预期余额及账户 version。并发其他交易导致当前余额/version 不再匹配时保守进入人工核查，不尝试证明未经规格定义的快照语义。

## 恢复

WAITING_USER 任务直接恢复，过期授权禁止执行；授权有效期内不得随意重签。授权过期而动作有效时，重新检查 policy 后签发新授权。动作过期必须新建请求。

重启发现 EXECUTING/VERIFYING 时进入 MANUAL_REVIEW，没有自动写重试。执行前 RUNNING 中断则 FAILED。审计链损坏时启动失败。完成核验之前没有成功回执。
