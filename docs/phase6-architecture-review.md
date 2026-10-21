# Phase 6 编码前架构评审

日期：2026-10-06。基线先执行 `python -m unittest discover -s tests -v`，371 项 / 310.955 秒 / OK，原始输出 `data/phase6-baseline-tests.txt`。测试哈希及全文件静态检查清单分别保存于 `data/phase6-original-test-hashes.json`、`data/phase6-source-review-inventory.json`。下述结论来自实际代码与 Phase 4–5 验收，不把接口存在当作外部能力完成。

| 审查项 | 状态 | 实际差距 |
|---|---|---|
| 六 Agent / Blackboard / V3 / CapabilityRegistry | IMPLEMENTED | Phase5 激活 Service；历史 profile4 保留。既有能力承诺不可机械改名 |
| BankAdapter 银行无关性 | PARTIAL | 方法隔离厂商，但 manifest 为旧规范工具名；read_domain 使用本地 SQLite 和 extended.resource |
| canonical naming | PARTIAL | proposal、执行权限、工具名为三个不同空间；需显式映射，不能把 payments.create 授予 Agent |
| 银行错误模型 | PARTIAL | Rejected 默认意味着银行已回滚；HTTP 超时/5xx 写请求不能沿用此假设 |
| 银行认证 | NOT_IMPLEMENTED | 本地固定客户 token；没有银行 OAuth、签名、刷新、撤销生命周期 |
| Gateway 可替换 Adapter | PARTIAL | 方法调用可替换，Service 构造硬编码 VirtualBank，Schema 只允许 CNY |
| 异步 Hard Verification | NOT_IMPLEMENTED | 本地当前余额/version+SETTLED核验；外部 CREATED/UNKNOWN 不能据此生成成功回执 |
| 真实 LLM 实验 | PARTIAL | Provider/Router/HTTP 已实现；15 fixtures 不是模型实验；环境检测到两类密钥，需探测实际可用性 |
| 外部 Anchor | NOT_IMPLEMENTED | LocalDevelopmentAnchor 已实现；UnconfiguredBlockchainAnchor 只有拒绝接口 |
| AA collection | PARTIAL | SPLIT_REQUEST 只生成份额；无 PaymentRequest、参与者独立授权、收款结算及恢复 |
| 通用规划 | PARTIAL | Planner 和 PlanValidator 固定模板；没有闭合语义 DSL、compiler、动态分支执行 |
| Scheduler | PARTIAL | 已持久化依赖/就绪前沿/时间任务，但安装限制模板；需要合法编译图安装、分支跳过与有界等待 |

## 架构决策

保留六 Agent 和全部冻结边界。新增 SDK 属银行适配边界；新增 Graph 属 Planner/Runtime；AA 属业务工作流而非新 Agent；Anchor 只在审计 worker 内。原 BankActionIR 字段、Contract 协议、V3 签名、历史 capability commitment 与 Merkle 协议保持。

银行规范命名使用复数资源空间，通过兼容映射保留旧 grant 与工具名，映射本身不授予权限。默认仍 VirtualBank，外部 Provider 必须显式配置。已有 IR 的 currency 字段可按 Adapter manifest 声明的币种核验，不进行 CNY→GBP 隐式兑换。只支持实际提供的能力，缺投资/卡能力不得从本地数据拼接成外部银行事实。

外部写请求提交前持久化幂等绑定。超时、5xx、响应丢失标记 UNKNOWN，不自动重试；已知 payment id 只做状态查询。HardVerifier 对外部状态重新读取并核对动作绑定、金额/币种/收款人、完成证据；PENDING/UNKNOWN 不发 VERIFIED receipt。生产默认关闭写；沙箱写也需 feature flag、来源/收款人白名单、整数限额和每动作明确授权，定时外部写默认禁止。

AA 持久化账单、参与者、请求、付款任务引用。参与者自行创建所属转账任务；付款继续经过原完整金融链。收款账户由银行受控 payee route 绑定，Organizer 不能替代参与者批准。零份额明确记录为无需付款，不能伪造一笔零金额转账。所有正份额 VERIFIED 且实际收款才 COMPLETED；拒绝不减去账单总额。崩溃不自动重新付款。

TaskGraphIR 为闭合语义计划：READ/ANALYZE/RESOLVE/COMPARE/DECIDE/PROPOSE_ACTION/WAIT_USER/WAIT_TIME/BRANCH/JOIN/REPORT。数据引用只能来自运行时提供的受控输入和已完成节点；银行资源必须来自 Gateway。LLM 不能提交执行、安全节点、授权或 endpoint/code。确定性 validator 检查 capability、归属、依赖、环、深度、等待/重试界限、写序列；compiler 为每个提案插入完整安全链，并隔离每个动作授权。Runtime 使用现有 Blackboard/Scheduler/消息签名，额外金融动作仍生成既有 IR 和单独业务任务，不共享审批。

## 安全与数据模型

新增附加表而不重写旧表：external_bank_operations（键/hash/provider/env/status/银行ID）、split_bills（组织者、总额、到期、状态）、split_participants（客户、份额、请求/任务/回执引用）、split_payee_routes（银行管理的收款路由）、task_graph_runs（语义图、编译图、状态与输入来源）。敏感 bank/API/wallet secrets 只在环境或专用文件，不能进入 Audit/Memory/模型数据集。配置中的 URL 只来自可信部署者，拒绝重定向和非 TLS 外部地址。Webhook 签名验证、防重复只能触发查询，不能替代结算验证。

链上仅 checkpoint hash/root/identifier commitment/schema/sequence，最小合约防重复并发事件。独立验证 tx、receipt、chain、contract、成功、event、root read-back、区块确认及重组，不接受仅 tx_hash。无钱包/资金/部署地址则如实 BLOCKED；不阻塞银行链。

## 实施顺序与验收

1. SDK+参考 Adapter+故障测试：验证银行无关请求、认证签名、状态和 UNKNOWN 不重试。
2. AA：独立身份/账户/授权，整数分摊、真实借贷与收款、拒绝/过期/取消/重复/重启测试。
3. TaskGraph：闭合 schema、validator、compiler、持久化运行；至少50人工规划案例，unsafe acceptance=0。
4. 外部 Anchor：最小合约、RPC read-back及伪造证据/确认/重组测试；独立 worker 配置。
5. 100人工模型案例，真实 Provider调用，raw 与 system 分别统计；路由真实可用性与故障注入分别报告。
6. 原371与新增测试全量回归，原两版 Security Benchmark、Banking Security和34例Banking Benchmark重跑，生成完整20项报告及逐项DoD。

不存在需要修改冻结架构的冲突。已有 assumptions/validation 的页首仍是 Phase4，正文为历史阶段；最新实际基线以 Phase5报告和本次371测试为准，最终文档会注明版本。

## 实施后的具体落点

此文件保留编码前评审。最终采用 split_bills JSON保存参与者、独立 split_payment_requests 表及银行管理 split_payee_routes；未创建单独 split_participants 表。图状态保存在原 Blackboard graph_state，并由HMAC和重新编译保护，未创建 task_graph_runs 表。General Graph 的每个动作在同一任务内有独立 ActionIR/hash/授权/合约；AA才是每参与者独立TRANSFER任务。最终事实、全部修改清单与外部能力边界以phase6-final-report.md、phase6-validation.json为准。
