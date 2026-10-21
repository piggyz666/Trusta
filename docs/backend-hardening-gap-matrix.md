# 实施前缺口矩阵

| 域 | 实际根因与路径 | 严重性 | 决策与验收 |
|---|---|---|---|
| LLM 合法规划 | llm/graph_protocol.py 冗余字段与交叉参数 shape；缺六场景新 holdout | 高 | 新紧凑 schema，原 Validator 不变；固定 gold/分母，真实模型单次测试 |
| 原子接收 | service.py 接收 nonce/audit 与 agent_results 两次事务 | 高 | 同事务结果/inbox/evidence/audit；故障注入与重复 delivery |
| Agent 访问边界 | service.py 同进程，crypto_registry.py 宿主拥有全部 key | 高 | Finance AppContainer + 固定主体签名 broker；真实访问拒绝测试 |
| 因果证明 | audit.py 只有 payload hash；records.py 只验证 inclusion | 高 | Runtime evidence 连接 V3/父节点/版本/输入输出/读取证据，离线验证 |
| 跨进程任务竞争 | graph_runtime.py 的锁只存在于进程内 | 高 | 数据库竞争所有权，无 TTL 抢占；不是所有写路径通用 fencing；异常恢复保守关闭 |
| 银行集成 | docs/revolut-banking-validation.json 的官方沙箱历史验证 | 中 | 核查真实请求/ID/receipt/模拟结算来源；不重新付款 |
| 公链 | external_anchor.py 已实现；配置缺失 | 外部阻塞 | 单列 BLOCKED_BY_CREDENTIAL；本地 EVM 不等于 Sepolia |
| 历史统计 | phase6_llm_metrics.py 与当前 generator 已区分候选标记，初步怀疑经源码排除 | 核对完成 | 复算不改历史 raw output；新指标分栏 |
| DID/ZKP | 已有 did_key.py/privacy_proof.py；单机 setup 与本地 issuer | 范围限制 | 全量回归与事实分级，不重新搭链/ceremony |
| 容量与可靠性 | 缺当前 IPC、长 audit、锁竞争、丢 ACK benchmark | 中 | 原始采样、p50/p95/分母/环境，限制声明 |

最终状态将在交付报告和 verification manifest 中关联实际测试证据，不能以此计划表替代实现结果。

## 实际完成状态

| 缺口 | 本轮状态 | 剩余限制 |
|---|---|---|
| LLM 任务规划 | 紧凑协议、真实两模型、独立最终120例已交付 | 合法命中率65.28%/80.56%，非任意语言成熟产品 |
| V3 原子接收 | nonce/audit/evidence/result/outbox ACK 同事务 | Scheduler/state save 仍有独立提交；默认恢复保守停止 |
| 跨进程隔离 | Finance 图读取/summary/anomalies 真 AppContainer | 其他 Agent、旧 Finance/recurring 仍宿主；无 HSM |
| 因果证明 | 签名父证据、编译图、原金融阶段 hash、离线 CLI | 不证明隐藏载荷语义或外部银行签章；旧非图覆盖不完整 |
| 执行竞争/恢复 | 同 DB 所有权、只读两次尝试、结果缓存、正常重启停止 | 无分布式共识、金融 TTL 抢占或自动续跑 |
| 外部银行 | 已有官方沙箱同笔历史证据重新离线验签 | 本轮0新银行HTTP，生产资金/KYC未验证 |
| 公链 | 已有合约/RPC/本地EVM测试保留 | RPC/钱包/部署地址缺失，gas未知 |
| DID/ZKP | 原真实 did:key 与开发 Groth16 通过回归 | 本地信任根/单机setup，不是生产资质 |
| 运行可靠性 | 31专项、640全量、HTTP/锁竞争/1000审计 | 本机有限样本，非生产容量证明 |
