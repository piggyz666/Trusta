# TRUSTA LLM 接入与任务规划最终验收

日期：2026-10-14。最终契约：`constrained_planner 3.2.0`。执行范围：`LOCAL_VIRTUAL_BANK_ONLY`。

结论：**工程与安全验收 PASS，真实模型任务完成 PARTIAL；LLM 工程模块可冻结，不能宣称所有模型的所有任务均成功。** DeepSeek 仍有一例遗漏账单分析，其余已确认的接口、编译、绑定和语义匹配问题已修复。没有继续增加架构、Agent、模型调用重试或金融权限。

## 实际修复

- DeepSeek 默认思考消耗输出容量：三家均明确请求关闭 thinking，统一输出上限 2048，不靠增加 Token 或重试解决。
- 模型承担完整 DAG 和安全元数据：同一现有 Planner 新增版本化业务提案契约；模型选择业务调用和引用，本地生成节点、依赖、可信参数绑定，复用原 TaskGraphValidator、SecurityExpansionCompiler 和 Runtime。
- 能力别名重复、参数数组及 Schema 回显：目录按实际权限能力去重，最终 Prompt 明确 JSON 实例、参数对象和字符串引用，旧 Prompt 全部保留。
- Windows 用户环境更新未进入现有进程：启动检查只刷新缺失的指定凭据，保留进程已有值，不输出秘密或改写环境文件。
- Kimi 旧模型配置：只读模型列表证明中国区凭据有效、`kimi-k2.5` 不在当前列表；换为真实服务端列出的 `kimi-k2.6` 后成功调用。MiMo 复用兼容 Adapter，显式配置 `api-key` 和 `max_completion_tokens`。
- 新提案与执行证据原来不等同于原始 TaskPlan：适配器及独立验证器复算原始提案到 TaskPlan 的映射，绑定响应、上下文、计划、执行及审计。
- 真实业务入口与评测入口：新增 `semantic_v3` 接口接入原 contextual Gateway；原六 Agent、授权与执行链不变，原条件请求仍走原完整协议。
- 原图匹配误判：独立规则允许无害只读顺序差异，并识别已注册 WealthAgent 内部读取风险档案和产品库的复合能力。显式独立查询、其他消费者、金融写入、参数及授权不被省略；原严格 Gold 评分不变。
- 原生多目标输入增加条款索引元数据；付款后再次读取余额保留为新鲜读取；规范化结果引用后拒绝重复调用，防止 `call:0`/`call:00` 绕过重复检测。

## 最终统一真实模型结果

每家使用同一冻结 20 个正常场景、20 个攻击场景，同一最终 Prompt、业务契约、输出容量及安全规则。全部真实输出保留。Kimi 使用 Prompt JSON 加本地 Schema 校验，DeepSeek/MiMo 使用原生 JSON object 加本地 Schema 校验；没有跨模型 fallback。

| 指标 | DeepSeek / deepseek-flash | Kimi / kimi-k2.6 | MiMo / mimo-v2.6-flash |
|---|---:|---:|---:|
| Provider 兼容 | 40/40 | 40/40 | 40/40 |
| Schema 合法 | 40/40 | 40/40 | 40/40 |
| 正常规划校验 | 20/20 | 20/20 | 20/20 |
| 原严格 Gold | 15/20 | 16/20 | 16/20 |
| 独立语义等价 | 19/20 | 20/20 | 20/20 |
| 有独立证据的完整目标完成 | 19/20 | 20/20 | 20/20 |
| 已执行计划与执行一致 | 20/20 | 20/20 | 20/20 |
| 攻击下未经授权金融执行 | 0/20 | 0/20 | 0/20 |
| API 延迟 P50 / P95（秒） | 0.868 / 1.146 | 1.449 / 2.290 | 6.878 / 15.588 |
| 服务端 input / output tokens | 21508 / 1330 | 19399 / 1368 | 19406 / 1501 |

唯一未完成目标：DeepSeek `analysis03`（“分析指定月份账单”）仅生成交易读取，遗漏 `finance.summary`。其读取执行及审计有效，但不计业务完成。原生从文本入口仍有可信目标匹配门，类似遗漏会被阻断。

严格 Gold 与语义等价分别保留：前者仍对原显式节点和依赖评分；后者按已验证的注册能力语义判断，不将错误金额、账户、收款人、额外付款或漏掉业务目标视为等价。复合能力规则有独立版本和正负测试；首次保守图评分及后续规则结果均留存，没有改写采集响应或旧评分。

## 安全、证据与回归

- **1158 passed，0 failed，0 skipped；保留原 1108 项，新增 50 项。** 锁定源码全量回归耗时 830.52 秒，源码指纹检查无变化。
- 最终集 120/120 签名审计证明、120/120 请求绑定、120/120 原始响应绑定通过；60/60 已完成执行通过独立 CLI 验证，验证无需操作员私钥或原银行数据库。
- 只读账本复核：12 笔受控虚拟转账，攻击场景转账数量 0。转账通过原 WAITING_USER、独立测试授权、精确 Action Hash、Policy、Execution Contract、HardVerifier、Receipt 链。
- 未经授权金融执行、真实银行付款、公链广播均为 0。银行执行仍只限受控虚拟银行；没有启用远端签名通道。
- Ruff F 检查无问题；依赖检查无破损。原 Validator、金融执行、六 Agent、ECDSA/DID/ZKP、Merkle 和 Sepolia 核心保持原边界；6801 个历史文件字节不变。
- 中间失败日志保留，包括首次接口/格式失败及一次在修改配置期间运行、持有旧模块缓存的回归结果。最终判定仅使用随后锁定源码的完整回归。

## 调用与预算

两次明确授权分别为 120 次 / 3 美元、44 次 / 1.2 美元。实际共 164 次首次请求，重试和 fallback 均为 0；保守预留分别 2.545904 和 0.941304 美元。前 44 次接口与契约诊断结果全部保留；最终契约的 120 例由已完成的 76 例及获批补齐的 44 例组成，没有再发送已完成的最终契约请求。

预留不是实际账单；服务端 token 用量可复核，实际扣费/税费未取得账单，保持未知。两份账本均已达到请求数上限，后续自动调用停止。

## 交付与冻结边界

机器结论与逐例数据：`data/llm-semantic-convergence/final-acceptance.json`、`final-metrics.json`、`independent-verification.json`。公开证明与独立 CLI 输出位于 `public-evidence/`。原始响应保留在各真实调用目录的 `live-responses/`。全量日志/XML 为 `locked-source-regression.log` / `.xml`；源码锁定指纹为 `final-source-pin.json`。

新增核心接口包括 `llm/credentials.py`、`llm/planner/semantic.py`、版本化 Prompt、三家 YAML 配置、`backend/llm/semantic_adapter.py`、独立语义匹配与评测入口。修改限于 LLM 配置/接入/规划及必要的 Sandbox、Alignment 适配接口。完整修改路径和原文件指纹对照保存在验收 JSON。

冻结的是已验证的工程实现与安全边界。DeepSeek 单例语义遗漏作为明确模型能力限制保留，真实鲁棒性和完整任务完成维度标为 PARTIAL；不保证任意自由语言或未知攻击均成功处理。冻结旧攻击文本较通用，结构越权、篡改、重放及跨用户边界另有离线确定性测试。当前没有已确认而未修复的工程缺陷，不继续重构 LLM 模块。

协议依据：[DeepSeek API / thinking](https://api-docs.deepseek.com/guides/thinking_mode/)、[Kimi 官方模型与接口文档](https://platform.kimi.ai/docs/llms.txt)、[MiMo Chat API](https://mimo.mi.com/docs/en-US/api/chat)、[MiMo 结构化输出](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/structured-output)。实际兼容性以保存的真实请求结果、服务端模型标识、usage 和执行证据为准。
