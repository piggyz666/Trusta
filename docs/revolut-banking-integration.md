# Revolut 官方 Sandbox：前端与受限付款接入

2026-10-08。原 V1/Phase 6 Execution Boundary、Agent Registry、能力授予、TaskGraph Validator/Compiler、Scheduler 保持原实现；没有新增 Agent。默认 `start.ps1` 仍运行 Virtual Bank。

前端已通过真实 Sandbox 读取验证。用户逐笔确认后，向 Acme Corp. 的一笔 0.01 EUR 经过原可信执行链提交。初始 PENDING 后，独立操作员完成官方沙箱模拟结算，原验证器再次独立读回通过，生成唯一成功回执。金额、币种、收款路由、幂等标识、reference、费用、时间及余额均匹配。这是官方沙箱模拟结算闭环，不是生产清算。最新真实性状态见 [运行证据](revolut-banking-validation.json) 和 [最终银行读回记录](revolut-settled-readback.json)；[初始 PENDING 记录](revolut-pending-readback.json)保留为历史。

## 目录与改动

```text
backend/
  bank_sdk/
    presentation.py          # 已认证账户目录、收款人展示
    revolut_transfer.py       # opt-in Sandbox 付款、永久幂等登记、银行 read-back
    reconciliation.py        # 原已授权交易的只读补充核验
    revolut_auth.py           # 独立 READ/PAY 凭据与刷新；拒绝其他写端点
    configuration.py         # 显式 transfer_profile、白名单、上限
  banking/intent.py           # 显式 EUR 与 GBP 原生金额输入
  server.py                  # 展示/查询 API；原任务创建、授权链继续使用
frontend/
  index.html / app.js / styles.css
config/
  bank.revolut-transfer.example.toml
scripts/
  revolut_server.py           # 隔离数据库与本地 UI
  revolut_transfer_prepare.py # 银行同意后只创建 0.01 原生金额提案，不付款
  revolut_sandbox_connect.py  # 原 READ 入口 + 显式 --payments；独立存储凭据
  revolut_sandbox_settlement.py # 独立 Sandbox 操作员测试工具，不授予 Agent 模拟结算能力
start-revolut.ps1
tests/test_revolut_banking.py
tests/test_revolut_settlement.py
```

真实来源/收款账户白名单在忽略提交的 `config/bank.revolut-transfer.local.toml`。OAuth token、银行私钥、UI 登录密钥和隔离数据库存于项目外的受限个人目录；示例配置没有秘密。

## 数据流

读取：本地认证 → `/api/bank`、账户选择 → Gateway/归属校验 → Adapter → 官方 `GET /accounts`、`GET /transactions` → 原生币种余额/交易展示 → 原 Finance 分析 → V3 消息/Audit。

付款：显式来源/收款路由/币种/金额 → 原 Intent Normalization、Supervisor、Planner、Transaction → Blackboard → 原 BankActionIR → Capability/Schema → Policy → 用户逐笔 Authorization → 原 Execution Contract → Tool Gateway → Canonical Bank Adapter → Revolut Transfer Adapter → 官方 `/pay` → 原 HardVerifier → 只有通过核验才生成 Receipt/Audit。

模型、Agent、聊天文本不直接访问银行资金 API。银行 PAY 授权与 TRUSTA 逐笔授权是两个不同步骤。READ-only profile 永远不具备付款能力。

## 安全限制与技术决策

- 固定官方 Sandbox 域名，不允许配置为生产。仅 GBP→GB 与 EUR→DE 两个同币种路由，不支持兑换、定时付款、卡片资金操作、购买或赎回。
- 每个来源账户、收款人 UUID 与收款账户 UUID 必须在本地白名单。使用已有收款人，不申请 WRITE 或敏感卡权限，不增加收款人。
- 金额是整数最小单位；每笔最多 100，即 1.00 GBP 或 1.00 EUR。发送 JSON 时 Decimal 编码为精确数值，前端显示币种，不把外币标为人民币。
- 原执行前状态与账户 version 必须吻合；发送前检查合约 deadline。永久幂等 claim 先落盘，同键不同 hash 拒绝，有 bank_id 只查询，无 bank_id 的 UNKNOWN 永不重发 `/pay`。
- 付款 POST 不自动刷新重试。401、403 等明确拒绝与超时/5xx/损坏响应分别处理；HTTP 200 只表示已观察到提交结果。
- 独立 GET 核查银行 transaction ID、request_id、收款人/收款账户、金额、币种、reference、零费用、银行时间、账户余额/交易腿余额及 SETTLED。任何不一致都保持 MANUAL_REVIEW，无成功回执。
- 对于超时或延迟结算，`reconcile` 只读取银行，验证历史合约 seal、原用户授权、动作 hash 和提交时间。成功后保存补充回执；原 Scheduler 终态和历史步骤保持 MANUAL_REVIEW，不重发也不发新合约。
- 账户/交易归属由服务端身份绑定。前端切换账户取消当前对话中的未确认请求、清除旧报告，并在处理期间禁止切换；已执行交易不被撤销。
- V3 身份/签名/hash/nonce、Capability Control 和审计保留。Merkle 证明只说明本地记录包含性；没有新增 DID/ZKP，也不宣称区块链或银行签名背书。

## 运行与 Demo

只读：`./start-revolut.ps1`，打开启动器显示的本地完整链接。选择 GBP 账户，读取 2026-10 记录，点击消费分析示例并发送；切换 EUR 后余额显示 EUR，旧报告清除。外部 Sandbox 不提供的理财产品能力显示为不可用，原 Virtual 三场景继续保留。

付款准备：先运行 `python scripts/revolut_sandbox_connect.py --payments`，在原登录浏览器进入 `https://trusta.localhost/start`，只保留 READ 和 PAY。回调后本地确认。新 token 存在单独文件，原 READ 凭据不被覆盖；银行响应没有 scope 字段时只记录请求范围，不能声称返回权限已独立证明。

然后运行 `./start-revolut.ps1 -Transfers`。启动器验证凭据标记及当前账户/收款路由，启用受限本地 profile，创建一笔 0.01 原生币种提案并停在 WAITING_USER。打开历史任务，核对账户、收款人、金额/币种；只有用户点“确认转账”或明确确认该具体提案后才执行。授权过期使用原 renew，动作过期需要新提案。

付款返回 PENDING/UNKNOWN 时不要再次发送转账；使用“重新核对银行结果（不会再次付款）”。没有银行 SETTLED 证据时不能显示已结算。

官方 Sandbox 的状态可能需要由其测试控制面推进。Revolut 提供独立 simulation API，能把测试交易状态设为完成/失败；该 API 没有接入 Agent、Gateway 或核验器。若使用外部测试控制面推进，应在证据中明确标注“官方 Sandbox 模拟结算”，随后仍独立 GET 核验，不能称为生产银行结算。[官方 simulations 说明](https://developer.revolut.com/docs/guides/manage-accounts/api-usage-and-testing/test-flows-with-simulations)

此次独立操作员工具只对上述已授权、已提交的同一笔交易尝试一次模拟完成，银行返回 HTTP 403 / BANK_SCOPE_DENIED，未推进结算；再次读回仍为 PENDING。模拟操作有永久 claim，不自动重试，也不重发 `/pay`。业务 SDK 继续拒绝模拟端点。403 证明当前凭据无法调用该端点，尚未证明具体缺少哪一种银行权限，不能自行增加 WRITE/管理权限。[模拟尝试记录](revolut-sandbox-settlement-validation.json)。历史审计保留原观察值；报告已将明确 403 从“不确定响应”更正为“银行拒绝”，没有重试银行操作。

### 模拟权限排查：待银行侧人工同意

以下准备流程记录为历史阶段；本次用户已完成同意，最终结果见下方“首笔闭环结果”。

官方 API Console 的 [权限表](https://developer.revolut.com/docs/guides/api-console/signing-requirements) 将 Sandbox simulations 列为 Write；这是新 API Console 的产品权限，尚不能据此宣称当前旧版 OAuth 的 WRITE 已被证明为充分条件。当前 READ,PAY 下的实际 403 已记录，交易继续 PENDING。

`python scripts/revolut_sandbox_connect.py --simulation-control` 准备显式 READ,WRITE 授权试验。用户在原 Chrome 从 `https://trusta.localhost/start` 进入，若同意银行管理权限，只保留 Read your account details、Manage your account details；关闭 Make payments、Read sensitive card details。回调必须在同一浏览器完成本地确认。入口有效期20分钟，过期需重新启动。

WRITE 包含管理收款人等较宽权限，银行页面授权不能由本地程序代替。凭据单独存于私密目录的 `oauth-simulation-tokens.local.json`，不覆盖既有 READ/PAY 文件，不用于 Agent 或业务 Adapter。业务 SDK 在银行没有返回 scope 字段时也拒绝标记为操作员测试用途的凭据。此入口只交换 token 并 GET 账户，不付款、不调用模拟端点；成功读取账户不等于模拟权限验证成功。

本次仍使用同一银行证书/client_id；独立文件不等于独立银行应用或身份。新银行同意是否影响旧令牌需后续读取核实。原模拟失败 claim 保留；得到新授权后也不能直接重复旧脚本，需要明确核对既知拒绝和新权限，再受控推进同一银行交易。未知响应不得当作拒绝重试。

[53 项权限与回归专项测试](revolut-simulation-consent-tests.txt)通过（23.231秒），包含5项新增授权隔离测试、既有 bootstrap/SDK/付款认证及模拟保护。冻结 Core/Registry/规划器和原测试文件保持一致；这不是重新运行全工程测试。[排查准备记录](revolut-simulation-permission-plan.json)。

### 首笔闭环结果：官方沙箱模拟结算已核验

用户完成独立 READ,WRITE 试验同意，账户读取返回5个账户。银行仍未返回 token scope 字段，因此不宣称完整授权范围已被独立证明。旧业务凭据仍可读取原交易；新操作员凭据不进入业务 SDK。

先前 403 的报告经原审计 payload hash 与原模拟 claim 绑定，且原逐笔授权、合约 seal、动作 hash、银行交易读回及新凭据签发时间均校验通过。保留原 claim，以第二个永久排他 claim 进行一次显式操作员试验；只调用同一交易的 `/sandbox/transactions/{id}/complete`，没有第二次 `/pay`。不确定响应不允许开启该重试路径，第二次试验也不自动重试。

银行模拟接口返回 completed，原 Core 的只读补充核验返回 VERIFIED，唯一回执 `ec24b28f-c172-48ce-b3ef-d41417f69175` 对应银行交易 `05677bc9-99a7-e35d-0040-00ab31651da6`。随后独立读回20项检查全部通过，包含 completed 时间、账户/交易腿余额、原授权、回执 hash 与唯一性。原 Scheduler 的 MANUAL_REVIEW 历史不覆盖；reconciliation 保存金融结果 VERIFIED，前端显示“补充核验通过”。

[操作员尝试记录](revolut-sandbox-operator-settlement-validation.json)、[最终读回](revolut-settled-readback.json)、[13项专项测试](revolut-operator-settlement-tests.txt)（14.900秒）、[实际已结算页面验证](revolut-settled-browser-validation.json)。浏览器显示 EUR 余额、0.01 EUR 完成记录、回执和补充证据，原授权控件隐藏，页面操作 POST 为0，无脚本错误。

[回执证明](revolut-transfer-receipt-proof.json)用[项目既有注册表中固定的公钥](revolut-transfer-audit-public.pem)在独立验证进程通过签名与 Merkle 包含核验。公钥从经过 seal 校验的本地注册表读取，不采信证明自带的公钥；这是本地审计信任，不是银行签名、DID/ZKP 或外部链背书。付款提交总数1，模拟尝试总数2（首次403、独立授权后成功），生产写入0。

## 验证与限制

[浏览器实测](revolut-banking-browser-validation.json) 包含四种运行：合成只读、合成已结算付款、合成延迟结算补充核验、官方只读。四种均无浏览器脚本错误；真实银行模式付款 POST 为 0。合成付款绝不等同于官方资金执行。

[全量测试日志](revolut-banking-tests.txt)、[工程与官方运行记录](revolut-banking-validation.json) 记录最终测试数量、冻结文件校验与真实性状态。新增测试覆盖权限不足、逐笔授权、币种、金额/收款/费用错配、幂等、403、断连、损坏响应、迟到结算、归属与合约篡改，并覆盖 bootstrap 成功后浏览器断连不会错误报告银行失败。

本次全量 545 项通过，624.898 秒；其中 23 项新增银行业务测试。单个资源协议路径修正后，在新进程中补充运行的 [35 项专项测试](revolut-banking-targeted-tests.txt) 全部通过，33.777 秒。官方实测证实单个收款人用 `/counterparty/{id}`，单笔交易用 `/transaction/{id}`；列表仍用复数路径。[官方 GET 预检](revolut-readback-preflight.json)。原 36 个测试源文件及冻结 Core 文件校验一致。

本次真实付款后新增操作员工具的 [7 项专项测试](revolut-settlement-tests.txt) 全部通过（7.256 秒），包括明确 403、断连、防重复、记录错配、未授权任务、生产环境拒绝及业务 SDK 无模拟权限。545 项是此前全量结果，7 项是本次新增模块专项结果，未重新运行整套工程。

尚未提供生产接入、跨进程共享限流、商户类别映射、外部 AA 收款或其他银行实接。Sandbox 余额与月份数据属于当次运行，不是固定业务常量。

协议依据：[Business API](https://developer.revolut.com/docs/api/business)、[付款流程](https://developer.revolut.com/docs/guides/manage-accounts/transfers/bank-transfers)、[收款人查询](https://developer.revolut.com/docs/guides/manage-accounts/counterparties/retrieve-counterparties)。
