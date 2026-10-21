# TRUSTA 第一版后端交付（2026-10-08）

本轮按“安全缺口 → 六场景 → 密码学创新”实施。未修改前端、未新增 Agent、未接触生产资金、未重复发起 Revolut 沙箱付款。历史 Phase 文档保留其当时的状态；当前实现以本文件与本轮验证记录为准。

## 执行架构保持不变

Conversation / Intent Normalization → Supervisor / Planner → 现有业务 Agent → Blackboard / Message Bus / Scheduler。

任何金融写入继续使用：BankActionIR → Schema / Capability → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → Verification → Receipt / Audit。模型、Memory、DID、ZKP、事件或人工备注均不能替代该路径。

安全控制接在原边界上；年度统计只使用读取能力；事件生成待确认任务；密码学证明在独立辅助路径上运行。区块链锚定继续由异步审计 worker 处理，银行执行不等待链上结果。

## 安全缺口已补部分

- competition 配置按用户及上海日期累计人民币转账；日累计不超过1000元仍需逐笔确认，超过则要求 TOTP。不同来源账户共享额度；外币始终强认证，无隐式汇率转换。
- Gateway 执行前原子登记动作 hash 与幂等键。未知写结果保留当日登记，不自动重发。
- 真实 TOTP 绑定、一次性消费、加密存储、期限检查；强认证证明绑定精确动作，执行合约再次检查。历史演示口令不能通过 competition 强认证。
- 连续认证失败/银行写异常触发持久化金融锁；确定性的权威状态不匹配立即锁定。读取超时、未知结算状态和消费异常候选不被错误当成已证实欺诈。
- 独立操作员凭据与理由解锁；人工核查案件/备注不会改变银行事实、金融授权或成功回执。
- 银行结果不确定仍进入人工核查，恢复和重启不重付。原 DAG、Capability、消息验签与审计回归继续执行。

详细 API 和本地 MFA 边界见 [backend-safety-api.md](backend-safety-api.md)。TOTP 是本地认证器能力，不冒充银行 SCA、生产级身份恢复或抗钓鱼认证。

## 六场景后端覆盖

| 场景 | 当前能力 | 本轮补充与验证边界 |
|---|---|---|
| 智能转账 | 姓名/手机号/备注、定时、本地多人 AA、逐笔授权、状态读回 | 日累计升级与并发登记；EUR 任务同币种路由。官方 Revolut 的既有0.01 EUR证据保留，本轮未重付 |
| 账单分析 | 分类、月度汇总、异常筛查 | 十二个月年度聚合；跨月、重复记录、币种检查。报告明确仅覆盖已读取的已结算记录，不声称银行数据完整 |
| 理财操作 | 风险读取、比较、推荐、沙箱申购/赎回、现金/持仓/操作核验 | competition 申赎强认证；完整 Demo 验证申购和赎回。不等同真实券商交易或保证收益 |
| 卡片管理 | 冻结/解冻、限额、交易开关 | 申请记录 AVAILABLE→SUBMITTED；挂失→LOST，不得解冻。申请没有伪造已发卡状态 |
| 订阅/代扣 | 识别、逐项取消/停用 | 持久化幂等续费提醒队列，取消后旧提醒失效。队列为本地提醒，不发送外部消息或自动扣款 |
| 跨场景联动 | 只读分支、DAG、逐项审批、中断恢复 | 生日转账/支出审查事件、单次原子领取、后台触发、待确认任务；中断触发保守进入人工核查 |

卡片、理财、订阅写操作属于 Virtual Bank 实现。外部银行只开放对应 Provider Manifest 明确支持的能力；不能用本地模拟冒充真实银行发卡、商户取消或投资服务。六场景 Demo 共用隔离数据库，外部银行与模型调用均为0。

新增事件/提醒接口沿用原 Bearer 身份、Host/Origin 检查和用户所有权校验：

- `GET /api/reminders?account_id=acct-demo`：重新读取权威订阅/代扣并更新自身提醒队列。
- `GET /api/events`：自身事件。
- `POST /api/events`：字段必须为 `request_id, account_id, event_type, execute_at, parameters`；`execute_at` 为未来一年内的 Unix 时间。
- `BIRTHDAY_TRANSFER` 参数为 `{"recipient_name":"张三","amount":100}`，金额使用分；`SPENDING_REVIEW` 参数为 `{}`。不接受模型提供的脚本或任意事件代码。
- `POST /api/events/tick`，`{}`：只处理当前用户到期事件，产生待确认任务。
- `POST /api/events/{id}/cancel`，`{}`：只取消尚未触发的事件；已生成任务应走原任务拒绝接口。

后台使用原 `python -m scripts.scheduled_worker --watch`，现在同时处理 TIME 任务与到期事件。只运行一个 workflow worker；worker 用 `recover_workflows=False`，不把服务的活跃任务误判为中断。主服务重启恢复事件若停在 RUNNING，转人工核查，不另造新任务重发。

## DID：真实可解析身份，权限仍由 Registry 决定

`backend/security/did_key.py` 使用 did:key 的 P-256 多编码格式，支持公钥导出及无网络离线解析。测试含独立 GOV.UK 公共示例，未仅用自身生成的数据回读。

Agent 当前密钥、版本、身份、状态和 Capability Hash 被现有 Registry 签名绑定，绑定期限60秒。验证要求当前 Registry 一致；伪造能力、旧版本、撤销和过期均拒绝。身份解析本身不授予权限。

did:key 不支持原地更新/撤销：轮换密钥产生新 DID；旧 DID 仍可解析，但 TRUSTA 的当前 Registry 可以拒绝其信任。原 V3 消息 URN 保留兼容，`messages.did_implemented=false`描述的是该历史消息标识，不代表新增适配器未实现；新能力状态查看 `extensions.did`。

认证后接口：

- `GET /api/identity/did?agent=finance`：当前 Registry 签名绑定与 DID Document。
- `GET /api/identity/resolve?did=...`：仅解析支持的 did:key，拒绝 URL、其他方法和不支持的密钥类型。

不引入完整 VC 生态或额外身份 Agent。方法语义见 [did:key specification](https://w3c-ccg.github.io/did-key-spec/)，独立例子见 [GOV.UK Wallet](https://docs.wallet.service.gov.uk/issue-credentials/credential/)。

## ZKP：真实电路、证明和验证

命题：隐藏当前余额、风险等级及随机盲化值，证明余额足够且风险等级满足公开的任务条件。电路固定为 `BANKING_ELIGIBILITY_V1`，使用 Circom 2.2.3、Poseidon 和 snarkjs Groth16 / bn128；对金额、余额、风险和随机数进行位宽与范围约束，拒绝域溢出式绕过。

数据流：

1. 服务验证用户拥有待确认任务及精确动作 hash。
2. 通过现有只读 Gateway 从本地 Virtual Bank 读取账户、风险档案、必要时产品条件。
3. 产生任务/用户/挑战/期限绑定的公开 context；余额、风险、盲化 nonce 仅在内存与子进程 stdin 中。
4. 独立本地签发密钥为 commitment、公有输入、任务和期限签名；签发密钥加密存储，不借用审计或 Agent 密钥的角色。
5. 验证端使用配置中固定的 manifest hash、逐文件 hash 和本地信任的签发公钥；不接受证明自带的任意验证密钥。
6. 真实 Groth16 验证成功后原子消费挑战；再次使用拒绝。越权、过期、声明篡改、银行依据改变均拒绝。
7. 成功只返回“条件证明已验证”；原任务仍待确认，未产生执行合约或银行写入。

签发角色标明 `TRUSTA_LOCAL_SANDBOX_ATTESTATION`，不是银行官方签发。公开信息仍包括金额、必要风险下界、commitment 与任务引用；ZKP 不是隐藏全部业务元数据。私有事实不会写入 proof challenge、审计或 witness 文件；内部源状态校验使用带秘密密钥的 HMAC，避免小范围风险等级的公开哈希猜测。

当前仅支持本地 Virtual Bank 的 TRANSFER 和 INVESTMENT_PURCHASE 待确认动作。外部银行证明签发要另行定义权威凭证和数据来源，不静默沿用本地信任。

**初始化边界：本次是单机开发初始化，`production_ready=false`。** 公开 Powers of Tau 下载返回403，构建器显式使用 `--local-phase1` 完成本地两阶段初始化。证明与验证是真实算法；单机初始化不等于独立多方可信仪式，其安全假设不能用作生产资金保障。后续生产方案需要独立多方初始化或另选经审查的证明系统。

认证后接口：

- `POST /api/privacy/prove`，`{"task_id":"...","action_hash":"..."}`。
- `POST /api/privacy/verify`，`{"bundle":上述公开证明}`。

构建与配置：

```powershell
cd crypto_trust
npm ci --ignore-scripts --no-audit --no-fund
cd ..
# 需要本机可执行的 Circom 2.2.3 与 Node。
python -m scripts.build_privacy_proof --local-phase1 --output data/zkp-dev-v1
```

构建器拒绝覆盖非空目录；重建请用新目录并人工更新独立配置中的信任 hash。设置 `TRUSTA_ZKP_ENABLED=true`、`TRUSTA_ZKP_ARTIFACT_DIR` 和构建输出的 `TRUSTA_ZKP_MANIFEST_SHA256` 后，服务才加载该能力。默认关闭，普通后端启动不依赖 Node/Circom。

算法/初始化依据：[snarkjs primary documentation](https://github.com/iden3/snarkjs)。单机 Windows 原生工具能运行算法；虚拟机是部署/隔离选择，不是 DID/ZKP 算法的必需条件。

## 区块链：补齐 worker 路由，外部验收尚待条件

发现原独立审计 worker 没有使用服务里的外部适配器选择逻辑，已改为共用 `configured_anchor`。启用外部模式且缺配置时显式失败，不回退本地并冒称上链；正常本地模式继续标明 `is_blockchain=false`。

既有 Solidity 合约、独立 RPC 读回、确认数/重组检查和持久化发送记录保留，并在全量测试里执行本地 EVM 验证。**本轮未向外部 Sepolia 广播，也没有新的外部 tx hash。** 仍需用户控制的测试钱包、测试 ETH、RPC 与部署后的合约地址；只接收本机配置路径，不发送私钥。

已有步骤见 [external-blockchain-anchor.md](external-blockchain-anchor.md)。生产银行/真实企业开户不作为本版比赛后端的前置条件。

## 新增目录与主要文件

```text
backend/security/
  banking_controls.py    TOTP / 累计风险 / 金融锁 / 核查案件
  did_key.py             did:key 解析与 Registry 绑定
  privacy_proof.py       固定信任根的真实证明辅助服务
backend/runtime/
  event_plans.py         受控事件与本地续费提醒
crypto_trust/
  package.json / package-lock.json
  banking_eligibility.circom
  proof_runner.mjs
scripts/
  build_privacy_proof.py
  backend_safety_demo.py
  backend_complete_demo.py
tests/
  test_banking_safety.py
  test_backend_scenarios.py
  test_did_key.py
  test_privacy_proof.py
  test_backend_maintenance.py
```

原有修改涵盖 config、service/server、Policy/Authorization/Contract/Gateway、Finance/Intent/Conversation、BankingAction/Schema/Adapter/VirtualBank、SDK映射、Capability Registry、Banking/Graph runtime、scheduled/audit worker。前端未修改。

全量首轮还暴露了 Revolut 本地授权辅助服务在 Windows 上的拒绝响应问题：错误来源的 POST 未读取请求体便关闭连接，可能导致403响应被连接重置替代。`scripts/revolut_sandbox_connect.py` 现在先读取严格小于1024字节的受限输入，再执行原 Host/Origin/浏览器/CSRF 检查；不会提前消费授权码或请求银行。17项相关专项复验通过，首次失败日志保留，不启动真实 OAuth 服务。

验证与 Demo：

```powershell
python -m unittest discover -s tests -v
python -m scripts.backend_complete_demo
```

最终本轮全量结果见 [first-version-backend-validation.json](first-version-backend-validation.json) 和 [first-version-backend-full-tests.txt](first-version-backend-full-tests.txt)。六场景及真实证明 Demo 见 [backend-complete-demo.json](backend-complete-demo.json)。可选证明资产不存在时测试明确跳过，不能把“跳过”算作真实 ZKP 已验证。

修复后最终复验：**609项通过，0跳过**，42个模块在4个独立测试进程中执行；六场景完整 Demo、真实 Groth16 验证和独立公钥 Merkle 复核均通过。首次全量609项中出现1项授权辅助页连接错误，已修复并完成17项相关专项和上述完整复验，历史失败日志保留。

## 下一步界限

当前可复现的本地基础后端已形成，不将工程测试通过等同生产资质。外部 Sepolia 仍需真实网络验收；新六场景自由口语/新模型泛化仍应另建未用于调参的独立 Gold，历史24例 DeepSeek结果不代表所有新能力的自然语言成功率。本轮配置中的真实模型处于 disabled，未擅自启用外部调用。比赛材料和认证器/六场景/证明可视化 UI 留到用户安排的下一阶段。
