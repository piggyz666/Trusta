# TRUSTA

2026-10-08 后端深化：当前重新实测基线609项；新增31项后全量640项通过，原测试文件保持字节一致。新增兼容的紧凑LLM提案、Finance图节点的真实Windows AppContainer隔离、持久化只读交付、原子V3接收与签名因果证据。真实DeepSeek/Gemini分别完成独立120例评测；合法任务命中47/72与58/72，不能称为任意语言生产就绪。原六Agent与金融安全链保留，没有UI改动或本轮银行付款。架构、原始数据、Benchmark、迁移、Demo与剩余限制见[最新后端交付](docs/TRUSTA-V1-backend-hardening-final.md)、[可复核清单](docs/backend-verification-manifest.json)、[真实模型评测](docs/llm-holdout-evaluation.md)。默认兼容原单进程；Windows/Python3.13下设置 `TRUSTA_FINANCE_PROCESS_ENABLED=true` 启用已验证的Finance范围，其他Agent尚未完成OS隔离。下文各阶段记录保留历史，不覆盖本段。

2026-10-08 第一版后端已补日累计风险、真实 TOTP、持久化金融锁、人工核查案件、年度账单、卡片申请记录/挂失、续费提醒及受控事件。DID 已实现 P-256 did:key 与 Registry 绑定；ZKP 已实现真实 Groth16 生成/验证，使用明确标记的单机开发初始化，不能用于生产安全承诺。六场景 Demo、当前目录/API/配置与边界见[第一版后端交付](docs/first-version-backend-delivery.md)，全量结果见[当前验证](docs/first-version-backend-validation.json)。未改 UI；外部 Sepolia 仍待测试钱包/RPC/gas，不冒充已上链。下文 Phase 文档保留历史状态，不能覆盖本段当前状态。

后端安全接口见[backend-safety-api.md](docs/backend-safety-api.md)。本地入口默认 competition；历史 Python 配置默认 legacy。启用 competition 的数据库禁止静默降级。新证明能力默认关闭，使用自己构建的资产并固定 manifest hash 后才启用。

Phase 6 基线已完成本地实施与验证，**当时449项全量测试通过**，原371项测试文件保持不变。新增 Canonical Bank SDK、Starling Reference Adapter、多人 AA 收款、闭合 TaskGraph/确定性安全编译器、Gemini native Provider 与外部审计合约/RPC Adapter。真实 Gemini 已调用；真实银行和外部测试网仍因凭据/配置缺失而未验证。目录、数据流、修改清单、20项交付、Demo与真实性矩阵见 [Phase 6 交付](docs/phase6-final-report.md)和[验证记录](docs/phase6-validation.json)。

后续分步收尾：[评测统计修正](docs/phase6-report-metrics-repair.md)、[TaskGraph输出与澄清改进](docs/phase6-taskgraph-usability.md)。后者在新24例配对实测中，正常只读完成从5/10变为10/10，金融提案停在审批从1/2变为2/2；缺参澄清3/4。原100例意图评测保留，不能把图评测结果当作整体自然语言成功率。该轮全量日志见[TaskGraph回归](docs/phase6-taskgraph-tests.txt)，最终状态见[专项验证](docs/phase6-taskgraph-usability-validation.json)。

后续已完成[第二供应商 DeepSeek 实测](docs/phase6-second-provider.md)：复用冻结24例场景回归，DeepSeek真实返回24次，全部达到预期；7项双向路由与回退检查通过，回退故障为明确的本地注入。金融提案继续等待审批，模型评测资金写入0。最新全量回归与可复核状态见[日志](docs/phase6-second-provider-tests.txt)及[验证](docs/phase6-second-provider-validation.json)。这不替代原100例意图评测，也不表示银行Sandbox、外部链或DID/ZKP已接通。

Phase 5 的六 Agent、智能转账、账单、理财申购/赎回、卡片、订阅/代扣、跨场景 DAG、逐项授权和 TIME worker继续保留。历史交付见 [Phase 5](docs/phase5-full-banking.md)。Web UI仍以原三个场景为主；AA与通用图通过API及实际HTTP/模型Demo验收。

Phase 4 的 ECDSA 消息 V3、持久化密码身份与能力绑定、签名 Merkle 检查点、单记录证明、独立公钥复核和本地异步锚定继续保留。18类安全攻击在旧配置与Phase5配置回归。历史实测与迁移见 [Phase 4 交付](docs/phase4-cryptographic-trust.md)。DID/ZKP保留接口；外部链Adapter与合约已完成本地验证，尚未连接测试网。

本地持久化虚拟银行沙箱。V1 转账闭环保留；Phase 2 增加 Finance 消费分析与 Wealth 只读理财推荐。输入“给张三转 2000 元”，核对并确认后，执行记账、重新读取银行状态、生成回执和可验证审计哈希链。

Phase 3.1 增加模型无关 LLM Gateway、Provider Interface、Model Router 和影子候选结果，默认关闭。详见 [接入与安全边界](docs/phase3-model-agnostic-platform.md)。候选结果不参与审批或金融执行。

Phase 3.2 增加持久化 Conversation、多轮补充、受来源校验约束的意图候选、模板计划校验和有据说明。页面支持“帮我给张三转账 → 两千元”，聊天确认不能替代授权卡；修改金额或收款人必须重新生成动作和审批。详见 [Phase 3.2 实现与 Demo](docs/phase3.2-conversation.md)。

Phase 3.3 增加明确同意的说明偏好 Memory、会话 / 风险档案引用、V2 消息与密钥版本轮换、统一 Registry 查询和默认关闭的扩展接口。Memory 只影响后续说明，不提供金融事实或授权。详见 [Memory 与通信交付](docs/phase3.3-memory-communication.md)。

## 启动

需要 Python 3.11 或以上版本；先安装密码学运行依赖：

```powershell
python -m pip install -r requirements.txt
```

Phase 6 全量测试还需要可选 EVM/外部审计依赖：`python -m pip install -r requirements-phase6.txt`。普通本地银行运行仍可仅安装原运行依赖。

在项目目录运行：

```powershell
.\start.ps1
```

启动脚本默认使用 Phase5/V3，并在隐藏后台进程运行独立审计 worker，退出时清理该 worker。也可运行 `python -m backend.server`，再在另一终端运行 `python -m scripts.audit_worker --watch`。需要执行定时任务时，在服务启动完成后另开终端运行 `python -m scripts.scheduled_worker --watch`；只运行一个TIME worker，停止/升级服务时一起停止。打开终端显示的完整本地链接。链接中的令牌用于本地客户身份验证，页面将其存入当前浏览器会话并移除地址中的令牌。服务仅监听 `127.0.0.1:8000`。

升级前停止旧服务与全部worker，备份数据库、原 `data/signing.key`、令牌与数据库旁的加密 `.keys/` 目录。升级保留旧动作、合约、回执和审计哈希；V3数据库禁止退回V2，Phase5数据库禁止退回banking_version=4。五Agent到六Agent的能力承诺升级只接受已知旧配置。密钥丢失不会静默重建；不要替换 signing.key 来轮换 Agent 密钥。详见 [Phase5迁移](docs/phase5-full-banking.md)与 [密钥管理](docs/phase4-cryptographic-trust.md)。

首次启动自动初始化：演示客户 `demo`、账户 `acct-demo`（10,000.00 元）、张三（已有收款人）、李四（新收款人）、结算账户。已有数据库不会因启动而重置。`data/` 保存银行、任务、审计、幂等键、防重放 nonce、签名密钥和本地访问令牌；不要将该目录提交或公开。

每笔转账都需要明确确认。competition 后端按同用户人民币日累计超过1,000.00元要求认证器的一次性验证码，外币始终要求强验证；绑定与授权接口见后端安全 API。当前网页仍为原演示界面，尚未接入认证器绑定流程，不能宣称新安全配置已完成网页验收。历史隔离演示可显式选择 legacy：单笔达到5,000.00元时使用 `TRUSTA_STRONG_AUTH_SECRET` 演示口令；它不是生产 MFA，不能用于 competition 强认证。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m scripts.demo
python -m scripts.scenario_demo --output docs/phase2-demo.json
python -m scripts.phase3_demo --output docs/phase3.1-demo.json
python -m scripts.phase3_conversation_demo --output docs/phase3.2-demo.json
python -m scripts.phase3_security_demo --output docs/phase3.3-demo.json
python -m scripts.phase4_demo
python -m scripts.phase5_demo
python -m benchmarks.banking_task_benchmark
python -m benchmarks.provider_evaluation
python -m benchmarks.banking_security_benchmark
python -m benchmarks.security_benchmark --banking-version 5 --output docs/phase5-security-benchmark.json
python -m benchmarks.security_benchmark --output docs/phase4-security-benchmark.json
python -m scripts.verify_audit_record docs/phase4-record-proof.json --trusted-public-key docs/phase4-audit-public.pem
```

演示脚本通过实际 HTTP 接口，对独立的 SQLite 银行执行真实转账，并核对余额、借贷账本、银行状态、回执、审计链和服务重建后的持久化结果。它不会改变日常演示数据库。

## 已实现的边界

- Intent Normalization 是非 Agent 接入层；输出结构化任务输入后交给 Supervisor。
- Phase5六个活跃Agent：Supervisor、Planner、Transaction、Finance、Wealth、Service；旧Python配置保留Phase4五Agent用于历史兼容验证。
- Agent 只能读取、分析、提案；执行主体为确定性 `executor`。
- Blackboard保存权威任务状态与独立动作/授权/核验/回执集合，Scheduler控制依赖、并行只读分支与TIME等待，Bus发送引用事件。
- 金融写链路：BankActionIR → Schema/Capability 检查 → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → Virtual Bank。
- 执行后重新读取权威银行状态，只有核验通过才能生成成功回执。
- 不确定执行或核验进入 `MANUAL_REVIEW`，没有自动金融写重试。
- 正常启动使用 ECDSA 消息 V3：密码身份、能力承诺、任务 / 引用情境、payload hash 与持久化 nonce；只在验证完成后接受消息。历史 Python Config 调用保留显式 V2 兼容路径，V3 不接受算法降级。Agent 消息轮换不改变执行合约密钥。
- 原审计哈希链继续保留；后台签名 Merkle 批次和记录包含证明可独立复核。Local Development Anchor 的 CONFIRMED 只表示本地核对，不是链上确认；证明中公钥必须由验证者通过可信渠道固定。

## 实现阶段与目录

V1 原实现顺序：领域模型和 Virtual Bank → Runtime → Trusted Execution → End-to-End → Frontend。后续业务场景 Phase 2、模型无关平台 Phase 3、密码信任 Phase 4 的编号见各交付文档。

`backend/security/` 包含 identity、signature、capability、replay_protection；`backend/execution/` 包含 contract、gateway。Schema、Policy、Authorization 是独立职责文件；不存在 `trusted/` 大模块。

详细说明见 [架构](docs/architecture.md)、[API](docs/api.md)、[假设与限制](docs/assumptions.md)、[验证记录](docs/validation.md)。原始规格保留在项目根目录。

Phase 2 的目录、数据流、权限和三个Demo见 [场景扩展](docs/phase2-scenarios.md)。新增请求示例：“分析2026-09消费”“推荐适合我的理财产品”。这两类任务通过原网关读取，不发起金融操作。

## 范围与限制

当前业务事实、调度和执行仍为确定性沙箱实现。推理默认关闭，可配置为 shadow 或 assisted；Phase6完成100条合成数据的真实Gemini评测及实际Gateway/Router语义图运行，OpenAI凭据验证失败。原始模型与系统校验结果分别统计，不能把拒绝当作模型正确。Conversation仍支持有限句式和来源可校验的补充；缺失/歧义字段澄清，模型不能补造关键字段或授权。Memory仅保存明确同意的有限偏好与来源引用。金融资金写始终经过原安全链。

SDK默认Virtual；Starling显式配置、GBP原生、写默认关闭，参考协议通过本地测试但尚未调用官方sandbox。Revolut新增显式Sandbox Adapter和OAuth刷新，真实账户/交易、Finance GBP月度分析与TaskGraph均已验证；商户语义分类尚未映射，显示未分类。一笔逐笔授权的0.01 EUR官方Sandbox付款已通过模拟结算、独立读回和原Core补充回执；生产银行资金执行、AA收款与跨进程共享银行限流未实测。外部锚定合约/RPC完成本地EVM和证明测试，尚缺Sepolia RPC/钱包/gas/部署地址。DID/ZKP、生产身份/KYC、生产密钥隔离仍不是已完成能力。基准为受控本地观察，不能外推生产安全率。

2026-10-07：独立 Revolut 官方 Sandbox 接入工具已完成真实用户授权、RS256 client assertion、token 交换和 `/accounts` 读取，返回5个账户。该工具仅请求 READ，不调用资金接口；它尚未作为 Bank Adapter 接入 Agent/Trusted Execution。银行未返回 token scope 字段，因此不把请求 READ 等同于已核验授权范围。接入流程见 [说明](docs/revolut-sandbox-bootstrap.md)，实测边界见 [验证记录](docs/revolut-sandbox-connect-validation.json)。

2026-10-08：已完成Revolut只读Core接入，Finance与TaskGraph通过原Gateway/Capability/V3消息/Audit读取真实Sandbox；5条GBP记录中3条消费净支出272.50 GBP，2条充值排除。没有银行资金写入或LLM调用，原安全架构与默认Virtual前端保留。现有凭据可自动刷新；实际权限字段仍未由token响应返回。[运行与边界](docs/revolut-readonly-integration.md)、[实测](docs/revolut-readonly-validation.json)。

2026-10-08 后续：前端账户选择、原生币种余额、交易读取与账单报告已在官方 Sandbox 实测。新增 opt-in 小额 GBP/EUR 付款 Adapter、永久幂等登记及只读补充核验，继续使用原逐笔授权和 Trusted Execution，默认 Virtual 保留。`./start-revolut.ps1` 启动独立银行 UI；付款需先完成 READ,PAY 银行同意，然后 `./start-revolut.ps1 -Transfers` 只准备 0.01 提案，仍须用户单独确认。[运行说明](docs/revolut-banking-integration.md)、[最终真实性与测试状态](docs/revolut-banking-validation.json)。

2026-10-08 逐笔确认后：向 Acme Corp. 的一笔 0.01 EUR 已通过原可信执行链被官方 Sandbox 接受，独立读回各字段匹配，银行仍为 PENDING；无成功回执。独立测试工具尝试推进同一交易的模拟结算时被银行 403 拒绝，没有重发付款或增加 Agent 权限。本次新增 7 项专项测试通过；结算完成仍待银行侧证据。[读回记录](docs/revolut-pending-readback.json)、[模拟尝试](docs/revolut-sandbox-settlement-validation.json)。

2026-10-08 最终：独立操作员试验授权后，同一笔官方Sandbox交易模拟完成，原Core独立读回VERIFIED并生成唯一成功回执。20项银行/回执检查、本地固定公钥的审计签名与包含证明通过；13项操作员专项测试及实际完成页面验证通过。付款仅1笔，原待核查历史保留，前端显示补充核验通过。属于官方沙箱模拟结算，未执行生产清算。[最终银行读回](docs/revolut-settled-readback.json)、[回执证明](docs/revolut-transfer-receipt-proof.json)、[运行说明](docs/revolut-banking-integration.md)。

2026-10-09 定向后端优化：原金融执行与六 Agent 架构保留，完成 compact_v2 schema/工具语义、最多一次提案重规划和后端槽位续接；重新运行原640项及新增27项，667项全量通过。真实模型最终一次验收中，DeepSeek合法任务70/72、澄清24/24；Gemini合法任务49/72、澄清22/24，合法任务目标尚未达到。所有恶意越权输入均未触发银行写。新增只读独立 Anchor 验证器，但 Sepolia RPC/受控钱包/合约仍缺，外部链未验证。本轮没有新增 UI 或银行/链上资金操作。[完整交付](docs/targeted-final-report.md)、[模型比较与原始证据](docs/llm-final-comparison.md)、[外部链阻塞](docs/external-anchor-blockers.md)。
