# TaskGraphIR v1

通用计划为 `{schema_version:1,nodes:[...]}`；节点字段闭合为 node_id,node_type,owner,capability,input_refs,output_refs,dependencies,condition,failure_policy,parameters。仅十一种语义节点：READ/ANALYZE/RESOLVE/COMPARE/DECIDE/PROPOSE_ACTION/WAIT_USER/WAIT_TIME/BRANCH/JOIN/REPORT。调用闭合语义函数表，没有代码解释器、URL tool 或动态 Agent。

参数为已声明 input:key 或祖先 Node:result 引用；每节点仅输出自身 :result。Finance records 必须来自 transactions.read，Service authority 来自 subscriptions.read，候选来自 finance.recurring；下一月预算的节省必须引用已核验取消回执。银行状态/风险/余额不得作为模型字面量。PROPOSE_ACTION 参数只能来自用户明确输入，模型不能用前一段生成数字自行下单。

Validator 检查 schema、六个现有活跃 Agent、namespace/capability、owner、bank资源及来源、DAG/依赖/引用/条件、无并行写。至多32语义节点、深度20、依赖16、图64KB；失败STOP，重试0，等待≤30天。条件必须引用祖先 BRANCH/DECIDE/WAIT_USER 的 boolean。资源存在性/归属通过 Gateway 安装前核验；compiler 检查展开后的 ID 无碰撞。

SecurityExpansionCompiler 为每个 PROPOSE_ACTION 插入 SCHEMA → CAPABILITY → POLICY → AUTHORIZATION → CONTRACT → EXECUTE → VERIFY → RECEIPT。依赖金融提案的后续节点自动等待其 RECEIPT；编译步骤归属固定可信系统角色。模型提交上述安全节点、共享/伪造授权、缺验证、跨范围写、endpoint/sql/code 都拒绝。

Blackboard 保留原任务/步骤表，增加 graph_state（计划、输入、输出、逐动作记录、用户选择、compiler hash）。HMAC 保护状态；每次恢复重新编译核验 DAG；并行只读用实际线程前沿、释放 Registry 共享锁后读取、提交前检查 revision；同任务 advance 串行。写与持久化串行，执行/核验不确定转 MANUAL_REVIEW，不能自动重试。金融动作同 GENERAL_GRAPH task 内有不同 ActionIR/action_hash/Authorization/Contract，AA 另为参与者独立 TRANSFER 任务。

LLM 接入：POST /api/graphs/propose(goal,inputs) → Supervisor 建立草稿 → LLMGateway.propose_graph → ModelRouter → 配置的 Provider → 严格 JSON/TaskGraphValidator → Gateway 资源核验 → compiler → Blackboard/Scheduler。调用不持有 Service 锁，返回后校验 revision。输入值保留本地；原始用户 goal 仍按 restricted 分类，不能回退至未批准 public Provider。disabled 时 BLOCKED；shadow 只记录校验/hash、不安装计划。生产未配置受审查 private/restricted Provider，真实 Gemini仅用于公开合成评测与隔离的真实Gateway图试验。

Planner 的语义提案权限是固定 Graph endpoint 的有限角色规则，受现有活跃身份保护；不改原 signed CapabilityRegistry bank/inference commitments，也不给新增金融 grant。普通旧 template inference 路径不变。raw Prompt/response 只保存在合成评测文件；Gateway telemetry 仅保留 hash/usage/安全错误。

2026-10-06提案接入增强：Gateway提供闭合响应Schema及按原注册表生成的功能目录；响应可为有效图或有限的缺参澄清。澄清问题由系统生成，不安装图、不产生审批，补齐输入后另建提案任务。旧纯图响应保留兼容。完整安全校验仍为上述原Validator/Compiler；详见[TaskGraph可用性改进](phase6-taskgraph-usability.md)，不将小规模图评测等同于原100例自然语言意图评测。

接口：POST /api/graphs 手动闭合图；/propose 模型提案；/{id}/signal 语义选择；/{id}/authorize 独立金融审批；/{id}/resume 恢复。选择不是授权。`python -m scripts.taskgraph_worker` 可轮询 WAIT_TIME，无用户审批权限。

55条人工指定 gold 本地规划用例：13合法、42危险，见 planning-benchmark.json/.csv 和 benchmarks/phase6_planning_gold.json。危险接纳率0限定于此数据集，不能外推为任意模型绝对安全。Demo：并行 Finance/Service读取 → 分析 → 取消提案 → 用户批准 → 核验回执 → Wealth 下月预算（仅建议）；输出 phase6-taskgraph-demo.json。
