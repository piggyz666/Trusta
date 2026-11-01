# TRUSTA Sepolia 只读费用验收（2026-10-10）

结论：当前公开部署钱包余额 0.0103 SepoliaETH 足以覆盖一次部署和首次 commit 的预算。但现有脚本硬编码的部署/commit Gas 上限都低于当前 Sepolia 的执行需求，不能直接执行。此轮未修改这些后端模块，未签名、部署、广播或消耗测试币。

## 网络与证据

本地填写的是 Infura 主网 URL，直接只读检查返回 Chain ID=1。此轮临时将同一 Key 的主机名选为 `sepolia.infura.io`，原 `.env` 和 Key 未修改或输出。估算工具使用系统代理；原有银行/Anchor 传输代码未改动。

- Sepolia Chain ID：11155111，已通过 RPC 验证。
- 区块：11879064。
- 区块 hash：`0x8fce2fe09607022629361df6ca0e06c8cb3668c1b0289c3116271dfa2098a533`，已重新读回确认相同。
- 区块时间：北京时间 2026-10-10 02:12:12。
- 余额：0.0103 SepoliaETH，查询自上述区块。
- 独立 RPC 尚未配置；本次是单一 Infura 的只读预算评估，不是双来源独立上链验收。
- 原编译产物：solc 0.8.24 创建 bytecode，SHA-256 `c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`。
- 已交付后端文件哈希变化：0。

未来运行前请将本地 URL 的主机名从 `mainnet.infura.io` 改为 `sepolia.infura.io`，保持自己的 Key 路径；不要把 URL 发到聊天。此轮未代改配置，避免混淆用户已保存的端点。

## RPC Gas 估算与交叉验证

| 操作 | 当前 Sepolia RPC estimateGas | 此前本地 PragueVM gasUsed（仅供比较） |
|---|---:|---:|
| 合约部署 | 3,802,284 | 550,499 |
| 首次非零 root commit | 356,357 | 94,333 |
| 合计 | 4,158,641 | 644,832 |

部署使用原创建产物、真实钱包地址和余额所在的实际链上状态。commit 使用 RPC state override 在空模拟地址装入相同编译产物的 runtime，publisher immutable 绑定真实钱包公开地址，映射初始为空，三个 bytes32 为合成摘要，sequence=1、version=1。先 eth_call 检查 publisher，随后 estimateGas。状态覆盖不会写入区块链。

部署 eth_call 在 1,000,000 Gas 时报告 out of gas，在 5,000,000 Gas 时成功并返回 2305 字节 runtime，逐字节匹配同 publisher 的编译 runtime。commit eth_call 在 200,000 Gas 时 out of gas，在 500,000 Gas 时成功。一项重复 commit estimateGas 上限交叉检查发生 HTTPError，已记录；其余成功估算及两种 call 验证保留。

这些是当前 RPC 执行规则下的估算，不是已广播交易的 receipt.gasUsed。未部署，没有真实部署/commit 收据，不能称为已完成链上锚定。合成摘要中零字节数、sequence 等值可使实际 calldata Gas 略有差异。

Sepolia 的 [Glamsterdam 官方升级公告](https://blog.ethereum.org/en/2026/09/17/glamsterdam-testnet-announcement) 给出 2026-10-06 的激活安排，并说明状态创建与访问的 Gas 重定价。当前部署/存储估算增长与该升级相符；本地旧 PragueVM 结果不适合作为当前预算。[EIP-8037](https://eips.ethereum.org/EIPS/eip-8037) 明确提高代码部署和新存储槽的成本。

## 当前费用：区分低价节点建议与保守中位数

| 项目 | RPC 值 |
|---|---:|
| baseFeePerGas | 13 wei = 0.000000013 gwei |
| eth_maxPriorityFeePerGas | 1,000,000 wei = 0.001 gwei |
| eth_gasPrice | 1,000,013 wei = 0.001000013 gwei |
| 最近五区块交易小费 P50 的中位数 | 999,999,987 wei = 0.999999987 gwei |

GasUsed/estimateGas 与 Gas 单价是不同量。type-2 有效单价=min(maxFeePerGas, baseFee+priorityFee)，实际费用由打包区块决定。现有脚本是传统 gasPrice 交易，并未升级为 EIP-1559 交易；本次仅分析两种报价方案。

节点建议低价时，预计部署费用 0.000003802333429692、commit 费用 0.000000356361632641，合计 **0.000004158695062333 SepoliaETH**。此价格不保证立即打包。

单按当前 base fee 的协议费用下界为 0.000000000054062333 SepoliaETH；零小费不保证被纳入区块，因此不能把这个下界作为实用资金建议。

## 预留与涨价情景

设 D=3,802,284，C=356,357，B=13 wei。Gas 余量分别取 ceil(1.2D)=4,562,741 和 ceil(1.2C)=427,629，共 4,990,370。这是建议未来 Gas 预算，不是实际消耗。

低价方案预留=4,990,370×(2B+1,000,000)/10^18 = **0.00000499049974962 SepoliaETH**。

保守方案采用五区块小费中位数 T=999,999,987 wei：当前 B+T=1 gwei；预留 4,990,370×(2B+T)/10^18 = **0.00499037006487481 SepoliaETH**。因基础费极低，基础费仅上涨 2 或 5 倍时总费用几乎不变；实际压力应另测有效单价整体上涨。

| 有效单价（假设压力情景） | D+C 预计费用 | 增加 20% Gas 余量 |
|---|---:|---:|
| 1 gwei | 0.004158641 | 0.004990370 |
| 2 gwei | 0.008317282 | 0.009980740 |
| 5 gwei | 0.020793205 | 0.024951850 |
| 10 gwei | 0.041586410 | 0.049903700 |

## 三种余额是否足够

下表仅讨论调整未来执行 Gas 预算后的资金能力；所有余额均不能使当前过低的脚本 Gas 上限自动变得可用。

| 起始余额 | 当前节点低价 + 20% Gas 余量 | 保守约 1 gwei + 20% Gas 余量 | 2 gwei + 20% Gas 余量 |
|---|---|---|---|
| 0.0005 | 足够 | 不足 | 不足 |
| 0.001 | 足够 | 不足 | 不足 |
| 0.005 | 足够 | 刚够，仅余约 0.00000963 | 不足 |

用户钱包实际余额 0.0103，在 2 gwei 加 20% Gas 余量情景下尚余 **0.00031926**。因此可以把 **0.006** 看作本次保守 1 gwei 情景的实用预留，**0.01** 看作覆盖约 2 gwei 情景的预留；不是对未来价格的保证。若有效价格到 5 gwei，需要约 0.025；10 gwei 时约 0.05。

独立链上验收是交易/收据/事件、代码、publisher、readback、确认深度、规范区块与 checkpoint 签名的 RPC 读取，不额外发送验证交易，因此额外链上 Gas=0。RPC 服务商自身限额/费用是另一项外部条件。

## 当前执行限制与后续边界

1. `scripts/deploy_audit_anchor.py` 仍硬编码 gas=1,000,000，当前 RPC 已验证不够。
2. `backend/audit_trust/external_anchor.py` 仍硬编码 gas=200,000，当前 RPC 已验证不够。
3. 节点对 Gas 上限×单价的余额预检查与最后实际扣费不同；前一个报告中的低 Gas 本地推算已被本次 live RPC 数据取代。
4. 现有 EthereumRPC 传输显式绕过系统代理，此机器直连失败。只读工具临时复用系统代理，未改核心适配器。
5. 暂未配置第二个独立运营 RPC，无私钥读取、无签名、无部署、无广播，外部锚定完成标志仍为 false。

在未来获准部署之前，需要单独调整部署和发布的 Gas 预算策略及网络配置，并重新只读估算；此轮依照用户要求未进行这些模块修改。

完整公开证据：[sepolia-cost-estimation-20261010.json](./sepolia-cost-estimation-20261010.json)。不含 RPC URL 或 Key。
