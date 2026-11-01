# 真实 LLM 与受约束 TaskGraph 评测

本轮使用实际 DeepSeek OpenAI-compatible endpoint 与 Gemini native endpoint，沿既有 Gateway/Router 调用。不存在模型直连银行。仅发送合成任务，使用独立临时 Virtual Bank；没有金融授权，所有资金执行仍暂停等待用户确认。每例保留 prompt、原始响应、usage、模型名、安装图、错误、状态及耗时；费用未知保持 null。

先冻结第一份人工合成120例：72合法、24缺字段、24攻击，覆盖六场景。DeepSeek 首轮合法26/72，Gemini43/72。保留该批全部 JSON/CSV 和修改前源码于 `data/hardening-llm-first-pass`。根因包括原完整协议与紧凑协议提示混用、重复元数据、多余 WAIT_USER、把 service.resolve 用于卡片、错误能力主体和依赖。没有放宽 Validator，也没有删除失败。

修正独立 compact_v2 提示、函数别名和安全输入字段描述，只在4条独立开发样例上检查。随后人工编写另一份无相同 goal 的120例并冻结 hash，再运行单次最终评测，未依据最终 gold 重调。两批场景族/银行夹具相近，因此不是盲第三方样本或生产准确率证据。

| 最终独立样本 | DeepSeek | Gemini |
|---|---:|---:|
| 返回真实模型响应 |120/120|120/120|
| 全部预期任务状态与图要求命中 |93/120 (77.5%)|96/120 (80.0%)|
| 合法任务命中 |47/72 (65.28%)|58/72 (80.56%)|
| 缺字段正确澄清 |22/24|14/24|
| 攻击预期被阻止 |24/24|24/24|
| 合法任务未安装图 |20/72|7/72|
| 未授权银行写 |0|0|
| latency p50 / p95（ms）|1197.857 / 1839.176|2355.959 / 2916.984|

命中指 gold 所需 capability/依赖与预期终态一致，不能改称原始意图准确率。合法72例中 DeepSeek：COMPLETED26、WAITING_USER26、BLOCKED20；Gemini：COMPLETED35、WAITING_USER30、BLOCKED7。已安装且完成仍可能漏目标，不能全部记正确。WAITING_USER 是安全暂停，不是资金执行完成。攻击批的拒绝也不是业务完成。

最终错误主要为 REJECT_PLAN 与 INVALID_MONTH。模型生成错误依赖、漏子任务、错误澄清仍存在，本轮没有通过放宽金融规则消除。明确输入的确定性场景用于稳定业务演示；LLM 规划标识为已真实验证但质量尚未达成熟产品水平。

复跑：`python -m benchmarks.hardening_holdout --dataset benchmarks/backend_final_holdout_20261008.json --provider deepseek --output docs/operator-new-run.json`（需要自己配置凭据且发生模型调用费用；新记录不可覆盖已交付最终样本）。本轮没有设置定期模型调用。人工 gold、`.sha256`、全部 JSON/CSV、首轮捕获、源码 hash 和统一汇总均保留。历史100例由 `phase6_llm_metrics.validate_summary` 本轮重新核对，原捕获不改写；legacy raw_unsafe 是候选综合标记，不是实际未授权执行。
