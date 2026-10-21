# TRUSTA Intelligence Layer v2.1 — Execution Alignment Report

交付日期：2026-10-13（Asia/Shanghai）。

## 结论

工程实现及覆盖范围内验收 **PASS**：当前完整 pytest 1048 项通过，新增 95 项；无跳过、失败或错误。真实 Provider 规划稳定性为 **PARTIAL**，不能将三种模型的 Planning 能力统一标为 PASS。没有修改提示词、评分或历史数据以提高分数。

Planning、Validation、Sandbox Execution、Execution Alignment、Audit Verification 在 20 个离线正常场景中均 PASS；20 个确定性攻击场景 PASS，越权资金写入为零。真实 Gemini 有 3 份执行证明通过最终独立验证，其中 2 份同时符合预先固定的完整任务意图。另 1 份执行符合模型自身合法计划，但与预期任务结构不一致，不能算任务完成成功。

## 架构及边界

原 Provider → 冻结 LLMGateway/LLMRuntime → EvidenceRuntime 观察候选 → 原 Structured Schema/PlanValidator → 新 Sandbox adapter → 原六 Agent / Blackboard / GraphRuntime → BankActionIR → Schema / Capability / Policy / Authorization / Execution Contract / Tool Gateway → Virtual Bank → HardVerifier / Receipt → 原签名 Merkle Checkpoint → 独立证明验证器。

LLM 不持有 Sandbox、付款方法、签名密钥或执行权限。付款接口只向受信操作员开放；测试控制器只有在完整计划语义与预冻结场景一致、金额/账户/收款人/币种同时匹配时才确认本地测试付款。金融执行继续经过原逐笔授权链。不是另一条付款通道。

所有新执行均在独立目录的 LOCAL_VIRTUAL_BANK_ONLY；显式构造本地 Config，禁止复用非空未知目录、不读取正式银行适配器配置。未运行 Anchor Worker，未接入银行生产资金或链上广播。已冻结的 Provider Router 直接复用，无新增路由设计。

新增 Audit metadata 使用原 append/append_in 接口，未改变 Audit 协议。记录 execution_id、plan_hash、execution_hash、agent_id、timestamp、result。运行快照有本地 HMAC、原 Runtime seal、状态 CAS 及数据库 revision 校验；过时观察不能覆盖已完成状态。

## 测试环境与数据

Windows / Python 3.13.1 / pytest 8.4.2。pytest 仅作为开发测试依赖，写入 requirements-evaluation.txt；原生产依赖未修改。pip check 无破损依赖。

新数据集：20 正常 + 20 攻击。正常场景为余额、交易、财务分析、转账计划及产品咨询，每类 4 个口语变体；全部输入为合成数据。攻击包括伪 Agent、非法 Action、缺参数、异常金额、环/自依赖、重复节点、权限提升、事实引用伪造、代码引用、重复 JSON 和重放。模型 fixture 只用于离线注入候选，银行、账本、授权、HardVerifier、Receipt 和 Merkle Proof 均真实运行，未 mock 成功执行。拒绝位置按实际结果记录：计划结构及权限错误由原 PlanValidator 阻止，超限金额由原金融 Schema 阻止，请求重放由本地持久化唯一约束阻止；没有将所有拒绝都归功于单一 Validator。

真实模型使用同一 prompt.v2.yaml、同一 Schema、同一输入投影、同一 normal_cases.json、同一指标。发送前固定 frozen-requests.json。max_output_tokens=768、timeout=45 秒、无 fallback 或隐式重试。适配器进程额外提供 50 秒外层截止，处理 DNS/代理阶段超出 socket 超时的情况。

## 真实模型结果

| Provider | Model | 场景数 | 完整意图匹配 | 最终独立执行证明通过 |
|---|---|---:|---:|---:|
| deepseek | deepseek-flash | 20 | 1 | 0 |
| glm | glm-4-flash-250414 | 20 | 0 | 0 |
| gemini | gemini-3.1-flash-lite | 20 | 2 | 3 |

预算预留 60 次、USD 0.93405，失败及中断计入；其中 2 次中断不重试。并非 60 次成功响应，也不意味着服务商账单精确等于预留费用。actual_billed_usd=null。新增原始响应观察仅在诊断之后启用，早期响应缺失没有补造。

价格保护采用每百万输入 USD2、输出 USD4 的保守上界，输入以 UTF-8 字节数加协议余量预留，输出按 768 上限预留，预留持久化且不退款。仅本次指定模型配置用于 live runner。[DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)、[GLM 官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing)、[Gemini 官方价格](https://ai.google.dev/gemini-api/docs/pricing) 是核价来源；没有将保守预留伪装为实际账单。

失败分类：DeepSeek 14 个截断、1 个 Schema 错误、2 个等待确认；GLM 19 个 Schema 错误及 1 个中断，观测到其输出 Schema 本身；Gemini 有 1 个不可用、5 个网络超时、1 个中断，以及 Schema/参数/grounded validation/等待确认错误。Gemini 初期未配置显式评测代理；在仅评测进程复用已有无凭据 loopback 代理后出现真实成功。代理没有进入银行或 Sepolia 配置。全部失败保留在 live/cases 及日志。

合法 JSON 不等于意图完成，合法计划执行一致也不等于预期任务成功。旧 live/results.json 中 DeepSeek 的 3 个早期执行成功记录缺少原始响应绑定，最终验证器拒绝计入完整证明；原记录保留，差异见 independent-verification.json。未修改旧分数或删除失败样本。

## 安全及可靠性复验

新增 95 项测试覆盖所有正常/攻击场景、强制用户确认、错误 Action Hash、跨用户访问、SQL 状态篡改、并发唯一请求、持久化预算、服务重启恢复、过时状态捕获、独立进程超时、模型输出与计划绑定、重复转账计划禁止自动批准、Policy/Authorization/Contract/Transaction/Receipt 因果错配、证明叶/Checkpoint/签名公钥/Trace 篡改。

95 项专项测试通过；最终全量 1048 项通过，用时 740.53 秒。早期 1035 项全量通过记录保留；被用户中断或工程变更后终止的运行没有算为最终通过。

Ruff F 检查无问题；Bandit 无扫描发现；编译检查与 pip check 通过。扫描不是不存在未知漏洞的证明。275 个冻结文件 Hash 未改变；105 次历史模型文件 Hash 校验通过。实际已配置 API Key 的精确值扫描未发现泄露；对服务商回显凭据的负例在持久化前拒绝。

23 份公开证明在新进程通过独立 CLI。验证器只使用公开 JSON、公钥 pin 和调用方预期 request_hash/plan_hash；不依赖原数据库、操作员密钥或 Provider 密钥。验证模型响应 Hash/Plan Hash、LLM hash chain、编译步骤与依赖、参数/收款人、授权与合同字段、真实账本回执、HardVerifier hashes、原签名 Checkpoint 和 Merkle Inclusion。公钥 pin 应由可信渠道单独提供，不能盲目信任证明自带公钥。

离线推理 fixture 的 p50 0.0528s，p95 0.0692s；这是测试运输延迟，不是生产模型或全任务性能。完整回归首次 720.87s，最终 740.53s；环境并发和新增案例不同，不能据此声称性能改进。

## 文件和复验入口

新增文件：

- `alignment\__init__.py`
- `alignment\evaluator.py`
- `alignment\matcher.py`
- `alignment\metrics.py`
- `alignment\verify.py`
- `evaluation_dataset\attack_cases.json`
- `evaluation_dataset\normal_cases.json`
- `llm_evaluation\__init__.py`
- `llm_evaluation\adapters\__init__.py`
- `llm_evaluation\adapters\compatible_adapter.py`
- `llm_evaluation\adapters\evidence_runtime.py`
- `llm_evaluation\adapters\mock_adapter.py`
- `llm_evaluation\adapters\openai_adapter.py`
- `llm_evaluation\adapters\worker.py`
- `llm_evaluation\budget.py`
- `llm_evaluation\evaluator.py`
- `llm_evaluation\run.py`
- `llm_evaluation\scenarios\attack_cases.json`
- `llm_evaluation\scenarios\normal_cases.json`
- `requirements-evaluation.txt`
- `sandbox\__init__.py`
- `sandbox\account_service.py`
- `sandbox\bank_api.py`
- `sandbox\payment_service.py`
- `sandbox\permission.py`
- `sandbox\transfer_service.py`
- `tests/test_execution_alignment_v21.py`

既有源文件修改：**0**。全部数据/日志在 data/intelligence-v21/；完整日志 full-delivery.log，JUnit full-delivery.xml，预算 live/budget.sqlite3，失败 live/cases/，原始观察 live/responses/，新数据集与 prompt Hash 见下表。

独立复验示例：

```powershell
python -m alignment.verify --proof <public-evidence 中的 JSON> --pinned-key <对应 PEM> --expected-plan-hash <可信计划 Hash> --expected-request-hash <可信请求 Hash>
```

公开包仅 public-evidence/ 中的 JSON/PEM 及 independent-verification.json；不要发布 banks/ 下的本地私有签名材料、数据库或预算凭据。再次运行 offline 应指定新目录；live 不会重发已有付费请求，本轮额度已经耗尽。

## 遗留限制及验收决定

已关闭本轮确认的适配、执行绑定、并发观察、超时、恢复及证据验证工程缺陷；覆盖范围内没有未修复的已确认内部问题。

真实模型整体规划稳定性未达全 PASS；预算已经用尽，不能追加试验。固定 768 输出上限与 DeepSeek 截断、GLM Schema 复述的能力问题保留；没有为提升分数改 Prompt、模型输出或评测标准。真实攻击 Prompt 的模型泛化评测本轮未运行，20 攻击是确定性防线复测。该限制不应冒充真实模型安全泛化证明。

当前完成的是“真实推理 → 受控本地银行执行 → 可独立复验审计”的闭环；并非真实银行或新增 Sepolia 验收。证据只能证明已测试、已签名观察范围内的一致性，不能证明不存在未知漏洞，也不能替代用户授权。

## Hash

- `evaluation_dataset\normal_cases.json`: `a6a703750d6e3a62b501eb9d2fc8749b962502ee57e8b1822069bbef48048094`
- `evaluation_dataset\attack_cases.json`: `0dace9e5236928b6c4ff0cb0f3959b2d9e9123af0ffadc3efc1fd31c5575c03c`
- `llm\planner\prompt.v2.yaml`: `58032279ddc57f66aa40192cdd7d152cc7b41a0b2327dc24ff72de1602007f32`
- `data\intelligence-v21\full-delivery.log`: `d33fd373f32fdcec377f79ec8cae50c776368ba87035b345e69e6a7167e787dd`
- `data\intelligence-v21\full-delivery.xml`: `2abc5de0224c4522953e5cb1eca239ba7d0f297fd788f8fa90247384a3429e41`
- `data\intelligence-v21\independent-verification.json`: `a1a5a0b49426b52870f2dd42fbcfc81e035d2b041c46b7b11d98c369cb14ba7a`
