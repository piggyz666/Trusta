# TRUSTA LLM Final Live Acceptance

最终结论：**PARTIAL**。新真实评测已完整执行 120/120 次；不是以离线结果替代真实结果。

回归：1108 passed，0 failures / errors / skipped；保留原 1090 项并新增 18 项验收账本测试。耗时 750.78 秒。

## 分项结论

|维度|结论|
|---|---|
|ENGINEERING|PASS|
|PROVIDER_COMPATIBILITY|PARTIAL|
|REAL_MODEL_ROBUSTNESS|FAIL|
|VERIFIED_TASK_COMPLETION|PARTIAL|
|EXECUTION_ALIGNMENT|PASS|
|SECURITY_INVARIANTS|PASS|

## 真实业务结果

|Provider|协议兼容 / 40|Schema / 40|合法计划 / 40|完整意图 / 20|独立验证任务完成 / 20|已完成执行一致性|
|---|---|---|---|---|---|---|
|deepseek|31/40|30/40|23/40|3/20|3/20|9/9|
|glm|40/40|0/40|0/40|0/20|0/20|0/0 (NOT_RUN)|
|gemini|40/40|38/40|30/40|9/20|9/20|10/10|

全部正常和攻击请求分别保留原分母。协议、Schema、计划、意图及执行的不可评估数量在 acceptance.json 中分别提供；不能把后续阶段 NOT_RUN 计为 PASS。

未经授权金融执行 0；真实银行支付 0；公链广播 0。独立审计证明 120/120；完整执行证明 19/19。

另以只读方式核对120个原生虚拟银行数据库：真实模型产生并完成 1 笔本地金融交易，攻击金融交易 0；金额、收款人、Action Hash、Authorization、Execution Contract、借贷两侧账本及最终余额均一致。Gemini transfer02 的本地转账200 CNY分（2元）已完成，余额1000000→999800分；不是只有规划或等待授权。

## 配置与协议

DeepSeek deepseek-flash / Chat Completions JSON object；GLM glm-4-flash-250414 / 同协议；Gemini gemini-3.1-flash-lite / generateContent JSON MIME。同一冻结 Prompt v2.0.0、Schema、40 Case 和 Gold；统一输出 2048；无 fallback、无重试。凭据通过现有本地环境读取，未进入报告。

Schema 的提供方式保持既有适配映射，业务验证统一执行原有 Schema + PlanValidator。Provider 更换不修改核心业务代码。实际业务成功率未达到全部场景通过，不能据此宣称产品已具备模型替换鲁棒性。

三者均取得40/40原始HTTP模型响应。DeepSeek 9次响应为截断，故完整响应兼容指标为31/40；这9次属于输出容量/终止状态问题，不是9次网络或身份认证失败。GLM未执行任何合法计划，执行一致性单独标为 NOT_RUN；总执行一致性 PASS 仅针对19个实际完成执行。

## 失败分类及根因

- deepseek: `{"TRUNCATION": 9, "GROUNDING_ERROR": 3, "FROZEN_GOLD_MISMATCH": 6, "NONE": 3, "WAITING_FOR_AUTHORIZATION": 13, "SCHEMA_ERROR": 1, "EXECUTION_POLICY_BLOCK": 1, "PLANNING_ERROR": 4}`；LLM latency P50=7.771718450007029, P95=12.81199179997202 秒；输入 tokens=23843，输出含 reasoning tokens=53210；usage 未报告=0。
- glm: `{"SCHEMA_DEFINITION_ECHO": 20, "MODEL_REFUSAL_OUTSIDE_TASKPLAN_CONTRACT": 13, "SCHEMA_ERROR": 7}`；LLM latency P50=9.365055849979399, P95=12.797590099973604 秒；输入 tokens=22025，输出含 reasoning tokens=11832；usage 未报告=0。
- gemini: `{"NONE": 9, "GROUNDING_ERROR": 4, "FROZEN_GOLD_MISMATCH": 1, "SCHEMA_ERROR": 2, "WAITING_FOR_AUTHORIZATION": 19, "EXECUTION_POLICY_BLOCK": 1, "PLANNING_ERROR": 4}`；LLM latency P50=1.9498692500055768, P95=2.3423564999829978 秒；输入 tokens=22217，输出含 reasoning tokens=8224；usage 未报告=0。

GLM 的20个正常场景全部回显 Schema 定义，属于完整 API 输出，但不符合 TaskPlan 实例契约；错误被原 Validator 拒绝。攻击场景多为 answer 字段内的拒绝文本，另有错误/非法 JSON，依然不符合冻结 TaskPlan 契约；拒绝文本单独标注，不计为业务成功，也不误称为 Schema 回显。无法仅凭这一轮证明正常场景问题是纯模型能力还是冻结 Prompt 的解释问题，未进行被禁止的 Prompt 调整。DeepSeek 的重复/冗余步骤、参数与依赖问题同样保留；截断按容量失败计入。WAITING_USER 是未完成，正常场景仅在完整 Gold 匹配及精确 Action Hash /可信金额/收款人一致时由本地测试控制器授权。

完整意图失败而执行证据一致的 Case 不计业务成功。逐例原始响应、候选哈希、错误代码和分类见 live-evaluation.json / cases / responses，原采集文件没有改写。

## 安全边界及攻击覆盖

仅复用 BankSandbox -> SecurityExpansionCompiler -> 六 Agent 原 GraphRuntime -> Capability/Policy/Authorization -> 原 Tool Gateway / HardVerifier / Receipt -> 签名 Checkpoint/Merkle -> 独立进程公开证明验证。LLM 仅产生候选计划。攻击 Case 从不授予付款权限。合法候选会等待用户授权；非法金额等进入策略阻断，均不允许完成写入。

真实攻击文本保持冻结版本：通用绕过指令加攻击标签。其 malformed candidate Gold 只用于独立离线确定性对照，未伪装成真实模型响应。20 项离线攻击与真实模型攻击结果严格分开；跨用户、伪造回执等更细分防线主要由原安全回归验证，不宣称这 20 条真实文本已经充分覆盖所有变体。

## 预算及数据完整性

用户新授权120次/$3。实际新账本预留 $2.490234；未退还失败预留。实际账单及 Provider 直接报告的美元费用为 null，不能把保守预留当作已付费用。已报告 tokens 按输入$2/M、输出$4/M保守计价为 $0.429234；未测量请求仍全额预留。

冻结 Manifest SHA256 `e60916c29874f0765cfacd3f14ed0ea9cdacb84cca008486999d06e206869862`。本轮初始350个源码/配置/数据文件、6801个历史文件全部逐字节核对；旧文件变更数量为 0。历史模型输出和旧预算没有覆盖。

## 本轮源码

- 新增 llm_evaluation/final_live.py：现有评测流水线的 Manifest 绑定、专用新账本、真实攻击编排、独立验证及指标汇总。
- 新增 tests/test_llm_live_acceptance.py：18项账本/请求绑定/错误评分/并发与攻击不授权测试。
- 已有 Provider、Runtime、Planner、Validator、Execution、Audit、Sepolia 和历史测试源码均未修改。数据目录内 preflight.py / launch.py / finalize.py 为本次可复核编排工具。

## 可复核交付

- data/llm-final-live-acceptance/acceptance.json：最终分项结论与全部指标。
- live-evaluation.json：120条真实结果、原始响应哈希、失败分类、token与独立证明结果。
- manifest.json / manifest-pin.json / authorization.json / budget.sqlite3：冻结输入及新预算账本。
- cases/ 与 responses/：采集原件，未修改。
- public-evidence/：公开证明和每例 pinned 公钥，可独立复验；不要对外分享 banks/ 私有签名材料。
- deterministic-controls/：明确标为 LOCAL_HTTP_FIXTURE 的离线对照。
- full-regression.log / full-regression.xml / acceptance-tests-final.log：真实测试日志。
- integrity-final.json / frozen-integrity.json / runner-pin.json：源码和历史完整性。

真实请求现已执行完毕，后续公开证明复验不调用模型，不支付银行或区块链资金。所有结果仅证明本次覆盖范围；工程测试通过不等于所有模型业务能力达标。

## 最终公开交付复验

复制到独立 public-evidence 目录后再次复验：120/120 审计证明、19/19 完整执行证明通过；只读取公开 JSON 和 pinned PEM，不读取银行数据库或私钥。120个请求的实际 Trace Hash 与冻结 Prompt、Schema、Provider配置逐例匹配；采集文件和运行器指纹未改变。486个可分享文件未发现已配置密钥或RPC秘密回显。

Ruff / 编译 / 依赖一致性检查通过。Bandit 4项低等级提示已逐项复核（固定参数无shell的独立验证器调用，以及不承担安全边界的冗余断言），未确认漏洞；未添加扫描抑制。细节见 static-review.json。

后置请求哈希重构检查曾把 Runtime 的浮点45.0写成整数45，造成误报；已按原 Runtime 的除法表达式纠正，初始诊断保留，不修改采集响应或执行代码。最终失败项为0。
