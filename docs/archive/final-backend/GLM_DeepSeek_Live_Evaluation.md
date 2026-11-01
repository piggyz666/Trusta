# DeepSeek / GLM 真实对照
审查日期：2026-10-09T19:44:08.228566+00:00。Windows / Python 3.13.1。证据状态与冻结策略分别记录。

真实 endpoint 为 DeepSeek OpenAI-compatible 与智谱官方 OpenAI-compatible。测试模型 deepseek-flash 与 glm-4-flash-250414，返回模型/usage/输出均逐请求保存。GLM-4.7-Flash 早期一次截断、随后探测不可用，原始诊断 HTTP 429/1305；不能据此断言不支持 JSON。实际可用 free 4-flash 质量不等于 4.7 或其他 GLM 型号质量。

新数据 3 dev / 8 locked / 12 holdout，各模型 23 次；全部数据在任一模型评测前哈希锁定，Holdout 只运行一次、未复用旧 Final Gold。该 Holdout 由本工程作者编写，不是外部独立作者的盲测，不夸大“完全未见”的统计含义。0 repair / 0 fallback。原始冻结 JSON 保留，两个评分器缺陷在 LLM_Score_Amendment.json 单独修订，不修改数据/响应/安全规则。

| 模型 | 任务入口 | 样本 | 原始语义正确 | 合法图/字段接受 | 期望 E2E 结果 |
|---|---|---:|---:|---:|---:|
| deepseek | B_INTENT | 10 | 5 | 6 | 6 |
| deepseek | A_GRAPH | 7 | 7 | 5 | 7 |
| deepseek | B_GRAPH | 6 | 6 | 6 | 6 |
| glm | B_INTENT | 10 | 2 | 3 | 6 |
| glm | A_GRAPH | 7 | 3 | 3 | 4 |
| glm | B_GRAPH | 6 | 0 | 1 | 0 |

A_GRAPH 是预提供结构化槽位：5 合法任务 + 2 不支持/攻击拒绝；合法执行成功只到 COMPLETED 只读或 WAITING_USER 提案。B_INTENT 是直接自然语言意图/槽位与对话：包含否定、条件、未知收款人、缺字段与一个多轮样本；E2E 一栏包含正确澄清，不等于付款完成。B_GRAPH 是从直接 NL 分句与确定性参数归一化，再真实模型规划、语义核对、运行；6 条是明确语法内的合法组合，不代表自由条件或无界 NL 泛化。

B_INTENT 的原始 intent/slot/clarification：DS 9/10、5/10、8/10；GLM 5/10、3/10、4/10。多轮样本两模型均未完成付款提案，不能宣称多轮模型成功率达标。结构合法与语义正确单独记录，GLM B_GRAPH 1 个合法图仍因语义不匹配被挡住。

对话 E2E 使用同一次真实响应经公共 Conversation 协调器本地回放；不是第二次实时 Provider 对话，也不是生产银行联调。B_GRAPH 为真实 Provider → Core 实际运行。所有样本未发出银行审批；未经授权 transfer=0，独立银行攻击基准另核查完整金融状态。

延迟：DS P50 2220.462ms / P95 6648.744ms；GLM P50 1678.451ms / P95 5588.948ms。46 次主评测 API error 0；GLM 早期 5 次探测失败另列，不从报告抹去。主评测 DS 用量保守估算 $0.02433030；GLM 免费型按官方公示价格估算 $0，非账号账单验证。参考[智谱官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing)。

累计历史 747 + GLM 探测 5 + 主评测 46 + 修复后 dev 2 = 800 次，保守累计 $1.09048315，低于 $5，但请求次数 800 已用尽，停止进一步调用。历史 ledger 文件未改，账目上限采用既有预算保护；DS 实际计算是每百万 input $0.3、output $1.2 的保守预算参数，不当作最新账单单价。unknown usage 保留预留值，未当成免费。

修复仅针对 dev FC01 的 amount_text 单位后缀，补提示词实例与确切子串规则。最后两次只重测同一 dev 请求：DeepSeek 成功，GLM 字段正确但仍请求澄清。这不是新的 Holdout 结果，未提高上表成绩。GLM 质量门仍 FAILED；下一轮需新的独立数据和获准预算/更适合模型，不能放宽 Validator 得高分。

模型 Router 仍按目的、复杂度、数据敏感度、locality、审核标志、安全级别、延迟和费用约束选择。演示模板是 public shadow，真实客户 restricted 数据默认不会发给未审核外部供应商。本轮显式 benchmark 配置仅允许作者合成数据，未修改生产出站规则。
