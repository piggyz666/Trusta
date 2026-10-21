# Bank Integration SDK（Phase 6）

Core 调用 ToolGateway 的规范工具，兼容桥将历史签名 grant/工具名投影到 SDK 规范能力；厂商路径仅存在于厂商 Adapter。默认 Provider 为 VirtualBankProvider，配置 TRUSTA_BANK_CONFIG 后显式替换。SDK 不授予 Agent 写权限。

`BankRequest(capability, customer_id, parameters, action, idempotency, deadline)` → Provider → `BankResponse(data, provider, environment, status)`。Manifest 包含真实可用能力、币种、异步性与限流。Auth、Webhook、Verification 是独立协议；错误只向 Core 暴露稳定码。金融输入始终是整数最小币种单位，不做兑换。

| 规范空间 | 历史工具例子 | Virtual | Starling Reference |
|---|---|---|---|
| accounts.read / balance.read | account.get / account.get_balance | 已测 | 映射已测，未 live |
| transactions.read / payees.read | transaction.list / payee.resolve | 已测 | Feed / 默认英国银行 payee，未 live |
| payments.create / payments.status | transfer.create / transaction.get | 已测 | 官方 local payment/order/payment/feed，未 live |
| cards.read/freeze/unfreeze/limit.change/controls.update | card.* | 已测 | 不声明可用 |
| subscriptions.read/cancel | subscription.* | 已测 | 不声明可用 |
| direct_debits.read/disable | direct_debit.* | 已测 | 不声明可用 |
| investments.products/portfolio.read/purchase/redeem | wealth.list_products / portfolio.get / investment.* | 已测 | 不声明可用 |
| customers.risk.read / operations.read/evidence.read | customer.get_risk_profile / operation.* | 已测 | 不声明可用 |

写路径：BankActionIR → Schema → proposer/executor Capability → Policy → Authorization → Execution Contract → Tool Gateway → SDK/Adapter → Bank → Status Query → HardVerifier → Receipt → Audit。SDK 对服务器受控 Adapter 的替换不产生公开绕过接口。

Starling PAT 从部署环境读取，可显式到期/撤销；写请求使用登记 RSA keyid 与 SHA512/PKCS1v15 签名，校验请求目标、日期和 body 摘要。OAuth consent/token acquisition、mTLS 没有实现，不能声称已验证。只有此目标所需 PAT/登记签名钥列入外部配置。

生产/沙箱写都默认关闭；写需来源与 payee 白名单、最大测试金额、Core 独立审批。外部定时付款默认禁止。幂等键/hash/provider/environment 在 PUT 前落盘：既有 bank_id 只查询；无 bank_id 的 UNKNOWN 永不自动重新发送。已知 4xx 拒绝与写 5xx/超时/损坏响应的“不确定是否已执行”分开。

状态词汇为 CREATED/PENDING/AUTHORIZED/PROCESSING/SETTLED/FAILED/REVERSED/UNKNOWN；Reference 实际返回可证明的子集。HTTP 成功仅为 CREATED；ACCEPTED 仅为 PROCESSING。只有完成时间、order/payment 金额币种/收款人/引用一致且账户 Feed 唯一匹配 SETTLED/OUT，HardVerifier 才能 VERIFIED。UNKNOWN/PENDING 无成功回执。Webhook 校验原始 bytes 的 RSA-SHA512、事件 hash 防重复，只提供 QUERY_TRIGGER_ONLY。

Transport 固定 HTTPS（本地测试允许 loopback HTTP），拒绝重定向/跨域路径，有限响应体，严格 JSON；401/403/404/409/429 规范化，429 保留 Retry-After。Manifest 声明 PAT 5 requests/sec、1000/day；Adapter 对真实官方端点实施单进程滚动5/s预算，等待后才生成签名并再次检查写deadline。协议fixture免除网络节流，limiter用可控时钟测试。未实现跨进程或本地持久化日配额，服务端429仍为权威；没有自动写重试。

测试：tests/test_phase6_sdk.py 的真实本地 HTTP transport、RSA 签名/撤销/到期/Webhook replay、provider protocol fixtures、Core 审批/结算核验/未确定写及重复调用。fixture 不等于官方 sandbox。

Feed 在 Adapter 内投影为 id/customer_id/account_id/整数 amount/currency/occurred_at/status/direction 等规范字段，不向 Core 暴露 feedItemUid 等厂商字段。未验证消费/退款分类则 kind=UNKNOWN、category=UNCLASSIFIED；不得把任意入账当作退款。Starling消费映射仍未实测。2026-10-08后Finance从初始化注入provider支持币种，仍禁止混币种汇总和隐式兑换。

## Revolut Sandbox 只读扩展（2026-10-08）

新增固定Sandbox Adapter和RS256 OAuth自动刷新，仅声明accounts.read、balance.read、transactions.read。真实账户/余额、交易读取、GBP Finance BILL_ANALYSIS及TaskGraph已通过原Gateway/Capability/V3消息/Audit验证；资金写能力拒绝，默认Virtual保留。Decimal金额映射、账户内legs、充值排除、分页完整性以及权限/签名/刷新异常通过本地测试。原安全链、Graph Validator/Compiler和能力授予没有修改。

本轮全量522项测试通过，24项为新增SDK/认证/原生币种测试；原三个Virtual HTTP场景再运行通过。银行未返回token scope字段，不把请求READ视作银行返回的权限证明。详细文件、实接证据及业务限制见[Revolut说明](revolut-readonly-integration.md)。

官方依据：[Starling Public API](https://developer-sandbox.starlingbank.com/docs)、[官方 SDK](https://github.com/starlingbank/starling-developer-sdk)、[签名示例](https://github.com/starlingbank/api-samples)。Live 状态见 reference-bank-validation.json。

## Revolut opt-in 小额付款与展示

后续增加独立 transfer_profile，固定 Sandbox、GBP/GB 与 EUR/DE 同币种白名单、每笔最多100最小单位。原 readonly profile 保持只读；新 profile 的银行 PAY 同意不能代替 Core 逐笔 Authorization。POST claim 先落盘，不确定结果不重发；只读 reconcile 依照原合约/授权核查并保存补充回执，保留原 MANUAL_REVIEW 历史。金融写不经过新增公开捷径，原 Gateway/Registry/Validator/Compiler 不变。

545项全量测试通过；23项为新增业务测试，最终协议端点版本另通过35项专项测试。前端与单个资源 GET 已官方实测，合成付款/恢复与官方资金执行分开统计。实接付款状态见 [最新运行记录](revolut-banking-validation.json)，配置、目录及 Demo 见 [说明](revolut-banking-integration.md)。
