# TRUSTA 第二阶段持续运行后端交付

日期：2026-10-12。代码、专项测试、全量回归通过。新增后台监督层复用第一阶段已通过的真实 Sepolia 交易安全机制，不增加签名或广播通道。

## 实现与安全边界

Transaction State Machine 持久化 CREATED、PLAN_APPROVED、SIGNED、CAPTURED、BROADCASTED、PENDING、CONFIRMED、ANCHORED、FINALIZED，以及 FAILED、REORGED、EXPIRED、REPLACED。状态迁移使用 SQLite BEGIN IMMEDIATE、版本比较和持久化迁移记录；非法跳转、错误版本、hash重新绑定拒绝。成功状态需要对应验收证据。监控运行错误另报 MONITOR_ERROR，不伪装成链上 FAILED。

已有交易通过受限数据库中的原始签名 capture、准确计划和 nonce reservation 恢复监督状态；这些是从可信持久化记录重建的监督历史，不把恢复时间冒充原钱包点击时间。新模块没有金融执行授权能力，外部 API 不能创建或更改这些状态。

Nonce Manager 每次读取 Sepolia pending nonce，在持久化表中原子预留，拒绝重复占用、陈旧准确 nonce、类型错误，重启保留绑定；同时检查原 external_anchor_nonces。新表用于规划阶段的本地协调，不改写原冻结 ingress 的预留表。真正签名 capture 的互斥、网络 nonce检查和最终约束继续由原机制执行；被外部交易抢占的旧计划必须重新生成并批准，不自动换 nonce。

Receipt Watcher 核验实际交易签名与准确字段、Receipt、区块绑定、canonical block、稳定回执和至少3个确认；发现回执消失或区块改变转为 REORGED。确认、锚定、最终性分开表示。FINALIZED 要求已通过原 Anchor 验收并观察到节点 finalized 区块覆盖提交区块；历史 FINALIZED 仍继续检查重组，不是永久免检。

新 Anchor Worker 复用原 Checkpoint/Merkle 算法及原 AnchorWorker：Audit Event → Checkpoint/Merkle Root → 待批准 Commit → 原 ingress 捕获及发送 → 真实回执读回 → 原 Anchor job 确认 → 持久化监督状态。新事件 Checkpoint 生成只创建 PENDING job；无法自动代替 MetaMask 或 Exact Plan 审批。已经捕获的 Commit 必须匹配既有 submission hash，监督器才调用原 Worker 的读回接口。持续分页避免只处理首批记录；单条 RPC/证据故障记录降级并继续其他交易。

Recovery Manager 自动追踪广播响应丢失、超时和重启后的交易，保留原 nonce与原 hash。未签署报价过期进入 EXPIRED；已捕获或已广播的超时进入操作员检查提示，不能仅因没查到回执就宣布失败。REPLACED 需要实际观察另一笔同 sender/nonce 的独立签名交易、canonical receipt及确认深度，不把替代交易当作原操作成功。不会自动重发、加价、签名或释放不确定 nonce。

Sepolia ingress、MetaMask signing flow、Exact Plan validation、ECDSA verification 和 Anchor Solidity 合约本轮均未修改。原 Agent、银行业务链、DID/ZKP/Merkle 算法均未修改。没有新增依赖。

## 文件清单

新增：

```text
backend/tx/__init__.py
backend/tx/state_machine.py
backend/tx/nonce_manager.py
backend/tx/recovery_manager.py
backend/tx/api.py
backend/monitor/__init__.py
backend/monitor/receipt_watcher.py
backend/anchor/__init__.py
backend/anchor/worker.py
scripts/transaction_supervisor.py
tests/test_transaction_supervision.py
docs/Production_Grade_Backend_Phase2.md
```

修改：`backend/server.py`，仅增加两个受原 Bearer身份、Host、Origin保护的查询入口及访问拒绝状态码：

- `GET /api/v1/tx/{0x交易hash}`
- `GET /api/v1/anchor/{root}`，Root 支持带或不带 `0x`。

结果按服务器配置的身份过滤；不同用户与未认证访问拒绝，不返回原始交易签名、RPC URL、密钥或完整计划。查询的是最新持久化观察，不会在 HTTP GET 中签名、广播或无限等待网络。

## 运行方式

复用已有 `.env` 中的 Sepolia RPC。不新增钱包，也不导出私钥。应用 API 与监督器必须使用同一 DB，`--owner` 必须对应服务器的可信身份映射，由本机操作员配置，不能由 HTTP 请求指定。

先只读运行一轮：

```powershell
python -m scripts.transaction_supervisor --db data/sepolia-public-acceptance/business/business.sqlite3 --contract 0x0A97FE534171aB6f1080Ba2F459f2DC54477322d --public-key data/sepolia-public-acceptance/business/audit-public-key.pem --owner demo --once --output data/sepolia-public-acceptance/continuous-sepolia-readback.json
```

持续运行时去掉 `--once`，默认每15秒一轮，`--interval` 可设1–300秒。Ctrl+C安全退出。轮询有批量上限，所有网络方法经过只读 allowlist；签名和发送方法立即拒绝。进程级互斥复用原 GraphClaims 的 `anchor-job:` 元数据死进程恢复，不靠金融任务超时接管。

如需监督进程同时生成新增审计事件的 Checkpoint，增加 `--checkpoint-dir data/sepolia-public-acceptance/business`。此目录必须包含原本机 Core key/agent-keys，DB路径必须匹配；不会打印这些密钥。未启用此选项时，Checkpoint 由原应用生产，监督器只负责已有捕获的交易。新 Root 仍等待独立准确交易审批与本人签名。

生产进程管理器可托管这一 CLI；本轮不安装常驻系统服务、不修改开机配置。部署时需运维监控输出中的 degraded、MONITOR_ERROR、OPERATOR_INSPECTION_REQUIRED，并制定持久化日志容量、备份和保留策略。

## 测试结果

全量回归：**851项，63模块，0跳过，全部通过，415.736秒**。日志与结果：

- `data/zero-defects/continuous-backend-final-result.json`
- `data/zero-defects/continuous-backend-final-tests.txt`
- `data/zero-defects/continuous-backend-final-modules/`

既有测试保留，原测试文件哈希检查通过。数量比此前829项增加包含第一阶段最后的兼容性测试和本阶段14项新增测试，不把重复专项运行累计为独立测试数量。

本阶段专项：**14项全部通过，28.657秒**，日志 `data/sepolia-public-acceptance/continuous-backend-verified-final.txt`。覆盖：nonce冲突/并发/重启、冻结 ingress 预留兼容、真实部署与Root Commit状态链、真实 EVM revert、真实 snapshot回滚重组、同nonce替换、报价过期/卡住、跨进程 CAS竞争、原 Merkle生产器生成新Checkpoint、单条观察失败隔离、真实已执行交易广播响应丢失后的只读恢复、两个HTTP API身份隔离、签名及广播方法拒绝。正向交易在隔离钱包和真实 PyEVM 中执行，没有用 mock 的成功回执代替业务执行；故障注入仅用于异常路径。

新模块 compileall 通过。测试发现并修复了 typed-transaction accessList 元组/列表兼容、已确认状态重复轮询错误迁移、过早把不确定发送标成BROADCASTED、后台死进程claim无法恢复、单条失败饿死后续记录等问题。测试未弱化原签名、合约代码、Gas或审批阈值。

## 真实网络联调与限制

复用第一阶段两笔真实公共 Sepolia 交易的只读监督结果：部署 CONFIRMED，业务 Root Commit FINALIZED，新增广播0。证据：`data/sepolia-public-acceptance/continuous-sepolia-readback.json` 与最终代码复核 `continuous-sepolia-final-readback.json`。此前真实公共链证据与历史 LOCAL_PYEVM_ONLY证据不覆盖。

持续监督层默认读取主 RPC，不能把每轮状态观察解释为每轮重新完成双独立RPC证明；第三方 ExternalAnchorProof验收仍使用原双RPC独立验证器。新交易、新 Root、费用替换仍需审批与 MetaMask控制权。测试证明覆盖范围内的实现正确性，不证明不存在未知漏洞或所有远程节点始终诚实。

本轮没有新增外部银行付款、真实签名或真实交易广播，也未消耗测试ETH。新增后台能力是持续发现、核验、持久化与安全恢复，不是无人审批的钱包执行系统。
