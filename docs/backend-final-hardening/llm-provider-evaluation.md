# LLM Provider 与真实失败审查
状态：历史模型接入 LIVE_TESTED；本轮新远端模型评测 BLOCKED_EXTERNAL。旧请求 800/800，未新授权费用/次数。本轮远端请求 0、新增 API 费用 0。不能把旧 holdout 再称为新盲测。

实际保存的 46 次原始响应已在当前代码离线重放，配置模型 deepseek-flash / glm-4-flash-250414。raw_model_quality、validated planning 与 E2E 保持分别计分。重放来源、SHA-256、原始内容、usage/latency、原分母和修订评分见 llm-historical-replay.json；根因逐样本见 llm-failure-analysis.json。

历史结果（不是本轮新模型准确率）：DeepSeek B_INTENT raw 5/10、接受 6/10、期望结果 6/10；B_GRAPH raw 6/6、接受 6/6、期望结果 6/6。GLM B_INTENT raw 2/10、接受 3/10、期望结果 6/10；B_GRAPH raw 0/6、接受 1/6、期望结果 0/6。正确澄清或暂停不等于付款成功。

源码与响应证据：DeepSeek FC01 amount_text 包含“元”，旧 Prompt 没明确数值子串边界，是提示词与约束接口问题；既有修复保留，不把失败统一归于模型。GLM FC03 模仿 few-shot 额外加入未请求 anomalies；FC11/20/23 丢失金融串行依赖，FC21 缺版本号，FC22 错 input refs。它们分别属于语义/依赖/格式错误，不是银行执行层问题。历史主评测响应 finish_reason=stop，正常模型 content 与 reasoning_content 已分离；不把思考内容当提案。早期 4.7 限流/截断是单独历史证据，不能推断所有 GLM 质量。

本轮根因计数（允许一项多标签）：{"SLOT_SUBSTRING_OR_CONTEXT": 12, "PROMPT_AMOUNT_UNIT_SPAN_AMBIGUITY": 3, "INTENT_SEMANTICS": 6, "CLARIFICATION_SEMANTICS": 8, "SEMANTIC_OR_FORMAT_FAILURE_REQUIRES_MANUAL_REVIEW": 4, "VALID_SCHEMA_WRONG_OPERATIONS_OR_ORDER": 1, "SCHEMA_ECHO_NOT_INSTANCE": 2, "VALIDATOR_UNSAFE_PARALLEL_WRITES": 3, "VALIDATOR_GRAPH_COMPACT_SCHEMA": 1, "MISSING_OR_WRONG_VERSION_ENVELOPE": 1, "VALIDATOR_CYCLIC_OR_MISSING_DEPENDENCY": 1}。

Provider/Router 保持模型无关：OpenAI-compatible、Gemini、声明式第三方 HTTP、企业/本地 Provider；配置控制 model、目的、复杂度、数据敏感度、审核、locality、超时、费用。只读检查不自动探测远端模型，429/断线只有既有有界重试，禁止修补金额、补审批或猜资源。新 format 参数不绕过 Router 数据出站条件。

新数据 6 development + 18 locked evaluation + 16 prepared holdout 已在任何本轮离线评分前冻结。24 个本地样本 21/24 符合原始预设；合法目标 16/19，正确澄清 5/5。不是 LLM 准确率。HN11 的 23 元低于产品最低申购额，预设 WAITING_USER 错误，实际 POLICY_BLOCKED 正确；不改冻结数据或安全规则，保留原得分。HN14 AA 混合图、HN15 任意口语仍是实际能力缺口。

新的 holdout 未运行；作者编写的数据不构成外部独立盲测。待新预算授权和独立 gold 后，才可报告新 DeepSeek/GLM 实时准确率及适合任务规划的新 GLM 型号可用性。
