# TRUSTA × ANCHOR 最终集成验收

验收日期：2026-10-15。范围：现有 TRUSTA 银行只读多 Agent 工作流、ANCHOR 版本化适配、独立 FISCO BCOS 测试联盟链；不包含生产银行写入或新增公链交易。

**结论：测试联盟链原型闭环 IMPLEMENTED & VERIFIED；生产就绪性 PARTIAL。** 已真实完成 WeID 登记、受限 M1 Groth16 准入、M2 消息接收、Full-window OTC、确定性聚合、M3 链上写入、双节点 RPC 和公开证据独立复验。全量回归 **1284 passed，0 failed，0 errors，0 skipped**。原有安全核心未修改。该结论不等同于跨机构联盟链、生产级密码学初始化或任意模型推理零知识证明已经完成。

## 1. 研究与工程核对

已读取项目工程、接口、密码学交付、后端及 LLM 验收材料，并完整读取冻结正式论文及对应协议实现。主要研究来源：

`D:\ANCHOR_V21_EXTERNAL_20261004\validation_copy\ANCHOR_V21_ENGINEERING\10_V21_POSTSUBMISSION_CLOSURE_20261001`

正式 PDF 为 `submitted_paper/ToBeSubmitted_ANCHOR_V21_Final.pdf`，40 页，SHA256：

`10ae4cd94fc448309209e9e087a27ba97d5f828c985d4e827987dfb78910ecdb`

论文和 8 个协议源文件重新核对均未改变。桌面及 D 盘材料只读；旧研究链、旧钱包和数据库没有被接管或覆盖。

| 项目 | 核对发现 | 本次处理及结果 |
|---|---|---|
| 六 Agent、TaskGraph、安全执行、V3 通信、原审计 | 已有稳定实现，不应另建系统 | 直接复用，冻结源码未变 |
| WeID | 旧研究网关的随机 DID / 本地 JSON 不能证明真实 WeIdentity 登记 | 新增官方 SDK 适配，真实注册、解析、绑定和生命周期验收 |
| M1 | BANKING_ELIGIBILITY 不是能力准入；研究源清单中的部分新电路只有 compile-smoke，缺少真实 zkey/VK | 独立电路、Policy、VK、Evaluator 和真实证明；不重命名旧电路 |
| M2 | 旧验签不自动表达 Round / Epoch / M1 成员准入语义 | 在适配层增加上下文与准入检查，不修改 V3 核心 |
| OTC | 普通 DAG timeout 不具有 Full-window 语义 | 单独作用于明确成员和 k 的只读协作轮次 |
| M3 | 原银行审计与 Sepolia 存证不能自动证明 FISCO 协作决策 | 新增独立 CollaborationAnchor、轮次证明、链上读回及验证器 |
| 外部恢复 | SDK 输出丢失可能使提交结果未知 | 持久化回执日志、原哈希恢复、受限只读链上发现，禁止盲目重发 |

兼容性取舍明确版本化：研究信封采用 secp256k1，现有 TRUSTA 通信采用 P-256；研究 Merkle 规则与 TRUSTA RFC6962 不同。本次使用 `TRUSTA-ANCHOR-P256-V1`、`TRUSTA-RFC6962-V1`，**不是原协议字节兼容实现**。WeID 控制密钥与 P-256 消息密钥通过链上服务承诺及消息密钥签名绑定，未强制转换算法。

## 2. 新增实现与冻结边界

核心新增目录 `backend/collaboration/`：

- `weidentity.py`、`process.py`：官方 SDK 桥接、受限子进程、当前身份和消息密钥绑定验证；失败不退回 did:key。
- `evaluation.py`、`capability_proof.py`、`admission.py`：可信评测回放、独立真实 Groth16、Evaluator 签名、Policy / Epoch / 模型承诺绑定、撤销与到期检查。
- `rounds.py`、`verify.py`：复用 V3 的轮次接收、原子 nonce、防重放、成员快照、迟到及 VOID、固定证据集合与独立重算。
- `otc.py`、`otc_model.py`、`telemetry.py`：历史删失统计、受保护的参数模型选择、经验回退、预先固定观测范围及公开测量。
- `workflow.py`：连接原多 Agent GraphRuntime、既有 Capability / Gateway / SecurityExpansionCompiler / Handoff / Audit。
- `fisco.py`：固定测试网络边界、双 RPC 验证、合约与 Publisher 固定、Outbox、回执日志及恢复。

其他新增实现：

- `crypto_trust/anchor_capability_v1.circom`、`crypto_trust/anchor_proof_runner.mjs`。
- `contracts/CollaborationAnchor.sol`；与冻结 Sepolia AuditAnchor 分离。
- `integrations/weidentity/pom.xml`、`requirements-otc.txt`、`src/main/java/org/trusta/AnchorWeIdentity.java`。
- `scripts/initialize_anchor_network.sh`、`anchor_network_lifecycle.sh`、`prepare_anchor_weidentity.sh`、`anchor_weidentity_bridge.sh`、`anchor_sdk_cache.sh`、`anchor_sdk_diagnostic.sh`、`anchor_maven_mirror.py`、`build_anchor_capability.py`、`compile_collaboration_anchor.py`、`anchor_collaboration_acceptance.py`、`anchor_identity_lifecycle.py`、`verify_anchor_collaboration.py`、`finalize_anchor_evidence.py`、`benchmark_anchor_otc.py`、`audit_anchor_dependencies.py`。
- 8 个新增测试文件见第 7 节。产物及工具缓存均位于本次独立 `data/anchor-integration/`，不替换历史文件。

275 个原有源码文件和既有 LLM 冻结清单的 280 个文件均 SHA256 未变；全量回归期间固定的 181 个文件未变。无 Git 基线，因此不以目录差集冒充精确 Git diff。`source-change-inventory.json` 的早期目录差集包含已有脚本，不可将其计数作为本次新增文件数；以本节明确文件及源码锁定清单为准。

## 3. WeID + FISCO BCOS 实际验收

环境：Windows 11 / Python 3.13.1，WSL Ubuntu 22.04 / Java 11.0.31；FISCO BCOS 2.9.1，WeIdentity SDK 1.8.6-rc1、合约库 1.3.1-rc1、FISCO Java SDK 2.9.1。使用官方 `WeIdServiceEngineV2`；未为绕开高层 SDK 对无关合约的初始化要求而填充假地址。

新建 8 验证节点测试链，Chain ID **101**、Group **1**。RPC 9645–9652、TLS SDK 22400–22407、P2P 32400–32407，全部监听 `127.0.0.1`。实际共识及持久化来自 FISCO 节点，非 PyEVM。两个验证 RPC 分属两个节点，但在同一主机，不能称为独立机构。

WeID 合约：`0x638e02b7ae888222f92c3eeaf55cfa1de3e9f840`。

| Agent | 真实登记的 DID |
|---|---|
| Finance | `did:weid:101:0xa968103852c7eae1a245b77e98660730d8ccc3d3` |
| Wealth | `did:weid:101:0xa8fac68a926fb16debb085af40a1bb5d840df7de` |
| Transaction | `did:weid:101:0xf9fb420c820cf567b5250f816dd9a21c80f88c31` |

另用隔离 Service 测试身份实际完成：P-256 轮换后拒绝旧绑定、链上停用后拒绝、链上恢复后新 Epoch 绑定、服务重启后真实 SDK 读回、本地撤销不能被链上恢复绕过。证据：`data/anchor-integration/identity-lifecycle/result.json`。该测试身份最终保留本地撤销状态，不影响三个正式演示成员。

SDK 返回的原生 secp256k1 控制认证条目存在 `Ed25519VerificationKey2020` 元数据标注问题；本次没有按该标注解释为 Ed25519，也未将其作为 P-256 公钥。适配采用控制者实际合约状态及明确的 `TRUSTA-P256-Binding` 服务承诺。生产迁移必须验证兼容 SDK/文档格式，不能忽略此限制。控制密钥为本次新建开发密钥，加密并限制文件权限；未读取用户旧操作员私钥。

## 4. M1 / M2 / OTC / M3 的实际语义

### M1：受限统计关系，不是模型推理 ZK

`TRUSTA-ANCHOR-M1-STATISTICS-V1` 实际证明分数由范围受限 TP/FP/FN 按固定整数 F1 公式推导、承诺一致且达到阈值 800000；33 个公开信号绑定声明、模型证据、Benchmark、Evaluator、Policy、评分规则及评测转录。DID、消息密钥、Epoch 等包含在固定声明摘要并与外部签名逐项复核。Agent 不能自行声明分数获得准入。

本次真实编译、prove、verify 及负例验证通过。修复了 JavaScript 对 uint64 见证数字的精度风险，使用精确十进制字符串。准入记录 HMAC 封装；Epoch 不可回退；同 Epoch 仅允许相同绑定且未撤销的续期，撤销恢复必须新 Epoch、新证明及有效绑定。

可信评测使用冻结的 20 条 MiMo `mimo-v2.6-flash` 真实历史正常响应，重新校验原始文件 Hash、原 Gold 语义和独立执行证明，20/20；没有新增 API 调用。相同规则回放历史 DeepSeek 得 19/20，保留“执行一致但缺失用户目标”的失败，未把执行成功冒充意图完成。三个 Agent 分别绑定同一基准的准入证据，不声称完成三套角色专用新模型评测。

此电路不在约束中执行 LLM，也不从私有预测位向量重新推导全部统计；统计与模型来源依赖固定可信 Evaluator。新研究源中的更强位向量关系未被本次冒充为已证明。模型承诺是模型别名、Provider 和冻结证据的承诺，不是厂商权重证明。

M1 Manifest SHA256：`c2eaf2832a64732ce0198762bab03e661fe92a8f6f58dabb4762f2c21cb50051`。使用官方 Phase-1 文件 Hash 固定；完整代数 Phase-1 校验曾超时，日志保留，不计通过。新 zkey 验证及实际证明验证通过。Phase-2 是单机开发初始化，`production_ready=false`，不是生产 MPC 或可信废料销毁证明。

### M2：准入、签名、权限分别验证

原 V3 签名及 Capability 边界不变；接收新增 DID / 密钥 / Policy / Epoch / 模型 / Task / Round / 成员及时效绑定。先执行廉价原验签再调用外部身份检查；nonce、成员复核、证据与审计在数据库事务中处理。错误签名、跨轮次/任务/Epoch、撤销、错模型、重放均拒绝。链上身份或有效签名均不授予银行写入权限。

### OTC：Full-window 与历史信息隔离

实现 `cw*T + Cvc*Pr(Yk>T)`。达到 k 只进入 VIABLE；到 T 才冻结正式证据，少于 k 则 VOID，迟到消息只用于隔离遥测。预先固定的 H 观测范围不因当前结果改变；在线只读取当前轮次之前、相同群组/模型/Policy 的历史。服务重启不复用失效单调时钟继续收集。

删失 Lognormal MLE、Bootstrap 拟合检查、漂移/相关性/样本门槛及有限成本检查已作数值和负例测试；假设不满足时回到 KM/安全默认值。当前异质银行 Agent 没有被冒充为 IID 参数模型样本。Rolling/Static P95 来自实测遥测的反事实回放；Oracle 严格离线。

4 个实际轮次的 Deadline 为 120、120、30、120 秒；第三轮 k=2 在约 19.55 秒达到，正式仍在 30 秒关闭，第三成员约 29.04 秒纳入。最后一轮改变 Epoch 后冷启动回退 120 秒。控制器选择实测中位数 28650ns、最大 47000ns；只有 4 个本地样本，包含 SDK/协调开销，**不构成通用性能或 LLM 延迟结论**。完整数据：`otc-benchmark.json`。

### M3：真实链上决策承诺与恢复

固定排序、RFC6962 Inclusion Proof、聚合版本、Verdict、Round 元数据与成员快照共同绑定。原银行 Audit / Checkpoint 与协作证据关联；只读任务的付款 Receipt 标记 NOT_APPLICABLE_READONLY，不制造交易回执。

真实 SDK 的 bytes32 字符串编码问题由显式 Bytes32/Uint256 合约包装解决；最初失败记录保留。提交结果未知时通过 fsync 公共回执日志或限定 32 区块/256 交易的只读发现恢复原哈希；范围内找不到则拒绝自动重发。实际响应丢失/重启注入恢复到同一交易，前后链高度均 `0x17`、新增广播 0，详见 `recovery-evidence.json`。

## 5. 最新真实多 Agent 闭环证据

工作流实际调用原 Finance 读取/汇总、Wealth 建议、Transaction 余额查询及 Supervisor 汇总，5 个业务节点，未创建新 Agent。原 TaskGraph、Capability、Gateway、签名 Handoff、审计均参与。独立验证绑定实际执行任务、输入、结果及原运行时证据，而非凭网页状态判断成功。

最新验收目录：

`data/anchor-integration/acceptance/b086f1bf-0154-478e-b03b-f7589b1db224`

| 字段 | 实际值 |
|---|---|
| 业务 Task | `705ef72f-e6d4-4c67-b1f8-73dc1c850adf` |
| Round | `231d07ef-4d53-4586-877e-207bc6289296` |
| 状态 / Verdict | COMPLETED / CONSISTENT_READONLY_EVIDENCE |
| 正式有效证据 / k | 3 / 2 |
| Deadline / 关闭原因 | 120 秒 / DEADLINE |
| Record Hash | `8d728e9ae8cb54eb4320936665cf4d01900104ba8fd36633a8496a4fed9bc0f2` |
| Merkle Root | `9429ba25c67a3f414473f53987040d5224e203a447bffc79547d41b16c99bcc7` |
| Snapshot Hash | `146d74ed42688a072f35053922ad268f77d6f261f3461ff2fb52dd8788a35a9a` |
| 合约 | `0xd9efa8a9e121fb664ba4087e20adc766704eaf83` |
| Publisher | `0x070fa0c9e4edac61242f70b6f37a2a91eef32520` |
| 部署交易 | `0x75f284193efc9566d7aeb6a804bbb6c63b98ef6755e54a598014be0cce377d1f` |
| Root 提交交易 | `0x3b69e8163aaeb90495f5fd7d987e30238a0431bc71bae6eb756c9c1df20cdafb` |
| 提交区块 / Gas Used | 27 / 156826 |
| 部署 Gas Used | 406178 |
| Runtime Code Keccak | `d4fd07ccdf0c6c89e03e4184a2eeb9838a256e047e49da2d45424ae8f76d08d0` |
| 回执状态 | `0x0`，FISCO 成功语义，不能误用 Ethereum status=1 |

9645、9646 两节点分别验证合约代码、Publisher、部署输入及回执、提交 calldata、事件、Root 读回、规范区块 Hash、成员身份、M1、M2、原审计与业务因果关系。全量回归后重启原测试链并再次验证通过，没有新交易。

## 6. 独立公开复验与交付包

`data/anchor-integration/ANCHOR_Public_Verification_Package.zip`，43378 bytes。

SHA256：`08e6f87d3cc8d86a38ea5fd1b98ef1441f85851f25648d3d3aeb2f13b37bd22d`

含 14 个固定 Hash 的公开文件及包清单：轮次/业务/FISCO/审计证明、公开信任锚、验证参数、遥测、M1 公共 VK / Manifest、合约产物及源电路。**不含数据库、私钥、口令、API Key、proving key、ptau、witness 或 wasm。** 不以本地数据库中成功标志代替外部验证。

回归后的第二次公开包复验使用最小环境，不传递模型 API Key；Python 文件访问守卫禁止读取 secrets、business、私钥、数据库和证明生成文件，结果 exit=0、verified=true。日志及完整结果：

- `data/anchor-integration/post-regression-public-verification.json`
- `data/anchor-integration/post-regression-public-verification.log`
- `data/anchor-integration/final-readonly-check.json`

第三方仍需要项目公开验证代码、snarkjs 环境及经授权的两个 RPC 读视图（本机或受控隧道）。信任键、合约、Publisher、部署交易、Record/Snapshot Hash 必须从独立可信渠道固定；不能因为包内自带公钥就自行建立信任。本地双 RPC 不是无须信任的公链浏览器证明，也没有证明协调者永远不遗漏未提交消息。

## 7. 测试、安全与完整性

最终命令对应完整 pytest 全集；终端 **1284 passed in 1452.10s**，JUnit suite time 1452.024s。1158 项旧测试全部保留，新增 126 项；无删除、跳过或放宽旧断言。

| 新增测试文件 | 数量 | 范围 |
|---|---:|---|
| test_anchor_admission.py | 19 | 准入、撤销、Epoch 单调性、恢复 |
| test_anchor_capability.py | 19 | 电路契约、真实 Groth16 正负例、仅 VK 验证 |
| test_anchor_collaboration.py | 32 | M2、重放、截止、VOID、并发/封存 |
| test_anchor_evaluator.py | 4 | 原真实响应回放、Gold/Hash 篡改、语义失败保留 |
| test_anchor_fisco_boundary.py | 13 | 真实记录回放负例、链/合约/Publisher 固定与恢复边界 |
| test_anchor_protocol_extended.py | 21 | OTC、删失、模型门槛、故障与证据边界 |
| test_anchor_workflow.py | 8 | 原多 Agent 只读工作流和公共因果证据 |
| test_collaboration_contract.py | 10 | 真实编译字节码本地执行与拒绝路径 |

单元测试中的替身、真实记录回放及本地合约执行分别标识；真实 FISCO 结论来自第 5 节 SDK / 共识节点 / RPC 实际验收，未用 mock 成功替代。

最初基线为 1157 通过、1 项旧 Windows HTTP 测试 Win10053 失败；原样单独复跑通过。中间因新增源码完善而中止的全量日志和旧 Receipt 测试孤立重验日志均保留，不计最终通过。最终固定源码的完整运行无失败，没有因此修改旧金融模块。

完整日志：`release-full-regression.log`、`release-full-regression.xml`；专项、真实证明和所有中间失败日志均保存在 `data/anchor-integration/`。

静态审查：Ruff F/E9 为 0；Bandit 6 个 LOW 均为受控 subprocess 调用，经固定命令、绝对系统执行路径、无 shell=True、输入边界及超时审查，未隐藏扫描结果。Java 编译、Solidity 编译及 Python 编译检查通过。

依赖审查：114 个 Java runtime jar 固定 Hash；88 条嵌入 Maven 元数据查询得到 29 个有 OSV 公告匹配的包，**不是 29 个已确认可利用的应用漏洞**。SDK 日志关闭、JSON 有界、默认多态关闭、Java 通道仅本机 TLS；未暴露不受信 YAML、Redis 或代码生成入口。元数据缺失/着色 jar 仍有覆盖限制。冻结 npm 依赖审计为 15 LOW、0 中高严重项；本次未擅自升级不兼容 SDK 或修改原锁文件。

最终只读数据库检查：transactions、transfers、ledger_entries、actions、authorizations、execution_contracts、bank_operations、investment_transactions、llm_calls 均为 0；collaboration_rounds 为 4。新模型调用 0、真实银行写入 0、公链广播 0；本次真实写入仅发生在新建免费 FISCO 测试链。

## 8. 最终状态与限制

| 模块 | 结论及边界 |
|---|---|
| WeID + FISCO | IMPLEMENTED & VERIFIED，真实隔离测试链及生命周期 |
| M1 | IMPLEMENTED & VERIFIED，受限统计证明及可信历史评测来源绑定 |
| M2 | IMPLEMENTED & VERIFIED，原 V3 与新增协作语义组合 |
| OTC | IMPLEMENTED & VERIFIED，经验分支实测、参数分支数值/故障测试；真实参数拟合效果 NOT_RUN |
| M3 | IMPLEMENTED & VERIFIED，真实合约提交、回执、读回、恢复及双 RPC |
| 原银行多 Agent 业务与审计关联 | IMPLEMENTED & VERIFIED，只读真实运行时流程 |
| 公开独立证明 | IMPLEMENTED & VERIFIED，需要预固定信任锚和 RPC 访问 |
| 新付费模型评测 / 任意推理 ZK | NOT_RUN / 未声明支持 |
| 生产部署 | PARTIAL，不将开发验收包装成生产认证 |

生产差距具体为：单机八节点不具跨机构独立治理；单机 Phase-2 不是生产 MPC；旧 SDK 兼容元数据与依赖公告需经受控升级验证；模型别名/历史证据不等于可信厂商快照；参数 OTC 的真实统计效果尚无足够样本；协调者接收时间、完整性及可信 Evaluator 仍是显式假设。没有以外部依赖掩盖已知可修复内部故障；测试覆盖通过也不能证明不存在未知漏洞。

## 9. 运行与复核入口

从项目根目录启动本次专用测试链并保持终端运行：

```powershell
wsl -d Ubuntu-22.04 -- bash scripts/anchor_network_lifecycle.sh run
```

只读复核已存在的最新证明，不签名、不上链：

```powershell
python -m scripts.finalize_anchor_evidence --run data/anchor-integration/acceptance/b086f1bf-0154-478e-b03b-f7589b1db224
```

此导出器读取隔离业务库；它启动的独立验证器不读取业务库。需要明确验证“只有公开包也能复验”时，使用 `data/anchor-integration/final-readonly-check.py`；该检查自动从公开目录运行验证器并启用访问守卫，不创建新业务或交易。第三方直接使用 `scripts.verify_anchor_collaboration`，参数由经独立固定的 `evidence/verification-arguments.json` 提供。

产生**新的**只读业务轮次并提交到此专用测试链（不是历史证据复核）：

```powershell
python -m scripts.anchor_collaboration_acceptance --reuse-admission
```

准入过期、撤销或模型/Policy 不符时必须拒绝；不绕过检查。需要重新评测准入时，去掉 `--reuse-admission`，默认流程回放固定历史响应并生成新证明，不调用付费模型。历史证据保留。网络停止入口为 `anchor_network_lifecycle.sh stop`。不要对旧研究链运行这些脚本。

最终机器验收摘要：`data/anchor-integration/final-acceptance.json`。本次交付到此结束，不修改已冻结 LLM、银行执行或 Sepolia 系统。
