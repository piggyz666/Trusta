# 真实 LLM 规划失败根因

证据范围：上一轮保留的 `llm-final-holdout-{deepseek,gemini}.json` 中 raw_output、attempt.validation_reason、installed_graph、system_status，以及当前 compact_v2、graph_protocol、TaskGraphValidator/Compiler 源码。历史数据不覆盖，不作新一轮调参集。

| 现象 | 实际证据 | 根因与处理 |
|---|---|---|
| REJECT_PLAN / PROVENANCE_OR_DEPENDENCY | DeepSeek bill-F02/F04/F06/F09 使用 `TxnList:records`；协议唯一输出为 `Node:result` | 改善输出契约与可运行的链式示例。保持拒绝不存在的引用。 |
| AUTHORITATIVE_FACT_PROVENANCE | DeepSeek cross-F05 把 recurring_history 的原始结果当作 recurring 的分析候选 | Tool 说明没有突出原始历史与分析候选的区别。补充类型语义与 matching 示例，不接受原始历史替代候选。 |
| INVALID_MONTH | DeepSeek cross-F08 给周期历史任务添加不需要的月度读取，或将 account_id 引用绑定为 month | 明确不同工具输入类型，recurring 自行读取历史，无需 month。禁止基于模型输出修改月份值。 |
| GRAPH_COMPACT_SCHEMA | 模型混入 `type:json_object` 等 transport 字段 | 原 schema 名称与参数形状相互独立；改为按参数形状分组的闭合 union，明确 JSON 与传输选项边界。 |
| 错误依赖 / 漏任务 | 模型给余额读取和付款提案平行依赖，只在最终 report 汇合 | 这不满足“先读后提案”。新评分验证指定操作的祖先关系，prompt 明确顺序词的语义。禁止评分只看 capability 集合。 |
| 多余 WAIT_USER | Gemini 为挂失强认证单独加 wait.user/input:deadline | 原 Compiler 已插入 AUTHORIZATION；补充说明。没有删除真正业务需要的 WAIT 节点。 |
| 应澄清而不澄清 | 缺 target_id 时改为列卡、列产品，或用银行列表 result 作为财务目标 | schema 对金融参数只允许 input 引用；显式 action_type 的本地提示携带缺少的引用名。不能让银行输出替代用户选择。 |
| 旧评测误判 | transfer-F09/F10 的 gold 使用 balances.read；wealth 部分 gold 使用 risk_profile.read/products.read | 与当前 registry 的 balance.read/customers.risk.read/investments.products.read 不一致。保留旧 gold 与旧分数，新评分使用规范能力名。旧“失败”不能全部归咎于模型。 |

## 本轮开发过程的失败也保留

评分器预检查错误地遍历 Compiler 返回对象而非其中 steps，导致安全扩展指标失败。停止预检查后修正、运行专门评分测试、再次冻结 scorer hash，数据与 gold 未改。已发的请求和费用仍计入累计预算，原结果保留为 `targeted-scoring-preflight-invalid.json`，不混入正式配对结果。

首次 schema 展开超过 Provider CONTEXT_LIMIT：Router 正确阻止了请求，没有真实推理费用。改用相同参数形状的 union 与共享 $defs，压缩重复描述，而不是提高 Router 上限。该开发迭代的全零结果保留。

Gemini 某开发迭代出现协议错误；单独诊断的异常链明确为 RemoteDisconnected，raw_provider_response 为 null，说明尚未收到可解析服务响应。Provider 现在将已定义的连接中断归为可重试传输失败，不重试内容拒绝、权限错误或不完整输出。传输与结构重规划共享最多一次追加尝试。同一请求的未知费用保留保守预留值，不假设失败免费。

开发集真实响应还出现只查异常却加 summary、缺目标时以列表替代动作等语义失败。Validator 可以接受安全但不完整的只读图，因此“安装了图”并不代表任务完成。新评分将操作匹配、依赖、澄清和实际状态分开，不把安全拒绝或等待付款计为资金完成。
