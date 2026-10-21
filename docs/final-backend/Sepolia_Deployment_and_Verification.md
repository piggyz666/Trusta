# Sepolia 部署准备与外部验证
状态：主 RPC LIVE_TESTED；真实部署、真实 commit、独立链验收 BLOCKED_EXTERNAL。external_blockchain_verified=false。没有 External_Anchor_Proof.json，避免把本地证明冒充链验收。

RPC 观测：2026-10-09T19:28:34.441109+00:00，Ethereum Sepolia 11155111。公开部署钱包 / constructor Publisher：`0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE`；余额 0.0103 SepoliaETH，pending nonce=0。该地址只是本轮报价绑定，不代表持有已获准真实签名器。

创建字节码 SHA-256：`c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`；Solidity source SHA-256：`c18ab76d2f695f6c119aa6ad78ee574067e4524baef8cf2c166b18e3f0b580dc`；钱包 immutable Publisher 替换后预期 runtime Keccak：`0x8e36d0fcd7489c6f9a9352cbfbd01109f2d1f7d2bb8b8214cb5ebb02bc63efe9`。尚未部署，链上代码/publisher 未验证。合约与冻结保护未改。

实测 RPC estimateGas：部署 3,802,284，首次 commit 356,357；20% 余量 gasLimit 4,562,741 / 427,629。commit 用 Sepolia fresh-contract state override，只读估算，非已经部署的合约执行消耗；不同实际 root 字节分布及未来链状态仍须重新 estimate。未拿 5,000,000 / 600,000 上限冒充实际消耗。

执行保护报价：priority=0.001000000 Gwei，maxFee=0.001000028 Gwei；结合当前 baseFee、RPC tip 及原安全策略，不追逐 feeHistory 高报价。read_only_estimation 的信息性建议价不是获准签名策略，真实受控报价以 unsigned_deployment_plan 为准。当前无余量估计最低总费约 0.000004158699 ETH；有余量的本次报价最坏上界 0.000004990510 ETH；余额预计至少 0.010295009490 ETH。

| 钱包余额 ETH | 当前报价含 0.0002 余额底线 | 硬保护有效最高总费用含余额底线 |
|---:|---|---|
| 0.0005 | 足够 | 不足 |
| 0.001 | 足够 | 不足 |
| 0.005 | 足够 | 不足 |
| 0.0103 | 足够 | 足够 |

保护：deploy ≤5m gas、≤.0085 ETH；commit ≤600k gas、≤.0015 ETH；priority ≤.1 Gwei、maxFee ≤2 Gwei、最低余量 .0002。独立 per-transaction cost 名义和 0.0100 ETH；结合 commit gas×maxFee 的有效总费用上限 0.0097 ETH，加保留余额需 .0099 ETH。当前 .0103 足够覆盖该有效最坏预算，余量 .0004；即便按更保守名义 .0100+保留 .0002，也还多 .0001。

相同余量 gas 在有效价格 .1 Gwei 时总费约 0.000499037 ETH；1 Gwei 约 0.004990370 ETH。2 Gwei 会令当前部署超过 .0085 上限，必须拒绝而非提高预算。小余额“当前够”并非涨价情况下也够。

签名：现有受控加密 keystore 机制；新增本地隐藏输入登记工具经过临时测试钥验证，真实钱包尚未登记。没有真实签名、广播、部署或 ETH 消耗。报价 TTL 120 秒，本报告 plan 已过期不能执行；未来重新报价、逐笔 exact hash 批准，禁止自动换 nonce 或调高 fee。

第二运营商：PublicNode(Allnodes)、dRPC、BlockPI、1RPC 等只读探测未成功，保留 diagnostic 与 runner 错误及更正记录。错误 URL/403/不可用不能算独立验证；本轮没有改写 ANCHOR_VERIFY_RPC_URL 成未经核实的服务。主 Infura 正常。操作员可使用已有第二运营商的安全本机 endpoint，不用重新创建钱包或重复注册 Infura。

后续验收：第二运营商 chainId + 同高度 canonical hash 匹配；精确批准部署并核验 receipt/code/Publisher；从真实 TRUSTA Audit Log 生成受签名 checkpoint，经原 outbox/worker 准备、单独批准 commit；≥3 确认、两运营商 receipt/event/read-back/root/current状态、canonical/reorg 检查全部通过才生成外部证明。当前这些链上字段均为空，不生成替代数据。
