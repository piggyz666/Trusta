# LLM 定向优化修改说明

## 已实现（IMPLEMENTED）

- compact_v2 schema 按相同参数形状分组，函数枚举与参数对象绑定；共享 $defs 控制长度。金融提案参数在输出 schema 中只允许显式 input 引用，原 Validator 继续校验值、权限和来源。
- Tool 说明区分周期交易原始历史、分析候选、银行订阅权威记录；提供账单分析、周期匹配、金融提案之后读取等完整 DAG 示例。明确顺序词需要依赖，report 必须覆盖每个请求结果。
- 显式 inputs 只传递类型、action_type 枚举及缺少的引用名，不序列化其金额、账号、资源值或银行数据。用户 goal 原文仍进入推理，可能包含金额或姓名，按 restricted 分类并受 Router 的数据审核约束；不宣称自然语言已自动脱敏。不根据 goal 或 gold 补造金融参数。
- `graph_repair_attempts` 默认为 0，可配置为 1。结构重规划和已定义的临时传输失败共享最多一次追加请求、同一 Provider、同一总 deadline。结构反馈只包含限定错误码，不回显原候选或客户数据；重新走 Router 资格检查和原 Validator。金融规则、授权和金额错误不自动修复。
- 未通过验证的有费用响应同样记录 usage、实际 model、原始输出 hash 和失败原因。Gemini usage 计入 thoughtsTokenCount。RemoteDisconnected 等已定义连接中断单独归为 Provider unavailable，内容错误保持不可重试。
- `ProposalDialogue` 保存签封的显式本地槽位；补充字段限定为原问题的缺字段，检查所有权、版本、一次消费与篡改。新提案使用新的 task_id，不继承旧动作或授权。它是后端 SDK 层，尚未接到 UI。

## 文件范围

修改：`backend/llm/compact_graph.py`、`configuration.py`、`graph_proposal.py`、`providers/gemini.py`、`providers/http_adapter.py`。

新增：`backend/llm/proposal_dialogue.py`、`backend/audit_trust/independent_anchor.py`；三个 targeted benchmark 文件；独立配置/Anchor 验证脚本；三份新测试模块。原有测试和所有金融执行、安全通信、Merkle/DID/ZKP、银行适配代码不修改。

## 配置方式

继续使用原 Gateway 的 Provider 配置、敏感度审核与 route 白名单。可在 gateway TOML 增加 `graph_format = "compact_v2"` 与 `graph_repair_attempts = 1`；后者只影响提案，不改变 Graph 节点自己的 STOP/max_retries=0 或执行幂等保护。模型名称、endpoint、密钥变量仍由 Provider 配置决定，业务 Agent 不硬编码模型。

本轮真实测试固定 deepseek-flash 和 gemini-3.1-flash-lite，并记录返回的 model；前后评测使用相同模型名称、合成数据、scorer hash。输入/输出预算、网络超时和错误均保留，不能把临时网络失败从主分母中剔除。

## 测试纪律与局限

Development 可多次运行，失败迭代保留。Locked 使用同一 60 条样本作配对；Final 120 条最后一次执行，不用其结果修改代码。三个数据集在推理前冻结 SHA-256；scorer 在正式配对前用专门测试修正并冻结。新 Final 是工程上的一次验收，而非第三方作者盲测；同一作者设计了数据与方案。

数据包括多目标和先后依赖，但合法场景中每个金融任务只有一个金融提案；不宣称完成多笔任意金融 DAG 的模型 Benchmark。原运行时的多动作串行保护仍由原测试验证。缺字段评测的输入来自显式本地槽位，Raw Slot 指标测引用绑定，不能等同于任意自然语言金融金额提取正确率。

本轮无真实银行客户数据、银行付款或链上交易。模型测试中的 WAITING_USER 表示正确生成待确认提案，未执行资金操作。
