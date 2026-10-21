# Sepolia 只读费用估算准备（2026-10-10）

目前状态：等待用户在本机填写 RPC 和部署钱包公开地址。当前没有 Sepolia RPC 结果、当前链上余额或当前 EIP-1559 费用，不能把下列本地模拟称为链上实测。

## 本地配置

复用现有 `backend/config.py` 的项目根目录 `.env` 格式和环境变量优先规则。`.gitignore` 已忽略 `.env`，无须改动。估算工具仅解析环境配置，不调用会初始化签名密钥的 `Config.local()`，不启动 Agent、数据库、银行或 Anchor Worker。

在 `D:/TRUSTA金融科技/.env` 中填写以下两项（不加引号）：

```dotenv
ANCHOR_RPC_URL=完整的 Infura Sepolia HTTPS URL
ANCHOR_WALLET_ADDRESS=计划部署并发布的公开钱包地址
```

不要填写私钥。若需以后独立链上验收，另配 `ANCHOR_VERIFY_RPC_URL`，应属于独立运营的 RPC 服务。费用估算本身不需要第二个 RPC。公开模板：`.env.sepolia.example`。不要提交本地 `.env` 或向聊天发送其中的 RPC URL。

配置完成后可执行：

```powershell
python scripts/estimate_sepolia_costs.py --output docs/sepolia-cost-estimation-20261010.json
```

该命令只允许 Chain ID、余额、区块、code、call、estimateGas、gasPrice、maxPriorityFeePerGas、feeHistory。所有广播和签名方法均被禁止；不会调用部署脚本。报告不包含 RPC URL、Key、私钥或原始异常文本。报告中的钱包地址为公开信息。

## 已完成的工程检查

- solc 0.8.24 默认编译产物与现有 JSON 创建字节码逐字节一致，2404 字节。
- 当前合约创建字节码 SHA-256：`c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`。
- 部署脚本和 commit 适配器均使用传统 `gasPrice`，不是 type-2 交易。
- 部署上限 1,000,000 Gas；commit 上限 200,000 Gas；gasPrice 大于 100 gwei 时原脚本拒绝执行。这些是上限，不能作为实际消耗。
- 已交付后端文件 SHA-256 均未改变；只新增本地配置、模板、估算脚本和本次说明/报告。

## 本地内存 EVM 结果

PragueVM，原编译产物，首次写入新的 checkpoint，sequence=1、version=1，三个非零 bytes32 合成摘要，无真实审计或客户数据。

| 操作 | 本地 estimateGas | 本地模拟实际 gasUsed |
|---|---:|---:|
| 部署 | 556,147 | 550,499 |
| 首次 Merkle root commit | 96,055 | 94,333 |
| 合计 | 652,202 | 644,832 |

本地模拟仅存在于内存，无真实钱包、RPC 广播或测试币消耗。相同 root 的幂等重复调用不能代替首次写入估算。真实不同 calldata、链上执行环境和节点估算策略会导致细微差异。

独立验收需要检查签名 checkpoint、publisher、代码、交易/收据/事件、readback、确认深度与规范区块；这些为 RPC 读操作，不额外消耗链上 Gas。此结论不是已完成真实验收。

## 没有当前 RPC 时的费用敏感性

以下 gwei 为假设的有效 Gas 单价，不代表当前 Sepolia 价格。单位均为 SepoliaETH。

| 假设单价 | 本地模拟消耗对应费用 | 本地 estimateGas 对应费用 | estimateGas 加 20% Gas 余量 | 原脚本部署前余额要求 |
|---|---:|---:|---:|---:|
| 1 gwei | 0.000644832 | 0.000652202 | 0.000782643 | 0.001 |
| 5 gwei | 0.003224160 | 0.003261010 | 0.003913215 | 0.005 |
| 10 gwei | 0.006448320 | 0.006522020 | 0.007826430 | 0.010 |
| 50 gwei | 0.032241600 | 0.032610100 | 0.039132150 | 0.050 |

最后一列仅是上限乘单价的付款能力检查，不是实际花费。节点要求部署时余额覆盖 1,000,000 × gasPrice；本次 local deploy + commit limit 小于 1,000,000，因此起始部署要求在当前模拟中更严格。超过阈值便不能保证原脚本发起交易。

| 初始余额 | 按当前原脚本的部署 Gas 上限，gasPrice 必须不超过 |
|---|---:|
| 0.0005 | 0.5 gwei |
| 0.001 | 1 gwei |
| 0.005 | 5 gwei |
| 0.05 | 50 gwei |

上述边界没有留费用上涨余量，不能当成资金建议。配置 RPC 后才能在当前单价下作足够/不足结论。

## RPC 后计算与证据范围

1. Chain ID 必须为 11155111，记录固定区块号和 hash，查询该区块钱包余额。
2. 原创建 bytecode 的 eth_estimateGas；若余额不足，尝试明确标注的合成余额覆盖估算，不冒充钱包资金充足。
3. 新合约尚未部署时，用编译器 immutableReferences 给模拟 runtime 的 publisher 绑定公开部署地址，覆盖到空模拟地址，用 eth_call 验证 publisher 后估算首次 commit；不是链上部署。RPC 不支持 state override 时保留失败类型并标注 commit 本地 EVM 后备值。
4. 查询 baseFeePerGas、eth_maxPriorityFeePerGas、五个区块 eth_feeHistory 和传统 eth_gasPrice。
5. 设部署/commit estimateGas 为 D/C，基础费为 B、建议小费为 T。协议基础费下界=(D+C)×B（不保证打包）；当前预计=(D+C)×(B+T)。建议预留=(ceil(1.2D)+ceil(1.2C))×(2B+T)。2 倍/5 倍基础费压力情景=(D+C)×(2B+T)/(5B+T)。未来两个交易打包价格可能不同，这些为同一快照情景估算。
6. EIP-1559 实际单价=min(maxFeePerGas, baseFee+maxPriorityFeePerGas)。原脚本使用 gasPrice，另算原脚本预计支出及节点余额检查；不会把 EIP-1559 建议参数伪称为原脚本已经采用。

参考：[Ethereum Gas 官方说明](https://ethereum.org/en/developers/docs/gas/)、[EIP-1559](https://eips.ethereum.org/EIPS/eip-1559)、[JSON-RPC estimateGas](https://ethereum.org/en/developers/docs/apis/json-rpc/#eth_estimategas)。
