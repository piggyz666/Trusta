# TRUSTA Phase 6 最终交付

本地实施与回归完成；**真实银行及外部链尚未 live 验证**。六 Agent、Blackboard/V3、CapabilityRegistry、BankActionIR、Policy/Authorization/Contract/Gateway/HardVerifier/Merkle 原边界保留，没有模型直连 Bank API，也没有新增 Agent。

## 架构与技术决策

1. Provider 替换在 SDK/Adapter 边界；历史签名 grant 使用兼容别名而不更改承诺。Virtual 默认；Starling 显式配置、GBP原生，缺失能力不得合成本地银行事实。
2. 不确定外部写和已知回滚错误分离；幂等先落盘，银行HTTP成功不等于SETTLED，独立状态核验才发回执。外部写默认关闭并逐动作审批。
3. AA 是收款工作流：3个独立身份/账户/任务/审批/回执与实际借贷入账；Organizer不能代扣。独立本地身份不等于生产身份/KYC。
4. 语义图由模型或受控输入提出；validator/编译器固定展开金融链，运行在原 Blackboard/Scheduler。WAIT_USER选择不是金融审批；shadow只观测、不安装。状态HMAC、重编译、同任务串行与真正并行只读保护恢复。
5. Anchor只在异步审计worker；EVM合约只存commitments；tx/receipt/chain/contract/event/read-back/confirmations/reorg全核验，不进入银行主路径。
6. Gemini native Provider 通过已有 Gateway/Router接入，模型与endpoint配置可替换；没有保证任意未知协议即插即用。实际只验证了Gemini，OpenAI认证失败。

## 新目录与数据流

```text
backend/bank_sdk/       contracts, auth, transport, configuration, virtual, bridge, starling
backend/banking/split.py
backend/runtime/        taskgraph.py, graph_runtime.py
backend/llm/            graph_proposal.py, providers/gemini.py
backend/audit_trust/    external_anchor.py
contracts/             AuditAnchorContract.sol/.json
config/                bank.starling.example.toml, llm.gemini.public-example.toml
benchmarks/             phase6_llm_cases.tsv, phase6_llm_evaluation.py,
                       phase6_llm_postprocess.py, phase6_planning.py, phase6_planning_gold.json
scripts/               phase6_demo.py, phase6_external_validation.py, phase6_gateway_live.py,
                       deploy_audit_anchor.py, taskgraph_worker.py, phase6_reports.py
tests/                 test_phase6_sdk/split/graph/proposal/gemini/http/anchor.py
docs/                  20项指定交付及补充证据
```

User → Conversation/明确输入 → Supervisor草稿 → LLMGateway/Router/Provider → 未可信语义图 → Validator/Gateway资源归属核验 → Compiler → Blackboard/Scheduler → 原六Agent → BankActionIR → Schema/Capability/Policy/Authorization/Contract/Gateway → SDK/Bank → HardVerifier → Receipt/Audit。

AA每一参与者从自己的普通TRANSFER任务进入同一金融链；已核验信用ledger实际指向Organizer收款账户。Audit → checkpoint/outbox → 独立worker → Anchor，与资金链分离。

## 测试与实测

- 全量 **449 项 / 341.137秒 / OK**；原371项保留，22个原测试文件字节哈希完全一致。补充检查分别29/24/7项通过。见 phase6-tests.txt、phase6-change-manifest.json。
- 本次报告统计修正另通过12项检查；上述全量结果来自此前phase6-tests.txt，统计修正阶段未重新运行全量。修正保留捕获输出与gold，未新增模型调用。
- 原 profile4/profile5 安全基准各90次攻击、135合法消息/证明，全部通过；Banking Security45/45拦截；原业务34/34通过（可完成业务23/23）。原数据集未替换。
- 55条人工规划gold：合法13/13，危险42/42拒绝，unsafe acceptance **0/42**；此结果仅适用于本地数据集。
- 100条真实Gemini请求，固定人工gold，原始意图/参数/澄清准确率 **67%/87%/82%**。raw与system分开；缺失准确率观测计为失败。源码语法与银行grounding重放后system最终意图准确率43%，接纳17/100：只读完成4、等待用户审批12、业务规则阻止1；保守拒绝83。虚构资源5/5、注入类别17/17被拒绝，资金写0。**安全拒绝不能算模型原始正确或业务完成。**
- 指标统一从逐例记录生成；候选校验标记（历史字段raw_unsafe）为15/100，包含资源不存在及图不合法，不等于模型恶意输出或实际危险执行。纯模型禁止字段检查与候选校验分栏；未知观测单列。完成率同时报告已接纳候选和全部请求两个分母。旧报告保存在data/phase6-report-repair-before，修正过程见phase6-report-metrics-repair.md。
- 另一次真实Gateway/Router→模型→三节点账单图已COMPLETED，usage/trace/raw合成输出见phase6-gateway-live.json。首次生成缺字段被拒绝的记录保存在data/phase6-first-gateway-live.json。
- 首次模型目录列出旧型号但实际404，换经实调可用型号；初次评测提示缺少规范字段/任务名，修正接口说明后全100例重跑，初次结果保留data/phase6-first-live-evaluation.json。没有修改gold或用gold修复输出。
- 合约solc0.8.24实际编译，本地EVM提交/读回/重复/冲突/权限通过；RPC证据错链/目标/收据/事件/读回/确认/重组及签名幂等测试通过。没有外部交易hash冒充live proof。
- 原全量回归曾发现空持仓redeemable_at整数0与SQLite REAL读回0.0并发Schema差异，统一为0.0后重跑通过。原断言未改；首次失败完整日志保留data/phase6-first-full-regression.txt。

完整分母/逐例/失败原因/latency/tokens见JSON/CSV；没有固定可靠价格，cost为null。

## 能力真实性矩阵

| 能力 | 分类 | 证据 |
|---|---|---|
| Canonical Bank SDK / Virtual compatibility | LOCAL_SANDBOX_VERIFIED | phase6-tests.txt; bank-integration-sdk.md |
| Starling reference protocol adapter | IMPLEMENTED_NOT_LIVE_VERIFIED | test_phase6_sdk.py; reference-bank-validation.json |
| Official sandbox bank connection | BLOCKED_BY_EXTERNAL_CREDENTIAL | reference-bank-validation.json |
| Gemini real inference, 100 synthetic requests | REAL_EXTERNAL_VERIFIED | real-llm-evaluation.json/.csv |
| OpenAI cross-provider live experiment | BLOCKED_BY_EXTERNAL_CREDENTIAL | real-llm-evaluation.json.cross_provider_attempt; authentication probe HTTP401 |
| Real Gateway/Router semantic READ graph | REAL_EXTERNAL_VERIFIED | phase6-gateway-live.json; model-router-live-evaluation.json |
| Privacy/fault/fallback controls | LOCAL_SANDBOX_VERIFIED | test_llm_gateway.py; test_llm_providers.py; test_phase6_proposal.py |
| Audit contract and EVM/RPC protocol | LOCAL_SANDBOX_VERIFIED | test_phase6_anchor.py; contracts/AuditAnchorContract.json |
| External Sepolia anchor/read-back | BLOCKED_BY_EXTERNAL_CREDENTIAL | external-anchor-proof.json |
| AA multi-identity collection | LOCAL_SANDBOX_VERIFIED | aa-demo.json; test_phase6_split.py; test_phase6_http.py |
| Constrained graph/compiler/runtime | LOCAL_SANDBOX_VERIFIED | planning-benchmark.json; phase6-taskgraph-demo.json |
| Starling card/subscription/investment adapters | NOT_IMPLEMENTED | Reference manifest deliberately excludes these capabilities |
| DID / ZKP future extensions | INTERFACE_ONLY | backend/security/extensions.py |

real_bank_connected=false；official_bank_sandbox_verified=false；real_llm_verified=true；cross_provider_verified=false；external_blockchain_verified=false。

## Demo与复现

1. `python scripts/phase6_demo.py`：本地多身份HTTP AA，100元→3334/3333/3333分，逐人审批→部分收款→完成；aa-demo.json记录实际收款余额1010000分与三独立审批。
2. 同脚本：Finance/Service并行读取→取消订阅提案→WAITING_USER→重启→独立审批→Verifier/Receipt→Wealth规划2026-11预算，从300000分加已核验月节省1999分为301999分；不购买产品。phase6-taskgraph-demo.json。
3. `python scripts/phase6_gateway_live.py`：只在临时合成银行中调用真实Gemini，经实际Router和validator执行READ/ANALYZE/REPORT；没有真实客户数据。配置型号可替换；环境密钥不输出。
4. `python -m unittest discover -s tests -v`；`python -m benchmarks.phase6_planning`；原benchmarks对应security/banking_task/banking_security模块，输出参数见脚本。
5. `python scripts/phase6_external_validation.py`：缺凭据时生成BLOCKED证据；有配置时先只读银行验证。钱包/合约部署需受控本地操作员，不自动申请凭据或资金。

## 修改清单

原文件修改：
- `backend/banking/actions.py`
- `backend/banking/extended_bank.py`
- `backend/banking/intent.py`
- `backend/banking/virtual_bank.py`
- `backend/business.py`
- `backend/config.py`
- `backend/execution/gateway.py`
- `backend/llm/configuration.py`
- `backend/llm/gateway.py`
- `backend/llm/providers/http_adapter.py`
- `backend/persistence.py`
- `backend/receipts.py`
- `backend/runtime/blackboard.py`
- `backend/schema_validator.py`
- `backend/security/extensions.py`
- `backend/server.py`
- `backend/service.py`
- `backend/verification.py`

新增Python文件：
- `backend/audit_trust/external_anchor.py`
- `backend/bank_sdk/__init__.py`
- `backend/bank_sdk/auth.py`
- `backend/bank_sdk/bridge.py`
- `backend/bank_sdk/configuration.py`
- `backend/bank_sdk/contracts.py`
- `backend/bank_sdk/rate_limit.py`
- `backend/bank_sdk/starling.py`
- `backend/bank_sdk/transport.py`
- `backend/bank_sdk/virtual.py`
- `backend/banking/split.py`
- `backend/llm/graph_proposal.py`
- `backend/llm/providers/gemini.py`
- `backend/runtime/graph_runtime.py`
- `backend/runtime/taskgraph.py`
- `scripts/deploy_audit_anchor.py`
- `scripts/phase6_demo.py`
- `scripts/phase6_external_validation.py`
- `scripts/phase6_gateway_live.py`
- `scripts/phase6_live_probe.py`
- `scripts/phase6_model_availability.py`
- `scripts/phase6_reports.py`
- `scripts/taskgraph_worker.py`
- `benchmarks/phase6_llm_evaluation.py`
- `benchmarks/phase6_llm_metrics.py`
- `benchmarks/phase6_llm_postprocess.py`
- `benchmarks/phase6_planning.py`
- `tests/phase6_helpers.py`
- `tests/test_phase6_anchor.py`
- `tests/test_phase6_gemini.py`
- `tests/test_phase6_graph.py`
- `tests/test_phase6_http.py`
- `tests/test_phase6_metrics.py`
- `tests/test_phase6_proposal.py`
- `tests/test_phase6_sdk.py`
- `tests/test_phase6_split.py`

新增配置、合约、人工数据集、requirements-phase6.txt、docs报告另见目录。原测试哈希与可复核变更清单在phase6-change-manifest.json。

## 下一阶段与边界

先配置Starling PAT/测试账户/登记签名钥，完成官方sandbox的只读与经过Core审批的小额GBP付款/status/Webhook验证；没有这一步不能提升银行真实性等级。外部AA收款映射、GBP消费语义/分类分析、OAuth自动刷新和跨进程银行限流尚未实现，不在manifest中伪报业务分析能力。

配置Sepolia RPC、部署合约、测试钱包/gas，经过确认与独立读回后才能把external_blockchain_verified变为true。保留失败交易的已知hash、明确不确定写，不自动重复广播新交易。

取得有效第二供应商凭据，再做跨provider实测。当前模型复杂语言/图生成成功率及严格源语法接纳率仍有限；建议改善闭合输出协议与对话澄清，使用独立新gold集验证，不能用此次数据拟合后宣称泛化成功。DID/ZKP保持接口，不把未实现能力包装成比赛已完成成果。

## 指定交付索引

1. [phase6-architecture-review.md](phase6-architecture-review.md)
2. [phase6-gap-matrix.md](phase6-gap-matrix.md)
3. [bank-integration-sdk.md](bank-integration-sdk.md)
4. [reference-bank-selection.md](reference-bank-selection.md)
5. [reference-bank-validation.json](reference-bank-validation.json)
6. [real-llm-evaluation.json](real-llm-evaluation.json)
7. [real-llm-evaluation.csv](real-llm-evaluation.csv)
8. [model-router-live-evaluation.json](model-router-live-evaluation.json)
9. [external-blockchain-anchor.md](external-blockchain-anchor.md)
10. [external-anchor-proof.json](external-anchor-proof.json)
11. [aa-workflow.md](aa-workflow.md)
12. [aa-demo.json](aa-demo.json)
13. [taskgraph-spec.md](taskgraph-spec.md)
14. [planning-benchmark.json](planning-benchmark.json)
15. [planning-benchmark.csv](planning-benchmark.csv)
16. [security-regression.json](security-regression.json)
17. [banking-regression.json](banking-regression.json)
18. [phase6-validation.json](phase6-validation.json)
19. [phase6-tests.txt](phase6-tests.txt)
20. [phase6-final-report.md](phase6-final-report.md)

## 2026-10-06 后续收尾记录

上述449项与100例结果保留为Phase6基线；后续已完成报告统计修正与TaskGraph输出/澄清改进。最新全量477项通过，独立24例配对图评测、失败轮次及最终捕获重放见 [专项说明](phase6-taskgraph-usability.md) 与 [验证记录](phase6-taskgraph-usability-validation.json)。新图评测不替代原100例自然语言意图指标，银行/外部链/DID/ZKP真实性等级不变。


## 2026-10-06 第二供应商收尾

DeepSeek通过既有兼容Provider接入Gateway/Router，实际模型deepseek-flash。复用冻结24例图回归，24次真实返回全部达到预期；两个金融提案停在WAITING_USER。7项双向选择、故障回退、非法输出不回退、敏感数据隔离与shadow检查通过，其中5次真实模型返回，故障由本地测试替身注入。预检加上述实测共30次真实推理，资金写入0，原100例意图指标不变。

最新全量486项测试通过；原22个测试文件、原Validator/Compiler及上一轮图协议/Runtime/Gemini源码校验一致。24份DeepSeek输出在当前Core重放一致、无用户授权、资金写0。详见[专项说明](phase6-second-provider.md)、[验证记录](phase6-second-provider-validation.json)及[全量日志](phase6-second-provider-tests.txt)。官方银行Sandbox、Sepolia外部read-back、DID/ZKP仍未完成实接验证。

## 2026-10-07 官方银行 Sandbox 只读连通验证

Revolut Business 官方 Sandbox 已完成用户在银行授权页的同意、本机回调与确认、RS256 JWT、真实 token 交换以及 `/accounts` 查询。银行返回5个账户、4种币种（AUD/EUR/GBP/USD），此次工具没有调用银行资金接口。OAuth state 在这次真实回调中正确返回，凭据仅存于项目外的受限个人目录。

这一结果更新了“所有官方银行 Sandbox 均未实接”的历史状态，但不改变 Starling 仍只经过本地协议测试的事实。Revolut 当前为独立只读 bootstrap；Bank Adapter 映射、Trusted Execution 业务场景、交易读取与分析、token 自动刷新、付款及执行后核验尚未实测。请求 READ；银行 token 响应未包含 scope，不能宣称已从返回值核验凭据的权限范围。核心架构、能力控制及模型配置未修改。

12项接入工具测试通过，包括来源校验修复后的本机 HTTP 确认流程（银行响应为测试替身）。这不是重新运行全工程测试，上一轮全量486项结果保留为历史基线。证据见 [只读验证记录](revolut-sandbox-connect-validation.json)；流程见 [接入说明](revolut-sandbox-bootstrap.md)。下一步是在既有 SDK 边界增加 Revolut 只读适配，再验证业务调用经过 Gateway/Capability/Audit；付款继续等待独立权限和原可信执行链。

## 2026-10-08 官方银行只读业务集成

Revolut新增只读Adapter、真实OAuth自动刷新和精确Decimal映射，已有Finance BILL_ANALYSIS与TaskGraph通过原Core读取官方Sandbox成功：5账户，GBP5条记录中3条消费合计272.50 GBP，2条充值排除。6条V3消息接受、11个SDK读取审计事件、审计链/本地固定公钥签名检查点验证通过，银行资金写0、LLM调用0。该结果仅提升只读业务真实性，不代表外部资金执行或生产接入完成；默认Virtual前端保留。

本轮文件、运行方法、权限与业务限制见 [集成说明](revolut-readonly-integration.md)；[真实业务实测](revolut-readonly-validation.json)、[工程验证](revolut-readonly-engineering-validation.json)、[全量日志](revolut-readonly-tests.txt)分别保留实接和合成测试边界。原Validator/Compiler、Execution Boundary和能力Registry保持冻结，原测试文件未修改。

全量522项测试通过（400.270秒）；本轮24项新增SDK/认证/外币分析展示测试，已有12项bootstrap与此前486项基线全部通过。原三个Virtual HTTP Demo再次完成，隔离银行余额、审计与重启持久化检查通过。

## 2026-10-08 银行前端与 opt-in 付款收尾

官方 Sandbox 前端账户/币种选择、交易列表、Finance 报告和账户切换已实测；新受限付款 Adapter、永久幂等登记及只读补充核验通过合成银行测试，继续使用原安全链，没有新增 Agent。真实 READ,PAY 授权流程成功交换 token 并读取5个账户，响应 scope 仍缺失；不得把请求 PAY 当作实际付款验收。凭据独立存储，0.01 EUR 提案停在 WAITING_USER。真实付款与结算以[最新状态](revolut-banking-validation.json)为准，未确认前资金接口没有调用。

全量545项测试通过（624.898秒）；最终单个资源协议路径修正后再运行35项专项测试通过，并实际验证官方 `/counterparty/{id}` 与 `/transaction/{id}` GET。四种浏览器运行通过，真实模式资金写0；原36个测试源文件、Core执行/规划/Registry文件保持校验一致。[运行说明](revolut-banking-integration.md)、[全量日志](revolut-banking-tests.txt)、[专项日志](revolut-banking-targeted-tests.txt)、[浏览器证据](revolut-banking-browser-validation.json)。

## 2026-10-08 用户确认后的首笔官方 Sandbox 付款

用户明确确认向 Acme Corp. 转账 0.01 EUR 后，原 Authorization → Execution Contract → Gateway → Adapter 链向官方 `/pay` 提交一次，银行交易 `05677bc9-99a7-e35d-0040-00ab31651da6` 返回 PENDING。独立读回的金额、币种、收款路由、request_id、reference、费用、时间及余额全部匹配；任务保持 MANUAL_REVIEW，无成功回执。此记录更新前文“真实资金接口调用为 0”的历史状态，仅代表 Sandbox 已接受付款，不代表结算完成或生产接入。

独立操作员测试工具对同一笔交易尝试一次官方模拟完成端点，银行返回 403 / BANK_SCOPE_DENIED，后续原验证器仍读回 PENDING。没有第二笔付款、没有自动模拟重试、没有扩展 Agent 或银行管理权限。完成结算仍需银行侧允许测试状态推进或银行自行完成；具体缺失权限尚未证实。本次 7 项操作员模块专项测试通过，原 545 项全量结果为此前基线，冻结 Core 和原 36 个测试源文件哈希保持一致。

证据：[付款运行记录](revolut-live-transfer-validation.json)、[独立银行读回](revolut-pending-readback.json)、[模拟拒绝记录](revolut-sandbox-settlement-validation.json)、[专项测试](revolut-settlement-tests.txt)。

## 2026-10-08 模拟结算权限排查准备

官方新 API Console 将沙箱模拟列为 Write；当前旧版 OAuth 的具体权限映射仍待验证。已准备显式 READ,WRITE 操作员授权试验入口，独立存储凭据，不向 Agent 提供，也不申请 PAY/敏感卡权限。业务 SDK 加强标记检查，即使 token 响应缺少 scope，也拒绝使用操作员凭据。银行管理权限必须由用户在银行页决定是否授予；尚未取得此次试验授权，未重试模拟或付款，原交易仍 PENDING。

53项相关专项测试通过（23.231秒），包括5项新增授权隔离测试；原545项全量基线不改。入口只验证账户读取，不能把授权准备或账户读取包装为模拟权限/结算验证通过。[准备记录](revolut-simulation-permission-plan.json)、[专项日志](revolut-simulation-consent-tests.txt)。

## 2026-10-08 首笔官方沙箱转账闭环完成

用户完成独立 READ,WRITE 试验授权后，新凭据通过原 claim、已知403的审计 payload hash、历史逐笔授权/合约与新签发时间校验。操作员对同一已提交交易显式尝试一次模拟完成，银行返回 completed。原验证器独立 GET 核查后，补充结果 VERIFIED，生成唯一回执 `ec24b28f-c172-48ce-b3ef-d41417f69175`。向 Acme Corp. 的金额为0.01 EUR，付款仍只提交一次；模拟共尝试两次，第一次403、第二次成功。

随后20项银行与回执检查全部通过，原审计哈希链、本地固定公钥的检查点签名和回执包含证明通过独立验证。前端展示补充核验、EUR完成记录及成功回执，无脚本错误，页面POST为0。原MANUAL_REVIEW终态历史保留，金融补充核验VERIFIED；冻结Core/Registry/规划器与原36个测试源文件未修改，未新增Agent。独立操作员工具不进入业务能力注册表，银行scope响应仍缺失，不能把本次端点成功外推为全范围授权证明。

本次13项操作员结算测试通过（14.900秒），涵盖已知拒绝绑定、新凭据、篡改/过期/额外PAY拒绝、未知响应不重试与第二次试验防重发。此前53项权限专项和545项全量分别保留为历史结果，未将它们伪报为本次全量。

本次完成的是“官方Sandbox付款 → 官方模拟结算 → 独立读回 → 原Core补充回执/审计”闭环；不是生产银行清算，也不改变Sepolia、外部AA、DID/ZKP的未完成状态。新文件与结果见[接入说明](revolut-banking-integration.md)、[最新真实性](revolut-banking-validation.json)、[独立读回](revolut-settled-readback.json)、[回执证明](revolut-transfer-receipt-proof.json)、[浏览器验证](revolut-settled-browser-validation.json)。
