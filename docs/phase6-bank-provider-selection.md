# Reference Bank 选择（2026-10-06，编码前）

比较公开官方资料；未完成注册、未调用有凭据银行 API。地区实际开户/批准仍以提供方审核为准，不推断中国用户生产可用性。

| 项目 | Starling Public Sandbox | TrueLayer Sandbox | HSBC Open Banking Sandbox |
|---|---|---|---|
| 类型 | 官方银行沙箱 | 第三方开放银行沙箱 | 官方银行沙箱 |
| 开发访问 | 开发者 portal 注册、创建测试客户；不要求先成为批准TPP | Console注册应用、商户账户、上传公钥 | 注册、激活、DevHub项目及HSBC测试证书 |
| 认证 | OAuth/PAT；支付附加消息签名；正式TPP条件独立 | OAuth client credentials、支付签名；付款还需用户银行授权 | OAuth/DCR、证书/mTLS按市场API配置 |
| 账户/余额/交易/收款人 | Public API提供，可从同一测试账户读取 | Data API与Payments需分开设计；支付商户账户可读 | AISP与PISP按市场分离 |
| 支付与状态 | Payment order及其payments查询；不能将ACCEPTED泛化为结算 | v3支付、授权、状态/结算 | PISP支付状态查询 |
| 通知 | V2 webhook RSA-SHA512签名 | 支付webhook | 市场/产品支持差异，需对应项目确认 |
| 幂等 | 请求externalIdentifier；本地必须保留同键/hash和不确定写 | 支付幂等键，签名请求 | 对应API规则；不能假设跨所有产品一致 |
| 限流 | 429/Retry-After；PAT公开限制5/s、1000/day，可能变更 | 需按实际应用限流配置，本文不编造数值 | 项目文档确认，本文不编造数值 |
| 文档 | 官方SDK及签名/webhook示例，公开可查 | 快速入门与API reference完整 | 官方指南成熟但安全接入更重 |
| 凭据难度 | PAT只读最少；写需注册签名公钥/KID | clientsecret+签名key+用户redirect | client credentials+测试证书/DCR |
| 地区/复现 | GBP原生；生产UK权限单独审核；比赛可建测试客户 | UK/EU业务覆盖与Sandbox模拟；商户支付目标不同 | 按市场/企业角色审核；比赛证书准备较多 |

选择 **Starling Public Sandbox** 为 ReferenceBankAdapter。原因是银行官方来源、同一账户的信息与付款接口、请求签名/独立状态查询可证明SDK映射；不选择需要预先批准的Starling Payment Services接口。第二家不是本期硬性项，VirtualBank canonical wrapper作为第二种实现验证可移植性。

计划实现 accounts.read、balance.read、transactions.read、payees.read、payments.create、payments.status。卡/投资/订阅没有经过资料和权限确认，manifest不声明。GBP不能伪作CNY；SDK原生GBP，Core按manifest核验币种。ACCEPTED可能代表延迟接收，只有完成时间、未拒绝且权威feed已SETTLED并匹配金额/账户/方向才形成settlement证据。

REQUIRED_EXTERNAL_CONFIGURATION：TRUSTA_BANK_CONFIG 指向 TOML；读需要 BANK_ACCESS_TOKEN 与 customer_id。写还需 key_path/key_id、source_accounts/payees/maximum_amount/writes_enabled，详见 config/bank.starling.example.toml。沙箱/生产按 environment 固定官方 URL，生产默认关闭写。Webhook 公钥由调用方可信配置传入；未实现 OAuth token acquisition 或自动 refresh，不要求虚构 OAuth/client-secret 字段。

Exact Next Step：在Starling开发者portal创建Sandbox应用和账户持有人，生成所需read scopes的Sandbox access token；需要测试付款时上传签名公钥，记录KID，登记测试账户/收款人白名单。私钥与token放环境/本地密钥文件，不发到聊天。执行reference validation脚本，先只读；支付验证要通过TRUSTA单动作授权且限额足够小。缺凭据状态BLOCKED_BY_EXTERNAL_CREDENTIAL，绝不能写official_bank_sandbox_verified=true。

来源：[Starling官方Public API](https://developer-sandbox.starlingbank.com/docs)、[PISP状态说明](https://developer-sandbox.starlingbank.com/docs/pisp)、[官方SDK](https://github.com/starlingbank/starling-developer-sdk)、[官方签名/Webhook示例](https://github.com/starlingbank/api-samples)、[TrueLayer快速入门](https://docs.truelayer.com/docs/quickstart-make-a-test-payment)、[HSBC接入说明](https://develop.hsbc.com/knowledge-article/get-started-open-banking-apis)。
