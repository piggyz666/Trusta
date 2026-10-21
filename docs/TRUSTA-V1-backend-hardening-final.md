# TRUSTA V1 后端深化交付（2026-10-08）

本轮已完成当前可验证范围的架构审查、代码开发、真实双模型评测、进程隔离、因果证据、攻击/恢复测试及全量回归。原六 Agent 与金融安全链保持；没有 UI 改动或新 Agent，没有新增银行付款。**LLM 可用性、全 Agent 进程隔离与外部 Sepolia 仍有明确限制，不能称为生产就绪。**

## 审查与决策

完整阅读两份用户材料，先检查源码/配置/环境/实际测试再设计。普通同用户 subprocess 不足以隔离密钥，因此使用实际 Windows AppContainer；可信 broker 位于宿主，不复制全部 Core 到子进程。原 schema 与金融安全规则不放宽，compact_v2 只减少模型重复元数据。拒绝用签名/包含证明代替银行事实核验。历史指标生成器的疑点经源码排除，没有照搬报告或重新修改已正确逻辑。

本轮没有冻结架构重大冲突。V3 原子接收补丁、Finance transport、handoff evidence 与数据库 graph claim 为兼容附加模块。原 Domain、Policy、Authorization、Execution Contract、Gateway、HardVerifier、Receipt、TaskGraph Validator/Compiler 文件 SHA-256 与修改前一致。

## 新目录与修改

```text
backend/llm/compact_graph.py
backend/runtime/finance_transport.py
backend/runtime/finance_worker.py
backend/runtime/windows_container.py
backend/runtime/handoff_evidence.py
backend/runtime/graph_claims.py
benchmarks/hardening_holdout.py
benchmarks/author_hardening_final_holdout.py
benchmarks/backend_hardening_benchmark.py
benchmarks/backend_*holdout_20261008.json / .sha256
scripts/verify_handoff_evidence.py
scripts/backend_hardening_evidence_review.py
scripts/backend_hardening_delivery.py
tests/test_backend_hardening.py
tests/test_backend_recovery.py
config/llm.compact.example.toml
```

已有修改文件：`backend/config.py`, `backend/service.py`, `backend/llm/configuration.py`, `backend/llm/graph_proposal.py`, `backend/runtime/graph_runtime.py`。完整文件清单、前后 hash、环境、依赖和输出 hash 见 backend-verification-manifest.json。项目没有 Git 元数据，本轮采用 SHA-256 和原始日志交付，没有虚构 Git commit。

数据流详见 backend-trust-boundaries.md：用户 → Supervisor → Gateway/Router/可替换模型 → 不可信候选 → 原 Validator/grounding → Compiler → Blackboard/Scheduler → 六 Agent。Finance 子进程只经固定 broker 读取与分析。所有金融写继续为 BankActionIR → Schema/Capability → Policy → Authorization → Contract → Gateway → Adapter → HardVerifier → Receipt/Audit。

## 实测结果

修改前真实基线：609项 / 42模块 / 220.575秒。最终真实全量：**640项 / 44模块 / 188.455秒 / 0失败 / 0跳过**。原44个测试文件字节一致；新增31项。每模块隔离测试进程、4并发，完整原始输出 backend-hardening-full-tests.txt；不是 Agent 隔离证据。

通信/恢复专项31项通过。真实 Child 的 key/DB/source/Adapter/宿主内存/本机网络/staging写访问被拒绝；签名/主体/版本/父引用/replay/存储篡改、撤销/轮换、崩溃、ACK丢失、bounded read retry、整段IO超时与无宿主回退均核对。并发同一授权8次只有1次成功、7次拒绝，生成1张回执。默认 Core 重启不自动恢复运行中的金融任务。

既有六场景基准34/34，预期完成的23/23，跨场景2/2；银行攻击45/45阻止、未授权状态变化0/45。Phase4/5通信基准各自伪造10/10、篡改10/10、重放5/5、审计篡改10/10、Merkle30/30，有限样本未授权执行0/15。均为实际本轮重跑，不是历史数字复述。

可靠性：8次实际独立 Finance 图冷启动往返 p50 3310.093ms / p95 3637.435ms；8并发客户端80请求包含20跨客户拒绝，80/80符合预期，p50 55.710ms / p95 236.325ms。1000条新增审计+检查点耗时 10002.280ms，回执证明查询/核验20次 p50 41.343ms。SQLite另一连接在1秒写锁后成功完成。全部原始采样留存；p95采用样本排序 floor(n*0.95) rank；小样本不外推生产p99/SLA。

真实模型最终两组各120例：DeepSeek合法47/72、全例93/120、缺字段22/24、攻击24/24；Gemini合法58/72、全例96/120、缺字段14/24、攻击24/24。均未授权银行写0。原始响应不等于正确完成；合法待审批也不算付款完成。第一批较低结果全部保留，开发集与最终gold分离，没有依据最终gold反复调分。详见 llm-holdout-evaluation.md、两组完整JSON/CSV与 llm-holdout-summary.json。

## 外部证据和创新范围

已有 Revolut 官方沙箱历史20/20读回记录的回执证明本轮固定公钥验证通过，task/tx/receipt一致；原 MANUAL_REVIEW和后续 VERIFIED均保留，单笔付款claim仍1。本轮银行HTTP0、生产资金操作0。历史证据重验不冒充新银行实测或银行签章。Sepolia缺3个配置，未发链交易；状态单列BLOCKED_BY_EXTERNAL_CREDENTIAL。已有 did:key/Groth16实际测试纳入全量，没有改授权边界；单机开发setup和本地issuer不称为生产密码信任体系。

## 运行、迁移与Demo

默认 full_v1、Virtual Bank 和单进程兼容；新增表/index/triggers按需 CREATE，无删除旧表、数据或nonce，没有迁移旧回执。启用Finance隔离需 Windows/Python3.13与 `TRUSTA_FINANCE_PROCESS_ENABLED=true`；无配置则保持原路径并不得宣称进程隔离。超时默认20s，可配置1–120s。新增宿主staging ACL和AppContainer profile仅用于独立Finance运行资源。新模式回滚先停止相关服务再关闭开关；保留交付/证据/nonce记录，不能清库重发未知付款。

模型示例 config/llm.compact.example.toml 为shadow/public且无密钥，客户敏感请求没有未经审查的数据政策便不路由至外部模型。改模型在Provider/Router配置完成；任意未知HTTP协议仍需相应适配器，没有固定绑定DeepSeek或Gemini。

演示：`python -m benchmarks.backend_hardening_benchmark` 在临时Virtual Bank执行 Finance READ → Summary → Transfer提案 → 独立逐笔确认 → 原全金融安全链 → Receipt → Report，并导出签名因果包。随后 `python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json` 离线验证。本轮两步均真实成功，演示registry须独立固定才形成可信根。六业务场景的实际任务基准 `python -m benchmarks.banking_task_benchmark --output docs/operator-task-run.json`。没有自动调用真实银行或定期模型任务。

## 安全评估与遗留限制

高优先级：LLM仍漏依赖/子任务或错误澄清，合法任务与缺字段质量未达成熟产品指标；下一轮需独立业务人员标注、预先设定成功门槛与新盲样本，不复用最终gold调分。跨进程只覆盖Finance图三类读取和两类分析，其他业务Agent与可信host密钥仍同宿主，未提供HSM/KMS、Linux容器、多主机互信或所有写路径fencing。

证据是历史Runtime声明与审计包含性，不独立证明隐藏业务语义、最新key状态、银行签章或公链。旧非图协作证据覆盖不完整。默认重启不自动续跑；graph claim没有TTL抢占，终态claim维护要先停止旧worker。没有电源中断/磁盘损坏/长期生产压力实验。模型攻击与本机隔离为受控样本，不能宣称100%安全。外部bank/chain缺项不以Virtual/本地EVM代替。

交付下一顺序：扩大独立LLM标注和多轮澄清、为剩余Agent扩展同样窄的进程边界、受控运维恢复与容量验证、具备可信配置后Sepolia独立read-back/真实外部授权证据；保持本轮后端边界，再进入UI/产品形态设计。
