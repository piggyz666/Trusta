# Revolut 官方 Sandbox 只读业务接入

2026-10-08：已从独立 OAuth 连通工具推进到现有 TRUSTA Core 的只读业务接入。
实测账户/余额、交易读取、Finance 月度分析及语义 TaskGraph；本轮没有调用模型、支付、购入、赎回或其他银行业务写接口。

## 本轮结果

- 真实银行 Sandbox 返回 5 个账户。GBP 本月读取 5 条记录，其中 3 条已结算卡消费，2 条充值。
- Finance 的 BILL_ANALYSIS 与 `READ → ANALYZE → REPORT` TaskGraph 均 COMPLETED，汇总一致：净支出 27250 最小货币单位，即 272.50 GBP。
- 充值没有计入消费。未建立经过验证的商户类别映射，分类显示 UNCLASSIFIED，不猜测商户类别。
- 本次业务运行：12 次账户 GET、4 次交易 GET；资金业务写 0、模型调用 0。6 条消息通过现有 V3 验证，11 个 SDK 读取审计事件。
- 原审计链有效，本次新增签名 Merkle 检查点通过现有验证器和本地 Registry 固定公钥验证。没有提交区块链，不能称为银行签名证明或独立外部背书。
- 自动 refresh 在此前本轮真实刷新探针中成功一次；业务 Demo 复用尚有效凭据，refresh_count 为 0。两项证据分别保留，不能将 Demo 的 0 解释为未实现刷新。

银行响应数据可能随 Sandbox 操作改变；以上数值属于 [本次实测](revolut-readonly-validation.json)，不是固定产品数据。
过期凭据刷新探针见 `data/revolut-refresh-probe.json`。测试结论与全量结果见下方验证文件。

## 数据流与执行边界

```text
显式来源账户 + 月份 → Intent Normalization → Supervisor
  → 原 Planner / Scheduler / Blackboard
  → Finance Agent → Capability Control → Tool Gateway
  → Canonical Bank Adapter → Revolut ReadOnly Adapter
  → 官方 Sandbox GET /accounts、GET /transactions
  → 账户内交易腿映射 → 确定性 Finance 分析
  → 原 V3 签名消息验证 → Supervisor → 报告 / Audit

原语义 TaskGraph → 原 Validator / Compiler / GraphRuntime
  → 同一 Gateway / Adapter 读取 → 同一 Finance 汇总 → REPORT

RevolutAuth（基础设施）→ 固定 Sandbox POST /auth/token
  → 受限个人目录中原子替换 token 文件
```

OAuth refresh 的 POST 是凭据生命周期请求，不是银行资金执行。Agent 不持有 token、RSA 私钥或 BankTransport。
真实金融执行仍须 BankActionIR → Schema/Capability → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → Verification → Audit。
本 Adapter 不提供金融写能力，因此没有任何外部资金动作进入该链。没有新增 Agent、修改能力授予、弱化 Validator 或放宽执行合约。

## 文件和工程决策

```text
backend/bank_sdk/
  revolut.py                 # 三项只读能力、规范映射、精确金额、完整分页
  revolut_auth.py            # RS256 assertion、refresh、共享凭据锁、原子保存
  configuration.py          # 增加显式 Revolut Sandbox 配置分支
  transport.py              # 可注入 JSON Decimal 解码器，原默认保留
backend/agents/finance.py    # 显式币种注入与单币种分析
backend/runtime/scenarios.py# 从银行账户传递报告币种
backend/service.py          # 初始化时注入 provider 支持币种
backend/llm/explanations.py  # 外币金额展示正确币种
config/bank.revolut.example.toml
config/bank.revolut.local.toml  # 本机配置，忽略提交；无 token/私钥正文
scripts/revolut_readonly_demo.py
tests/test_revolut_sdk.py
```

1. Sandbox 主机固定，不接受任意 endpoint，不支持 production 配置。仅允许账户、交易 GET 与凭据刷新 POST。
2. Manifest 仅含 accounts.read、balance.read、transactions.read；所有写、卡敏感字段读取和理财能力拒绝。原 Agent Capability Registry 保留。
3. 银行小数金额使用 Decimal 精确转换为最小货币单位；仅支持 GBP/EUR/USD/AUD 的两位小数，超额精度、非有限金额、浮点输入及不支持币种拒绝。没有汇率换算。
4. 只映射来源账户自己的 legs，剔除卡号、CVV、授权码及其他账户的交易腿。card_payment 的支出映射 PURCHASE，card_refund 的收入映射 REFUND；充值和其他类型映射 NON_CONSUMPTION。
5. 月份使用 Asia/Shanghai，范围依据银行 created_at。只有 SETTLED 消费/退款进入统计；这不是按银行 settled_at 生成的正式对账单。
6. 分页边界重叠、去重并检查重复观察一致性。相同时间戳组导致游标无法推进、最大页数耗尽或响应错误时拒绝发布部分报告；尚没有官方大规模分页实测。
7. 原 CNY 绝对异常阈值不套用外币。相对中位数/重复交易规则保留；外币绝对大额阈值暂未配置。
8. 空月份 BILL_ANALYSIS 从银行账户得到正确币种；多币种 provider 的通用空记录 finance.summary 因无法确认币种而拒绝。没有为此修改冻结的图协议。
9. refresh 使用实例间线程锁和文件锁、30 秒余量、原子保存。401 只刷新并重试该 GET 一次；不自动重试银行写或不确定的 refresh。并发单次刷新目前验证为同进程两实例，跨进程文件锁实现尚未做实测。
10. token/私钥、Demo 的银行业务数据 DB 和消息密钥位于项目外受限个人目录。公开结果仅汇总、数量与哈希证明。没有发送银行数据到 LLM。

## 运行与用户操作

当前本机的配置和凭据已齐全，可直接运行：

```powershell
python scripts/revolut_readonly_demo.py --month 2026-10 --currency GBP
```

新机器先按 [OAuth 连通说明](revolut-sandbox-bootstrap.md) 注册自己 Sandbox 证书和 READ 授权，复制配置示例并填写自己的路径。
仅当银行撤销授权、refresh token 无效或证书过期时，需要重新授权/更新证书，不需要定期手动复制 token。
不能将生成代码或本地配置当成真实银行授权。

默认启动、前端和三个原 Virtual Bank 场景仍使用原配置；本轮没有自动切换日常服务或改造前端账户选择。
此 Demo 使用独立数据库，通过现有 Python 服务和 TaskGraph 运行，不等同于前端已连接 Revolut。
若显式配置现有 HTTP 服务，必须提供实际账户 UUID，现有前端默认 `acct-demo` 不能直接用于官方账户。
本机配置的 demo 用户与 Sandbox 公司映射是演示绑定，不是生产多租户身份/KYC。

## 测试与证据

全量 **522 项测试通过，400.270 秒**。其中本轮新增 SDK/认证/外币展示测试 24 项，已有 bootstrap 测试 12 项；此前工程基线 486 项保留并通过。
原有 35 个测试源码 SHA256 一致，核心安全边界及图 Validator/Compiler/Runtime 的冻结文件一致。测试使用合成银行响应；真实外部验证单独记录在 Demo 中。

[全量测试日志](revolut-readonly-tests.txt)、[工程验证记录](revolut-readonly-engineering-validation.json)、
[真实业务 Demo](revolut-readonly-validation.json)、[公开检查点](revolut-readonly-validation-checkpoints.json)。
原三个 Virtual Bank HTTP Demo 再次通过，账单/理财读取后余额不变、审计有效、任务重启保留，见
[本轮兼容 Demo](revolut-virtual-regression-demo.json)。该转账仅发生在隔离 Virtual Bank，不能计为 Revolut 付款实测。
检查点文件附带公钥仅供检查；信任该公钥必须来自本地 Registry 固定或独立可信渠道，不能仅因证明文件携带公钥而相信它。
已有测试文件和核心安全文件的原始 SHA256 对比记录在工程验证文件中。

## 剩余范围

只读接入已实测，官方 Sandbox 金融执行尚未完成：付款权限、受限收款人配置、小额动作、用户授权、原合约执行、异步状态和执行后 read-back 需另一个独立阶段。
Revolut 理财/风险画像、跨进程共享银行限流、跨租户生产部署、前端账户选择、外币绝对异常规则与可信商户类别映射也未完成。
已有 Virtual Bank 转账/理财场景继续保留。Sepolia 外部锚定、DID/ZKP 的原剩余状态不变。

协议依据：[官方鉴权指南](https://developer.revolut.com/docs/guides/manage-accounts/get-started/make-your-first-api-request)、
[官方交易分页指南](https://developer.revolut.com/docs/guides/manage-accounts/accounts-and-transactions/retrieve-transactions)、
[Business API](https://developer.revolut.com/docs/api/business)。
