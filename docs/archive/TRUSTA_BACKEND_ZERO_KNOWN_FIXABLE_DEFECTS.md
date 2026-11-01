# TRUSTA V1 后端缺陷收敛总报告

交付日期：2026-10-11（Asia/Shanghai）。本机记录的实际测试、RPC 与银行读取时间使用各证据中的 UTC 时间；不得将这些快照解释成阅读报告时的最新报价。

## 结论与验收范围

**本轮确认、复现并修复的工程内功能与安全缺陷均已关闭；最终全量回归 781/781，通过后再次执行新增攻击与边界测试 44/44。没有待处理的已确认 P0/P1 可利用缺陷。** 原有 737 项测试文件未修改，没有删除测试、放宽金额/权限规则、关闭 Validator 或使用 mock 替代官方银行读取验收。

这表示当前 V1 本地后端在本报告覆盖的功能、配置与威胁模型下完成收敛，不表示不存在未知漏洞，也不表示已经具备生产银行服务、全部 Agent 的操作系统隔离或生产 ZKP 信任设置。下文明确保留静态类型覆盖债务、上游依赖公告和外部验收限制。

**本轮真实钱包签名 0、链上广播 0、测试 ETH 消耗 0、外部银行业务写入 0、新增付费 LLM 调用 0。** 合约本地 EVM 执行、临时测试密钥签名和 Virtual Bank 写入均与操作员钱包、银行凭据隔离。银行 OAuth 认证签名不属于链上钱包签名。

唯一总报告为本文件；辅助 JSON、原始测试日志及哈希清单位于 [证据目录](D:/TRUSTA金融科技/data/zero-defects)。

## 审查方法与架构决策

先以实际源码和当前 Windows 环境建立不可覆盖的源文件哈希基线，再重新执行 737 项原有测试。随后进行历史故障复现、跨 Service 与跨进程检查、运行中恢复检查、金融提交后故障注入、证据篡改、恶意 HTTP/Provider 响应、密钥权限检查、合约本地执行和外部只读验证。修复后多次运行关联测试和全量回归，最后再审查修改面与攻击结果。

没有引入需要改变冻结架构的冲突；采用现有模块内的互斥、事务、状态绑定和拒绝机制修复。六 Agent 仍为 Supervisor、Planner、Transaction、Finance、Wealth、Service。Blackboard、Message Bus、Scheduler、Trusted Message V3 及原有注册表继续使用。

金融写操作仍走以下边界：

`BankActionIR → Capability Check → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → HardVerifier → Receipt/Audit`

LLM 只能提出受约束任务图；Runtime 验证操作注册表、输入引用、资源归属、DAG 与授权条件，再编译为确定性执行。模型 JSON、消息签名、DID、ZKP、用户条件选择和 Memory 均不替代金融授权。付款后校验异常进入人工复核，禁止将“请求发出”当成“交易成功”，禁止自动重复付款。

新增 `backend/network.py` 与 `backend/security/local_files.py` 分别集中总时限读取和本地秘密文件保护；没有新增业务 Agent 或 UI。数据库认领复用原有持久化机制，不使用超时抢占金融任务。只对已证实进程死亡的非金融维护任务允许恢复认领。

当前目录不是 Git checkout。因此采用前后 SHA-256 比对记录修改，未伪造提交、PR 或历史 diff。[源文件基线](D:/TRUSTA金融科技/data/zero-defects/source-before.json)、[最终源文件哈希](D:/TRUSTA金融科技/data/zero-defects/source-after.json)、[完整修改清单](D:/TRUSTA金融科技/data/zero-defects/source-changes.json) 可直接核对。

## A. 已修复并验证的问题

下表按根因归组，同一组可能涉及多个入口；不把重复断言或静态扫描提示计为独立漏洞。

| 编号 | 复现问题与根因 | 最终处理 | 验证 |
|---|---|---|---|
| Z01 | 第二个 Service 启动时把仍在执行的 graph/legacy/event 任务当成中断任务；认领和 RUNNING 发布存在窗口 | 先认领再发布运行状态，启动恢复在同一事务重新读取任务和存活认领；运行中任务不被重置 | 新增 graph/legacy/event 存活恢复、真实多进程用例及原有恢复测试 |
| Z02 | 信号、授权、取消与推进缺少覆盖整个状态变化的跨实例认领；进程内锁不能保护跨 Service | 持久化认领覆盖完整 mutation/execution；仅同线程同控制器允许指定重入；graph 门锁使用弱引用 | 信号并发、两 Service、任务所有者与重复推进负例 |
| Z03 | 平行只读节点等待时持有 Service 锁，可形成互相等待 | 在受认领保护下释放 Service 锁执行平行读取，保存阶段重新校验 | 平行读取、DAG 多任务、超时/故障恢复 |
| Z04 | split 建账/参与人/审计非原子；重复 prepare 可生成多个提案；状态被篡改仍可能显示 PAID | bill 级认领，原子建账，状态 HMAC 封印；历史无封印状态仅在匹配完整审计哈希时接受；PAID 必须绑定已验证回执及唯一入账 | split 并发、封印篡改、历史审计合法/非法、参与人隔离 |
| Z05 | AA 组合缺少注册表参数引用支持，空参与人和合法组合行为不一致 | split.request 引用确定性规范化输入，验证参与人/分配/期限；串行化有副作用节点 | AA 收款提案与混合 DAG、并行副作用拒绝、原有完整参与人付款流程 |
| Z06 | 口语多读任务、年度分析、近期月份及条件选择被错误拒绝；整句安全检查与拆句顺序不一致 | 支持通用有限语法、明确月份和 12 个月 READ/JOIN；先检查整句注入/否定再拆句；明确用户选择转换 WAIT_USER | 口语变体、年度深度、跨场景、整句注入、条件不授权 |
| Z07 | 多轮 graph 金额局部修改走 legacy 路径；替换可能保留旧授权；解析失败可能部分建任务 | 先解析全部子句；完整替换取消旧草案并绑定 revision；不完整 graph 修改要求完整重述；legacy 金额编辑保留币种 | 多轮澄清/修正、旧授权不可复用、缺参数不产生部分执行 |
| Z08 | 恶意 Provider message/choice 类型触发 AttributeError；拒绝/格式失败被当作语义失败继续处理 | 固定协议错误，非重试拒绝/工具/超大响应不自动修补；保持 GLM/OpenAI Compatible 可配置接口 | 9 类畸形响应、原 Provider/Gateway/Router/注入测试 |
| Z09 | 银行写入已返回交易，但 verifier 异常或保存回执失败导致错误状态或遗漏已知交易 | 保留已知交易 ID，任务/job 进入 MANUAL_REVIEW，不生成成功回执、不再发送 | Banking 与 legacy 写后 verifier/持久化故障注入 |
| Z10 | 回执写入和 RECEIPT_GENERATED 审计非原子；重复/冲突绑定可能产生多回执 | 单事务提交回执和审计，按 action/transaction 精确幂等，冲突拒绝 | 审计插入失败触发器、重复一致/不一致回执、原证据测试 |
| Z11 | 定时任务恢复未原子检查运行中认领；死进程执行阶段存在重复发送风险；并行 step 清理不完整 | 活认领跳过；死 CHECKING 刷新提案并重新授权；执行/校验不确定进入人工复核；同事务清理 task 和所有运行 step | 真正 multiprocessing.spawn、进程死亡、重启、重复 tick、状态一致性 |
| Z12 | reconciliation 并发及 stale 银行返回可能覆盖已确认 SETTLED/FAILED/REVERSED | reconciliation 维护认领；事务内再次检查银行状态，拒绝倒退并审计；允许真实 SETTLED→REVERSED | PENDING 覆盖 SETTLED 负例、反转可见且不能重新成功、原对账测试 |
| Z13 | socket 超时不限制总读取时间，慢速响应可无限续期；重复长度、截断响应边界不统一 | 有上限的单调时钟总时限读取，长度歧义/超大/截断拒绝；金融 POST 不确定不重发 | slow-drip 读取、写入不确定、原 SDK/RPC/Provider 用例 |
| Z14 | HTTP 每连接无限创建线程、慢请求耗尽、重复关键头和歧义 JSON；异常输出可能暴露信息 | 有界 worker、总输入时限、重复关键头/JSON 拒绝、安全错误计数；仅完成输入后解除输入时限 | 容量拒绝与恢复、slowloris、重复金融 JSON/头、跨用户 API |
| Z15 | checkpoint 后台异常被静默忽略，业务健康检查未体现审计退化 | 后台退化计数和 health 状态可见，固定错误标签，无敏感异常内容，恢复成功解除退化 | 故障注入、退化健康检查与恢复测试 |
| Z16 | Anchor job 多 worker 可互相覆盖；部署和 commit 各自控制 nonce；中断未知广播后重签风险 | job 级认领，钱包级共享认领与 durable nonce reservation/outbox；广播前落盘已知 hash；已知交易只读查询不要求重新获得签名认领 | 双 worker、部署/commit 共享 nonce、未知提交、重复 checkpoint；全部使用临时签名器/fake RPC |
| Z17 | 独立 RPC 可配置为同对象/同主机不同 Key；离线签名计划接受浮点/布尔/额外字段；记录晚于广播 | 同对象/同主机拒绝；精确交易字段及整数检查先于密钥加载；部署文件私有、独占、fsync 后才允许未来广播 | 网络隔离、畸形计划、nonce/认领负例、本地合约执行与独立验证器 |
| Z18 | **本机实际签名根密钥继承 Authenticated Users 修改及 Users 读取权限；DB/WAL 和 token/key 创建不完整受保护** | 原生 Windows 私有 ACL，目录继承保护，拒绝链接/junction；秘密先保护再写入，flush/fsync 后原子发布；已有文件每次加载保护、无内容替换 | 实际 ACL 修复、8 线程首次建根密钥、失败不覆盖、加密签名钥重载、DB/WAL、实际 AppContainer 无法读取宿主 DB/密钥 |
| Z19 | 产品依赖中已存在可修复公开公告风险 | 升级 cryptography；锁定 HTTP 依赖；npm override 修复 ws/underscore，重跑密码学/证明测试 | 产品依赖闭包审计、pip check、真实 Groth16、密码学负例、全量回归 |
| Z20 | 六场景 demo 的合成 TOTP 时钟在证明跨 30 秒窗口后过期；Python 软关键字同一行解析被工具拒绝 | 仅修正 demo 合成计数器时钟与脚本换行，不修改 TOTP、重放/权限规则或业务语义 | 六场景真实本地证明和 HTTP demo、Ruff、全量回归 |

复现前失败日志保留，例如 [活任务恢复](D:/TRUSTA金融科技/data/zero-defects/live-recovery-before.txt)、[写后验证](D:/TRUSTA金融科技/data/zero-defects/verifier-before.txt)、[回执事务](D:/TRUSTA金融科技/data/zero-defects/receipt-before.txt)、[Anchor worker](D:/TRUSTA金融科技/data/zero-defects/anchor-worker-before.txt)。所有轮次原始日志仍在证据目录，不用最终通过覆盖失败原因。中途错误模块名、测试夹具配置和缺失密钥错误映射造成的失败已处理；不计入最终通过数。

## B. 已排查且无需改变的模块与安全边界

| 审查范围 | 本轮核实结论与保留边界 |
|---|---|
| 六 Agent、Supervisor/Planner、Blackboard/Message Bus/Scheduler | 六 Agent 数量和职责保留；修复 Runtime 所有权/恢复，不更换协作架构。跨进程 Finance 真实 AppContainer/IPC 检查通过，不能据此声称六 Agent 均有 OS 隔离。 |
| Policy、Authorization、Execution Contract、Tool Gateway、HardVerifier | 金融执行仍要求资源/币种/金额/授权哈希/时效/强认证匹配；这些核心规则源码未改。风险画像、Memory、DAG 等待信号都不是支付许可。 |
| ECDSA、Trusted Message V3、身份/Capability Registry | 保留原始 envelope、payload hash、nonce、接收端验证、版本/撤销、签名 handoff 和能力绑定；修复密钥文件加载权限，不更换算法或信任根。 |
| DID、Groth16 | did:key 绑定与真实证明执行通过；DID/ZKP 不产生金融权限。现有 Groth16 是开发单宿主 setup，不宣称 production-ready 或第三方审计。 |
| Merkle Audit、checkpoint、可信协作证据 | 保留追加式审计、不可变 checkpoint、独立 record proof 和签名 handoff 验证；修复事务/作业所有权，使业务状态与证据一致。验签通过本身不代表获授权。 |
| AuditAnchor Solidity | 合约源码哈希未改变；本地 EVM 验证 publisher、首提交、重复一致/冲突、事件/read-back。没有把本地 anchor 当外部区块链。 |
| Bank Adapter 与多币种 | 金额以 minor units 精确处理，禁止混币推导；真实 Sandbox 只返回其支持的能力。产品购买/赎回、卡片等未支持的外部能力不能静默切到模拟并宣称银行成功。 |
| API、Memory、SSRF/注入、Provider 路由 | 原有用户隔离、API token/操作员权限、Memory consent/TTL/白名单、配置域名/重定向限制、Provider 数据策略保留；新增输入/响应边界拒绝。 |

初始源码 SHA-256 与最终 SHA-256 可以核对上述未修改模块，不依靠历史“已完成”标签证明。

## 测试、攻击与功能验收

### 实际回归

| 项目 | 本轮实际结果 | 证据 |
|---|---|---|
| 修改前全量基线 | 737/737，50 个模块，0 skip，334.310 秒 | [基线摘要](D:/TRUSTA金融科技/data/zero-baseline-result.json)、[原始日志](D:/TRUSTA金融科技/data/zero-defects/baseline-tests.txt) |
| 最终全量 | **781/781，58 个模块，0 skip，475.022 秒** | [最终摘要](D:/TRUSTA金融科技/data/zero-defects/locked-final-result.json)、[完整日志](D:/TRUSTA金融科技/data/zero-defects/locked-final-tests.txt) |
| 全量后攻击/边界复测 | **44/44，36.867 秒** | [复测日志](D:/TRUSTA金融科技/data/zero-defects/post-regression-attacks.txt) |
| 银行任务 benchmark | 34/34 个预设结果正确；应完成任务 23/23；跨场景 2/2 | [原始 case 与指标](D:/TRUSTA金融科技/data/zero-defects/banking-task-final.json) |
| 银行安全 benchmark | 45/45 攻击检测，未授权执行 0/45 | [攻击逐例结果](D:/TRUSTA金融科技/data/zero-defects/banking-security-final.json) |
| 原有隔离/可信通信攻击与恢复验收 | 31/31，0 skip，含实际 Windows AppContainer | [逐例结果](D:/TRUSTA金融科技/data/zero-defects/agent-communication-security-benchmark.json) |
| 六场景本地证明 demo | 六场景完成；真实 Groth16/DID 绑定、独立 Merkle record 验证通过 | [结果](D:/TRUSTA金融科技/data/zero-defects/six-scenario-final.json) |
| 六场景 HTTP E2E | 6/6；11 个记录证明；定时重启后一次交易、重复 tick 0 写入 | [结果](D:/TRUSTA金融科技/data/zero-defects/six-scenes-http-final.json) |

表内各套件有重叠，不相加制造总测试数。全量测试由两个隔离的 unittest 模块进程执行；这只是测试运行方式，不是所有 Agent 隔离的证明。新增 `test_zero_recovery.py` 另有实际 spawn 多进程并发、死亡与恢复测试。

新增八个测试模块分别为：`test_zero_defects` 10、`test_zero_transport` 6、`test_zero_planning` 9、`test_zero_recovery` 5、`test_zero_anchor` 6、`test_zero_secrets` 6、`test_zero_bank_states` 1、`test_zero_provider` 1，共 44。旧测试源码哈希全部一致。

### 六业务场景与协作验收

1. 智能转账：明确收款人/金额 → 提案与风险判定 → 逐笔确认/必要的 TOTP → Virtual Bank 交易 → HardVerifier/回执。年度累计升级强认证也经验证。
2. 账单分析：获取已结算记录 → 分类、退款/净支出、异常分析 → 月度或 12 个月 READ/JOIN 汇总；只读，不制造交易。
3. 理财：风险/产品查询 → 推荐及比较；本地购买/赎回各自生成新金融 IR 和授权。Virtual Bank 不代表真实市场收益或外部券商执行。
4. 卡片：本地申请/限额/冻结/挂失，经策略和必要强认证，禁止从 LLM 直接调用银行接口。
5. 订阅/代扣：只读识别 → 明确选择 → 新操作提案与授权 → 本地取消/关闭 → 回执和提醒队列。
6. 跨场景：分析→选择→新授权→订阅取消→根据已验证节省重新规划预算；事件只触发提案，不能继承之前的付款许可。

[订阅节省与预算工作流](D:/TRUSTA金融科技/data/zero-defects/subscription-budget-demo.json) 在重启后继续：本地基础预算 10000 minor units，真实本地取消回执确认节省 1999，结果 11999；签名 handoff、checkpoint 与 Merkle receipt 验证通过，外部写入 0。AA collection graph 的 COMPLETED 表示收款请求产物完成，不表示参与人付款；参与人仍分别确认自己的交易。

### 自然语言与真实模型证据边界

[历史自然语言固定案例重放](D:/TRUSTA金融科技/data/zero-defects/nl-historical-locked.json) 为 **21/23** 符合原冻结预设，新增远程调用 0。HN15 口语分析与理财读组合已修复。两项与预设不同但安全正确的案例仍保留在原分母：

- HN11：23 元申购低于产品最低额，系统 BLOCKED；原预设 WAITING_USER 不合理，没有下调最低金额。
- HN14：alice/bob 未在该夹具建立权威参与人资料，BLOCKED；没有捏造账户。新增已建立真实本地参与人资料的 AA/混合 graph 用例通过。

原 HN13 使用固定历史预约日期，不改成新未来日期来提高成绩；本次重放明确排除该时间敏感案例。定时行为另由实际重启 E2E 与原定时测试验证。该集已被使用，不是新盲测或 LLM 准确率。16 条预备 holdout 未做新增付费模型评测。

本轮用当前代码重放 **46 条历史真实模型原始响应**，没有远程推理、重试付费或编造模型结果。如下数值分别是 raw 正确 / Validator 接受 / E2E 预设结果；三者不是同一指标。

| Provider | 意图 B_INTENT（10 条） | 结构 A_GRAPH（7 条） | 自由图 B_GRAPH（6 条） |
|---|---|---|---|
| DeepSeek | 5 / 6 / 6 | 7 / 5 / 7 | 6 / 6 / 6 |
| GLM | 2 / 3 / 6 | 3 / 3 / 4 | 0 / 1 / 0 |

[重放输出](D:/TRUSTA金融科技/data/zero-defects/llm-historical-replay.json) 与 [逐条根因](D:/TRUSTA金融科技/data/zero-defects/llm-failure-analysis.json) 保留 schema echo、槽位/单位跨度、过度澄清、错误操作/顺序、截断/限流历史等。GLM 兼容层协议异常已修复，**不能据此宣称 GLM 当前真实复杂规划质量已经提高**。有限规范化语法能完成有证据输入的支持任务；未定义条件、缺参数和未知资源继续澄清或拒绝。

## 静态检查、依赖与安全复核

| 检查 | 实际结果 | 解读 |
|---|---|---|
| compileall | backend/scripts/tests/benchmarks 成功 | 语法检查，不证明业务正确 |
| Ruff E9/F63/F7/F82 | 0 findings | 语法与关键代码错误集，未宣称全部风格规则通过 |
| mypy 默认模式 | 115 文件通过 | 未注解函数体覆盖有限 |
| mypy 扩展 check-untyped-defs | 59 diagnostics / 22 文件，逐项位置保留 | 严格类型检查未通过；异构字典推断、动态 guard 注入、平台 fcntl stub 与运行时已检查的可选对象等，属类型覆盖债务，不伪报为已修复安全漏洞 |
| Bandit | 129 提示：111 low / 18 medium / 0 high，解析错误 0 | 全部分类复核，未将扫描级别直接当已确认漏洞数；含交付辅助脚本字段名产生的提示 |
| 产品 Python 已安装依赖闭包 | 42 包，已知公告 0；pip check 成功 | 包含实际可选 crypto/EVM 编译验证依赖；不是整个宿主环境审计零风险 |
| npm crypto_trust | 15 low，moderate/high/critical 0 | elliptic 未修补上游公告的传播结果仍保留，未宣称 npm 零公告 |

Bandit 中 7 处 SQL 为内部白名单/固定名称拼接且值参数化；10 处 URL 为固定 loopback demo 或固定 HTTPS ceremony artifact；1 处 exec 为读取本地已固定 workflow 脚本，仅调整输出路径。默认空 proof 不绕过强认证；脚本 demo secret 不用于生产银行或钱包。运行时的 audit worker 静默异常已修复，剩余 B110 位于历史离线评分辅助路径。每项文件/行与理由见 [扫描复核](D:/TRUSTA金融科技/data/zero-defects/scan-review.json)，类型诊断见 [类型复核](D:/TRUSTA金融科技/data/zero-defects/type-review.json)。没有新增忽略规则或通过扩大 Any 覆盖来隐藏错误。

产品依赖更新为 cryptography 50.0.0、requests 2.33.0、urllib3 2.8.0、idna 3.15，npm ws 8.21.0/underscore 1.13.8。当前 proof runner 调用 Poseidon/Groth16，不调用 elliptic signing；其上游公告仍是供应链限制，未来新增 ethers 签名调用需重新审计。没有采用 npm force 建议的 circomlibjs 主版本降级来破坏电路/证明兼容。参考 [cryptography 官方安全公告](https://github.com/pyca/cryptography/security/advisories)、[elliptic 公告](https://github.com/advisories/GHSA-848j-6mx2-7j84)、[ws 公告](https://github.com/advisories/GHSA-96hv-2xvq-fx4p)、[underscore 公告](https://github.com/advisories/GHSA-qpx9-hpmf-5gmw)。

宿主全局 Python 环境还包含 TRUSTA 不使用的其他应用依赖及公告；没有擅自升级用户其他应用。产品结论只针对 [真实安装依赖闭包](D:/TRUSTA金融科技/data/zero-defects/product-dependency-closure-final.json) 与 [闭包审计](D:/TRUSTA金融科技/data/zero-defects/product-pip-audit-final.json)。部署应使用按产品依赖创建的专用环境，而不是据此声明全局环境安全。

实际文件保护限制为当前用户/SYSTEM/Administrators；可以防范普通其他用户及无权限 AppContainer，不防范已控制同一操作员用户、管理员或可信 broker 的攻击者，不能等同 HSM。单机 SQLite 的本地可信管理员仍可绕过应用修改存储；签名和 Merkle 用于发现不一致，不承诺消除宿主控制风险。

## 性能与可靠性对照

相同 benchmark 脚本，真实 Windows 本机、Virtual Bank、冷启动包含在内；当前测量在全量回归结束后进行。历史数据为上一交付保留值，未取得本轮修改前的同负载性能快照，不能把差异精确归因为代码，也不隐藏变慢。

| 指标 | 历史参考 | 当前实测 |
|---|---:|---:|
| Finance 进程图，8 样本 p50 / p95 | 2290.628 / 2321.557 ms | 2527.051 / 2592.449 ms |
| 8 并发、80 HTTP 请求 p50 / p95 | 44.389 / 185.882 ms | 91.938 / 552.380 ms |
| HTTP 最大耗时 / 正确结果 | 205.417 ms / 80 | 600.337 ms / 80 |
| 1 秒 SQLite 写锁竞争等待 | 1198.571 ms | 1058.121 ms |
| 1000 审计事件 + checkpoint | 6522.633 ms | 8236.063 ms |
| 20 个 receipt proof 查询验证 p50 / p95 | 23.611 / 26.488 ms | 61.960 / 70.022 ms |

当前 HTTP 含 20 条跨用户请求，全部正确 403；其余 60 条正确响应。Finance 冷启动最大 2711.575 ms，HTTP 最大 600.337 ms，在现有 20 秒配置预算内无失败。小样本 p95 使用原脚本排序位置法，不能解释为生产 SLA 或 p99。当前读取/证据延迟高于历史参考，V1 本地功能和超时预算内接受；没有通过关闭权限、证明或审计降低耗时。未来生产容量需要独立并发负载与长期数据，当前不作生产吞吐承诺。

[当前完整样本](D:/TRUSTA金融科技/data/zero-defects/backend-reliability-benchmark.json)、[历史样本](D:/TRUSTA金融科技/docs/backend-final-hardening/backend-reliability-benchmark.json)。重启定时、死进程恢复、SQLite 竞争、慢网络输入、worker 容量恢复、审计退化可见和不确定支付禁止重发均有本轮日志与测试证据。

## 外部集成与 Sepolia 部署准备

### 官方银行只读验收

[Revolut 官方 Sandbox 本轮读取](D:/TRUSTA金融科技/data/zero-defects/revolut-readonly-final.json) 时间为 **2026-10-10T11:17:28.781612Z**：5 个账户，余额读取、10 月交易摘要与 Finance/TaskGraph 结果一致，6 条可信消息接收、11 条 SDK 读取审计、1 个 checkpoint 验证通过。请求计数为 accounts GET 12、transactions GET 4，refresh 0、业务写入 0、LLM 调用 0；金融状态表前后均为 0，证明该独立只读验收没有伪造本地银行交易。

生产企业账户/KYC 不在本轮完成范围。当前是官方 Sandbox 集成，不能称真实资金生产闭环。本轮不重做旧的 0.01 EUR 支付或模拟结算；旧交易人工复核与补充验证语义保留。外部服务不支持的理财/卡片等场景在 Virtual Bank 演示，明确标注，不伪报为官方银行成功。

### Sepolia 只读快照与费用

[部署准备证据](D:/TRUSTA金融科技/data/zero-defects/sepolia-readiness-final.json) 时间为 **2026-10-10T11:17:10.100560Z**。目标 Chain ID=11155111；钱包公开地址 `0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE`，余额 **0.0103 SepoliaETH**，部署计划 nonce=0。以下是 estimateGas 与受保护 unsigned plan，不是实际交易 gasUsed。

| 项目 | RPC 估算 | 20% 余量后的交易 gas |
|---|---:|---:|
| 部署 | 3,802,284 | 4,562,741 |
| 首次 Merkle Root commit | 356,357 | 427,629 |

commit 在同一 Sepolia RPC 使用 fresh-contract state override 估算；合约尚未部署，不冒充实际合约执行。单独本地 PragueVM 实际消耗为部署 550,499、commit 94,333，网络规则/状态不同，不用它替代 Sepolia 预算。

unsigned deployment plan 报价：base fee 15 wei、priority fee 1,000,000 wei、maxFeePerGas 1,000,030 wei；deploy 最大报价支出 **0.000004562877882230 ETH**，commit **0.000000427641828870 ETH**，合计 **0.000004990519711100 ETH**，剩余 **0.010295009480288900 ETH**。只读估算器另提供保守历史 reward 分位 tip 策略（1,200,003 wei），20% reserve 合计 **0.000005988608682210 ETH**；两个价格策略分别注明，不混算。

按相同网络估算 gas、20% gas 余量的价格上涨情景：1 Gwei 约 **0.00499037 ETH**；2 Gwei 约 **0.00998074 ETH**；5 Gwei 约 **0.02495185 ETH**。价格上升不能无限追价。

实际执行保护仍是：deploy gas≤5,000,000、commit gas≤600,000、tip≤0.1 Gwei、maxFee≤2 Gwei、deploy 费用≤0.0085 ETH、commit≤0.0015 ETH、保留余额≥0.0002 ETH、报价 TTL=120 秒。**两笔政策总硬上限 0.0100 ETH；现余额覆盖硬上限和保留余额，最坏剩余 0.0003 ETH。** 这些为拒绝阈值，不是会花掉的预计金额。若 2 Gwei 下单笔部署超出 0.0085 ETH 则拒绝，即使总余额足够也不能绕过。0.0005/0.001/0.005 ETH 在本快照报价加余额保留下均够；不保证它们覆盖 1/2/5 Gwei 上涨或政策总硬上限。

publisher 构造/补丁 runtime 为上述钱包地址；预测地址 `0x0A97FE534171aB6f1080Ba2F459f2DC54477322d` 只在 sender/nonce 未变时成立。合约源码 SHA-256：`c18ab76d2f695f6c119aa6ad78ee574067e4524baef8cf2c166b18e3f0b580dc`；creation artifact SHA-256：`c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`；预期 publisher 补丁 runtime Keccak：`0x8e36d0fcd7489c6f9a9352cbfbd01109f2d1f7d2bb8b8214cb5ebb02bc63efe9`。

现有 Infura/Pocket 两个 RPC 均已只读确认 Sepolia，共同区块、样本交易/receipt/events/code 读取一致。[双 RPC 证据](D:/TRUSTA金融科技/data/zero-defects/dual-rpc-readonly.json) 表明两个供应商协议访问可用，不证明底层基础设施完全独立，更不是本项目 AuditAnchor 链上验收。

签名入口支持受控本地加密 keystore、保护后的文件权限和钱包地址匹配；当前没有配置真实签名器、没有证明真实钱包控制权，签名/广播均关闭。严禁在聊天中导出 MetaMask 私钥。旧 120 秒计划已过期，下一次执行必须重新估算并对确切计划审批，不自动沿用旧报价或静默换价。

## C. 无法自行完成的外部依赖：一次操作员清单

以下不作为程序缺陷，也没有因这些阻塞停止离线修复：

1. **钱包控制与部署批准**：操作员在本机配置现有钱包的受控签名器/加密 keystore，独立确认 sender/publisher；不发送私钥到聊天。重新生成当前 Chain ID/nonce/Gas/费用/余额/代码哈希绑定的 unsigned deployment plan，再明确批准该具体交易。
2. **部署后外部验收**：批准后才能签名广播部署；使用第二 RPC 检查 canonical receipt、code hash、publisher、确认数。当前没有部署，所以这些实合约验收不能预先宣称通过。
3. **Root 具体提交批准**：部署验收后生成当时 checkpoint/root/nonce/费用绑定的新 plan，再明确批准 commit。提交后双 RPC 验证事件/read-back、receipt 与 canonical block，继续做 reorg/finality 检查；独立只读验收不需额外链上验收交易。
4. **新付费模型评测**：锁定新的未评估集和请求/token/费用上限，取得新增预算后才运行 DeepSeek/GLM 真实评测。本轮不能以历史 replay 替代 fresh blind benchmark。
5. **生产资金集成（可选产品方向）**：需要真实银行账户资格、银行准入/权限与逐笔批准；本轮不扩权、不支付。比赛后端演示使用官方 Sandbox 与明确标注的 Virtual Bank；是否生产开户由产品阶段决定。

上游 elliptic 暂无补丁的公告、开发 Groth16 setup、全 Agent/HSM 级隔离和生产负载认证另属明确的信任/供应链/产品范围限制，不标为本轮已实现。

## D. 尚未解决的内部缺陷与冻结范围

在本轮源码审查、可复现攻击与实际权限内，**没有仍待修复的已确认功能或可利用 P0/P1 内部缺陷**。不据此推断未知缺陷为零。

严格类型覆盖仍未达到 check-untyped-defs 全绿：59 条已定位诊断保留为可维护性债务，未发现其对应的未保护金融路径或可复现运行故障；默认检查通过不能覆盖它们。本轮没有为消除提示无必要重构既有动态注册/注入模块。npm 15 条上游低风险公告也保留，不包装成完全无风险。

冻结的是本报告已验证的 V1 本地执行协议、金融授权与验证边界、六场景 Virtual Bank、V3/DID/Groth16 开发证明、跨进程 Finance 和外部只读适配。**不冻结为已完成**：真实 GLM 质量新盲测、AuditAnchor Sepolia 部署/commit 独立链上验收、生产资金、六 Agent 全隔离、生产 ZKP ceremony 和生产性能认证。明确的外部权限与产品范围不能混同成已关闭代码缺陷。

## 实际源码交付清单

原 Python/Solidity 基线 234 文件。本轮修改 37 个既有源码文件、新增 16 个源码/测试/支持文件，删除 0；另更新 4 个依赖声明/锁文件。原测试全部保持哈希一致。

| 分类 | 修改文件 |
|---|---|
| Runtime/服务 | backend/runtime/{graph_claims,graph_runtime,taskgraph,banking,event_plans}.py；backend/{service,server,persistence,receipts,config}.py |
| 银行业务/Adapter | backend/banking/{intent,split}.py；backend/bank_sdk/{auth,reconciliation,revolut_auth,revolut_transfer,transport}.py |
| 对话/LLM | backend/conversation/service.py；backend/llm/{graph_proposal,grounded_request,proposal_dialogue}.py；backend/llm/providers/{http_adapter,openai_compatible}.py |
| 信任/Anchor | backend/security/crypto.py；backend/audit_trust/{anchor,external_anchor,independent_anchor,transaction_safety}.py |
| 操作脚本/验收 | scripts/{anchor_operator,backend_complete_demo,deploy_audit_anchor,estimate_sepolia_costs,phase6_reports,revolut_readonly_demo,revolut_sandbox_connect,revolut_server,sepolia_deployment_readiness}.py |
| 新公共安全工具 | backend/network.py；backend/security/local_files.py |
| 新测试 | tests/test_zero_{anchor,bank_states,defects,planning,provider,recovery,secrets,transport}.py |
| 新支持脚本 | scripts/zero_defects_{delivery_evidence,demo_trace,dependency_closure,external_reads,local_acceptance,regression}.py |
| 依赖 | requirements.txt；requirements-phase6.txt；crypto_trust/package.json；crypto_trust/package-lock.json |

本机 .env、签名根密钥和实际使用的秘密存储仅修复保护权限；不将秘密提交到文档。[交付凭据扫描](D:/TRUSTA金融科技/data/zero-defects/secret-scan.json) 比对配置凭据字符串，仅输出命中类别/文件，0 命中；不读取钱包私钥，不输出 Key/Token/RPC URL。扫描只证明所比较字符串未出现在本轮文本证据，不能证明所有形式的数据泄漏不存在。

所有最终摘要、原始日志、逐例结果、前后源码哈希和支持证据由 [SHA-256 证据清单](D:/TRUSTA金融科技/data/zero-defects/evidence-manifest.json) 索引。哈希保证本地材料可核对，不等同外部独立认证。后续任何源码或配置变化须重新验证相关边界；本次交付没有未授权银行或区块链资金操作。
