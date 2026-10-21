# TRUSTA 分步收尾：第二部分——TaskGraph 输出与澄清

日期：2026-10-06。本次改进模型提案接入，不新增 Agent，不修改原 TaskGraphIR、Validator、安全编译器、Capability、Policy、Authorization、Contract、Tool Gateway 或银行核验规则。

## 改动

原请求只提供自然语言格式说明，输出 Schema 为空。缺少参数时模型返回空图，用户只能看到拒绝。现在 Gateway 提供有限的功能目录、闭合响应 Schema，并区分两种响应：

- 有效提案：`schema_version=1, nodes=[...], clarification=null`。Gateway 去掉响应封装后，将原始节点交给原 Validator。通过后才核验银行资源、编译和安装计划。
- 需要补充：`schema_version=1, nodes=[], clarification={reason, missing_parameters}`。只允许固定原因和有限参数名，由系统生成中文问题；拒绝模型自由编写的问题、授权信息及银行事实补录请求。

旧版纯 TaskGraph 响应仍支持。两种格式最终都走同一个原 Validator，没有自动补造节点、修改金额或用 gold 修复候选。

澄清任务保留在 Blackboard，状态为 BLOCKED，错误码为 GRAPH_CLARIFICATION_REQUIRED，result 包含 needs_clarification、question 和 authority=NONE；不安装图、不创建金融动作或审批。用户补充明确输入后再次提交提案，形成新任务并保留原记录。它与金融 WAITING_USER 审批互相独立。

生产推理默认仍禁用；公开 Gemini 示例仍为 shadow/public，不能用于真实客户 restricted 请求。全部实测使用临时 Virtual Bank 和合成请求，输入值仍留在本地，只投影引用名称与用户明确提供的 goal。

## 供应商兼容处理

首次完整 Schema 请求被真实 Gemini API 拒绝，HTTP 400；失败烟测记录已保留。诊断发现去掉嵌套数组的非零数量约束后请求可以被接受。最终 Gemini Adapter 保留闭合字段、类型、枚举和引用格式，仅省略供应商端的非零数组数量约束，保留 REPORT/JOIN 的 input_refs 空数组约束。

完整数量、节点命名、数据来源与能力检查仍由 TRUSTA 校验。模型不得加入安全节点或执行权限；不存在“供应商不支持就放宽执行校验”的回退。

Schema 是模型无关的 JSON Schema；供应商原生约束是可配置的辅助能力。供应商不支持原生结构化输出时，配置 structured_outputs=false，仍须经过同一个本地 Validator；本次没有新增第二供应商的真实验证。

参考：[Google 结构化输出文档](https://ai.google.dev/gemini-api/docs/generate-content/structured-output?hl=en)。文档指出仅支持 Schema 子集，复杂嵌套可能被拒绝；具体适配以本次请求证据为准。

## 新数据集与结果

保留第一轮 24 例 v1 及失败输出：`phase6-taskgraph-heldout.json`。当时澄清改善，但供应商适配误删了节点引用格式约束，正常图频繁使用下划线节点名或字面值 NODE:result，被原 Validator 拒绝；该轮不作为成功交付证据。

修正引用约束后，新写并冻结第二套 24 例人工 gold：`benchmarks/taskgraph_holdout_v2.json`。没有改写 v1 gold 或用模型生成 gold。v2 对允许的辅助银行只读步骤单独列清，避免将合理读取误记为未请求操作。每例分别运行保留的原协议与新协议，使用同一模型、4096 输出 Token、30 秒超时、无金融审批、无供应商回退。比较发生在同一个 v2 数据集内，不拿 v1/v2 分数作直接性能对比。

| 业务结果 | 原协议 | 新协议 |
|---|---:|---:|
| 正常只读任务完成并报告所需结果 | 5/10 | 10/10 |
| 金融提案正确生成并停在独立审批 | 1/2 | 2/2 |
| 明确补问指定缺失参数 | 0/4 | 3/4 |
| 危险请求在安装计划前被阻止 | 7/7 | 7/7 |
| 不存在资源被银行边界阻止 | 1/1 | 1/1 |
| 全部案例符合人工预期 | 14/24 | 23/24 |
| 实际银行资金写入 | 0 | 0 |

剩余失败 F14：模型识别出缺少目标卡片，但使用 resource_id 而不是提案需要的 target_id。系统生成的问题仍为“请补充目标资源”，但按严格 canonical gold 保留失败，不提高统计成绩。

这是一套“小规模、明确输入已由调用方提供”的 TaskGraph 测试，不是自然语言参数提取评测。原 100 例意图识别 67%、参数 87%、澄清 82% 和系统接纳 17/100 的结果没有替换，不能宣称自然语言整体接纳率已升至 95.8%。小样本、每协议每例一次，尚无统计泛化保证。

v2 首次真实评测后又禁止澄清请求客户补录交易记录、银行权威数据、分析候选或取消回执；原 24 条捕获输出在最终 Core 上离线重放，逐例核对状态和分数，未新增模型调用或修复输出。

## 文件

- 新增 `backend/llm/graph_protocol.py`；修改 `graph_proposal.py`、`providers/gemini.py`、`runtime/graph_runtime.py`。
- 新增 `tests/test_graph_protocol.py`，包含协议、安全、澄清、编译审批链与评测结果判定检查。
- 新增两套人工数据、`benchmarks/taskgraph_live_evaluation.py`，原协议代码及 SHA-256 保存在 `data/phase6-taskgraph-usability-before/`。
- `scripts/taskgraph_usability_demo.py`：离线模型 Fixture，实际 Gateway/Router/Core，临时 Virtual Bank；明确不算真实模型 Demo。
- `scripts/taskgraph_usability_report.py`：验证冻结数据、原文件哈希、全量日志，并重放捕获结果。

## 验证与 Demo

最终全量回归 **477 项 / 353.098 秒 / OK**，日志：`phase6-taskgraph-tests.txt`。其中原 Phase 6 的 449 项保留，加上前一部分 12 项统计检查、本部分 16 项协议与评测检查。校验与最终源码指纹：`phase6-taskgraph-usability-validation.json`。原 22 个测试文件与原安全编译器/Validator 的 SHA-256 保持不变。24 条真实捕获结果在最终 Core 上重放，逐例结果一致，资金写入为 0。

Demo：`python scripts/taskgraph_usability_demo.py`。先缺月份得到明确问题，补齐后完成 READ→ANALYZE→REPORT；另一个转账提案进入 WAITING_USER，实际资金写入为 0。结果：`phase6-taskgraph-usability-demo.json`。

真实评测复现：`python -m benchmarks.taskgraph_live_evaluation --dataset benchmarks/taskgraph_holdout_v2.json --output <新报告路径>`。需要用户本地已有的 GEMINI_API_KEY，模型可由 GEMINI_MODEL 替换。复现是重复评测，不能再把同一数据集称为新的独立 gold。

## 下一部分

第二供应商实测与 Router 切换。银行官方 Sandbox、Sepolia 外部锚定、DID/ZKP 和生产身份集成的原真实性等级保持不变。
