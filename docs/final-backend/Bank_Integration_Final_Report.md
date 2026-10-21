# 银行集成最终报告
官方 Revolut Sandbox 状态 LIVE_TESTED；生产银行资金操作 NOT_APPLICABLE。本轮无银行写入和模拟器调用，没有重复付款。

新 OAuth refresh + 真实账户/余额/交易远端读取，发现 5 个账户；GBP 账单净支出 27250 minor units，排除 2 条非消费数据，Core Finance 与 TaskGraph 汇总一致；V3 accepted=6，审计链与签名 checkpoint 均通过。没有把 GBP 交易套 CNY 大额阈值；类别保留 UNCLASSIFIED 而非编造商户类别。bank-live-read.json 记录请求分类及前后金融表行数。

历史获准唯一 0.01 EUR → Acme Corp. 官方沙箱付款：bank transaction `05677bc9-99a7-e35d-0040-00ab31651da6`；本轮 GET 读回 SETTLED，金额/币种/recipient/request_id/action_hash/reference/fee/余额/回执/对账/唯一性/审计/证明共 14/14 检查通过。Receipt `ec24b28f-c172-48ce-b3ef-d41417f69175`。早期 403 已在原操作员获准 READ,WRITE 模拟结算试验后解决，不再按早期 PENDING 报告。

结算是官方远端沙箱返回，且历史使用官方沙箱模拟器推进；不是生产真实清算，也没有 bank-signed attestation。单银行回执读回不是第三方独立银行证明。本轮 simulator_calls=0，business_write_calls=0。

银行抽象仍使用 Adapter Interface → Capability Manifest → Provider Connector → Trusted Gateway；Revolut 特定字段在 Connector/映射层，unsupported 能力在 HTTP 前拒绝，凭据不发给模型。SDK 的两实现及其他 manifest 测试说明扩展方式，不能宣称已接全球所有银行。外部 AA、理财买赎、卡与订阅写操作当前 manifest 不支持，标为 BLOCKED_EXTERNAL；对应虚拟银行已完成实际账本状态验证。
