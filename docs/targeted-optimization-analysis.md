# TRUSTA V1 定向优化架构审查（2026-10-09）

本轮仅处理真实模型规划质量和现有 Sepolia Anchor 的独立验收，不改变六 Agent、金融执行安全边界、V3、Merkle、DID/ZKP 或银行适配器。

## 当前实际基线

44 个模块、640 项测试已经重新运行。首次四进程运行有 1 个错误：`test_phase5_evaluation` 的六场景 HTTP 演示收到 Windows `WinError 10055`（套接字资源不足），其余模块通过；原始日志保留在 `data/targeted-baseline-logs/`。该模块未经修改单独复测，6/6 通过，耗时 33.588 秒。后续完整回归使用低并发独立进程，并保留首次失败记录；不能把首次运行写成零失败。

## A：根因与方案选择

源码与上一轮 raw response 对照发现：

1. compact_v2 schema 将所有函数名称与所有参数形状分别枚举，未绑定函数和参数。提案 prompt 没有充分说明输出类型，唯一示例是无依赖报告。模型会产生 `List:records` 等不存在的输出、将原始周期历史当成 finance.recurring 的分析候选，或把 account_id 绑定到 month。原 Validator 的拒绝是正确的，应改善输入契约而非放宽它。
2. 部分旧 gold 使用 `balances.read`、`risk_profile.read`、`products.read`，实际 registry 为 `balance.read`、`customers.risk.read`、`investments.products.read`。这会将已完成或正确等待的任务误判为失败。保留旧报告；新评分使用注册表中的规范能力，预先冻结后才能评测。
3. 现有提案没有结构错误后的安全重规划；无效响应的 usage 在验证之前尚未记录。可添加默认关闭、最多一次、同一 Provider/同一 deadline 的修复，仅返回结构错误代码，绝不复制客户值、引入权限或绕过 Validator。所有尝试保留，分别统计首个原始输出与最终接纳结果。
4. 现有金融确认由 Compiler 自动插入，额外 wait.user 会降低可用性。改善说明与链式示例。缺字段先澄清，已有显式槽位保留在本地；不能从提示词补造资金参数。
5. 当前 Router 已约束敏感度、Provider 允许范围、复杂度与预算，保持原实现。Provider 的输出格式差异只作序列化适配。

实施顺序：固定原源码和测试哈希 → 建立 Development / Locked / Final 三套独立合成数据及评分哈希 → 在原代码上执行 Locked pre → 改善闭合 schema、工具语义、上下文和有界重规划 → 仅 Development 调整 → 同配置 Locked post → 冻结最终代码后 Final 一次验收。旧 Final 不用作新的调参集。

三层指标独立呈现：Raw（首次响应的操作/引用/依赖/澄清）；Validated（图接纳、权限、grounding、编译、误拒）；E2E（只读完成、正确等待、澄清、提案、拒绝）。WAITING_USER 不等于付款完成；攻击被拒绝不计入合法业务完成率。目标为合法与缺字段 ≥90%，不能以牺牲安全换取分数。

## B：Anchor 根因与方案选择

当前环境未配置 ANCHOR_RPC_URL、ANCHOR_CONTRACT_ADDRESS、ANCHOR_WALLET_PRIVATE_KEY、ANCHOR_VERIFY_RPC_URL。钱包地址、余额、合约存在和 Worker 可用性因此尚无法外部核实。现有 submit 已持久化签名交易并以 checkpoint hash 幂等，不为不确定广播重新分配 nonce；inspect 校验链、回执、事件、确认数、历史/当前 read-back 和重组。

真正缺口是独立验证入口：现有 inspect 依赖提交数据库中保存的 expected body。新增只读验证器与脚本，以公开的签名 checkpoint、预先信任的公钥/合约/发布者和交易哈希为输入，不读取原提交数据库或钱包。检查签名、固定链、合约、交易参数、回执、事件、确认、两次 canonical block/read-back；支持第二 RPC 交叉复核。离线 fixtures 的成功明确标记 LOCAL_VERIFIED，不能生成假的真实外部证明。

不部署或广播。补齐凭据后，任何消耗测试 ETH 的操作另行说明网络、目标合约及 gas 上限并等待明确批准。现有部署 gas 上限 1,000,000、gasPrice 上限 100 gwei，理论上限 0.1 测试 ETH；现有 commit 上限 200,000，理论上限 0.02 测试 ETH。实际审批须针对已核实目标，不能以这些上限视为授权。

## 风险与验收

复杂 union schema 可能超出 Provider 支持范围；使用统一语义契约和 Provider 既有适配，任何不支持情况明确失败。重规划可能增加费用或消耗超时；默认关闭、最多一次并保留真实 usage。槽位补充不得继承旧授权或混淆用户；采用显式本地参数、所有权与版本校验。模型别名可能变更；固定请求名称并记录实际返回 model，模型不一致的配对不冒称因果收益。

完整回归保留全部原 640 项断言，并新增 schema/重规划/澄清/独立 Anchor 攻击测试；另运行 Banking Task、Phase 4/5 Security 和 Banking Security benchmark。核对冻结模块和原测试哈希。真实模型费用未经本轮授权前不发请求，最终报告不得把 NOT_TESTED 或 BLOCKED 项写成完成。

价格依据：[DeepSeek 官方](https://api-docs.deepseek.com/quick_start/pricing/)、[Gemini 官方](https://ai.google.dev/gemini-api/docs/pricing)。费用统计以 usage 和预先固定的非缓存保守单价估算，区别于供应商账单。
