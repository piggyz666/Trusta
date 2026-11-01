# Sepolia Deployment Readiness Report

日期：2026-10-10（Asia/Shanghai）。状态：**部署准备完成，实际执行尚未授权；未部署、未产生真实签名、未广播、测试币消耗为 0。**

## 1. 范围与冻结边界

本轮仅修正 External Anchor、部署/估算工具和 Anchor 测试夹具。已有六 Agent、Blackboard、金融执行链、银行适配器、ECDSA、DID、ZKP、Merkle 算法均未修改。Solidity 源码和部署产物未修改。

既有后端仅 `backend/audit_trust/external_anchor.py` 改动；新增 `backend/audit_trust/transaction_safety.py`。既有测试仅 `tests/test_phase6_anchor.py` 夹具补充动态报价、余额、Publisher RPC 和合成逐笔批准，原断言保留，未删除测试。完整源文件哈希比对见 `sepolia-readiness-verification-manifest.json`。

## 2. 目标链、钱包、产物

| 项目 | 检查结果 |
|---|---|
| 目标网络 | Ethereum Sepolia |
| 强制 Chain ID | 11155111 |
| 本地 RPC 配置 | `.env` 中 Infura 主机名已从 mainnet 改为 sepolia；Key 保留、不输出 |
| 配置优先级 | 当前进程环境变量优先于 `.env`；残留主网环境变量会触发强制拒绝 |
| 部署钱包公开地址 | `0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE` |
| 当前余额 | 0.0103 SepoliaETH |
| 预计 constructor Publisher | 同上述部署钱包公开地址 |
| 编译器 | solc 0.8.24 |
| Solidity 源码 SHA-256 | `c18ab76d2f695f6c119aa6ad78ee574067e4524baef8cf2c166b18e3f0b580dc` |
| 创建字节码 SHA-256 | `c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17` |
| 当前钱包 Publisher 绑定后的 runtime Keccak-256 | `0x8e36d0fcd7489c6f9a9352cbfbd01109f2d1f7d2bb8b8214cb5ebb02bc63efe9` |
| nonce=0 对应预测合约地址 | `0x0A97FE534171aB6f1080Ba2F459f2DC54477322d`，nonce 改变则必须重新计算 |

已验证钱包当前无合约代码，符合本次 EOA 本地签名路径。Publisher 和 runtime 匹配是构造器/代码模拟验证；没有真实部署合约，因此不存在已确认的链上 Publisher、部署 receipt 或 Anchor proof。实际签名账户的控制权尚未通过真实签名验证。

实时 estimate 的区块为 11879165，区块时间北京时间 2026-10-10 02:32:24；部署 unsigned plan 又重新读取 latest/pending 进行余额、nonce 与费用复核。精确区块号/hash、时间及报价字段保存于 JSON。两个快照并非承诺未来打包费用。

## 3. 链隔离与代理

- 准备、签名、提交入口均强制 RPC Chain ID=11155111；错误网络在 nonce、费用、签名和广播之前拒绝。签名前和广播前再次检查。已缓存的 outbox 提交引用也先验链，不跨网络返回成功。
- 所有交易强制 type=2、chainId=11155111、value=0；禁止未知签名字段和非空 accessList。signed raw transaction 的 chainId、nonce、目标、calldata、Gas、费用及恢复出的 sender 必须匹配已批准计划。
- 部署仅接受固定审核过的源文件/创建字节码；commit 仅接受受限 commit ABI、版本 1、非零 checkpoint/root、uint64 sequence，并绑定本地 checkpoint root。
- commit 检查部署代码存在、代码 hash 精确匹配 `ANCHOR_CONTRACT_CODE_HASH`，以及链上 `publisher()` 等于签名钱包。
- EthereumRPC 独立使用系统代理，关闭重定向、限制响应大小，拒绝远程签名方法。银行 BankTransport 源文件及其代理规则不变。
- 广播默认禁止；仅已批准计划的精确 signed bytes 可通过短暂内部广播许可。传输异常不输出 URL/Key，不重新分配 nonce；原始 signed bytes 在广播前耐久保存，后续需调查而非重新付款。

## 4. 实时 Gas 与动态费用

| 操作 | RPC estimateGas | 20% 余量后的 Gas Limit | 允许的最大 Gas |
|---|---:|---:|---:|
| 部署 | 3,802,284 | 4,562,741 | 5,000,000 |
| 首次新 root commit | 356,357 | 427,629 | 600,000 |

旧固定 1,000,000 / 200,000 上限已移除。GasLimit=ceil(estimateGas×1.2)，超过最大值则拒绝，不能静默截断。commit 使用 RPC 状态覆盖模拟尚未部署的新合约；不是重复已存在 root 的低费估算。实际提交真实 checkpoint 时仍需重新 estimateGas。

本轮动态策略只采用 `eth_maxPriorityFeePerGas` 节点建议，**不按近期交易高小费抢跑**：

```text
maxPriorityFeePerGas = 节点建议
maxFeePerGas = 2 × 当前 baseFeePerGas + 节点建议
```

当前节点建议小费=1,000,000 wei（0.001 gwei），部署计划 baseFee=14 wei，maxFee=1,000,028 wei。不使用无限涨价/循环替换；若节点建议超过 0.1 gwei，或 maxFee 超过 2 gwei，直接拒绝，等待重新报价和批准。近期交易中位数约 1 gwei 的保守费用分析保留于只读估算数据，但不会被执行工具自动采用。

| 当前计划费用 | 上限（SepoliaETH） |
|---|---:|
| 部署 GasLimit × maxFee | 0.000004562868756748 |
| commit GasLimit × 同一 maxFee 情景 | 0.000000427640973612 |
| 合计报价最坏费用 | **0.000004990509730360** |
| 扣除上述费用后的余额 | **0.010295009490269640** |

这是当前报价的最多可扣手续费，不是已发生消耗。未来 commit 要重新报价；报价预算不是无限期保证。基础费上涨后需要重新准备，未经批准不能更改费用。

## 5. 绝对费用与余额保护

| 保护项 | 硬限制 |
|---|---:|
| 最大 priority fee | 0.1 gwei |
| 最大 maxFeePerGas | 2 gwei |
| 部署单笔总费用 | ≤0.0085 ETH |
| commit 单笔声明总费用 | ≤0.0015 ETH |
| commit 叠加 Gas 上限后的更紧费用限制 | 600,000×2 gwei=0.0012 ETH |
| 两笔策略下可接受费用的最坏合计 | **≤0.0097 ETH** |
| 钱包强制剩余余额 | **≥0.0002 ETH** |

两笔单笔声明总额为 0.01 ETH，但考虑 commit Gas 与单价上限后的紧约束是 0.0097。钱包 0.0103 在该紧约束下剩余 0.0006，仍高于 0.0002 的保护余额；即使按较松的声明上限 0.01，剩余 0.0003 也符合保护。

准备与签名前查询 pending nonce/余额；广播前重新验证 nonce、Gas、报价和余额。余额必须覆盖批准交易 `gas×maxFee`，并额外保留余额底线，不能只用最低有效费用判断付款能力。

| 假设初始余额 | 当前报价 + 0.0002 保护余额 | 覆盖所有策略允许的最坏合计 + 保护余额 |
|---|---|---|
| 0.0005 | 足够 | 不足 |
| 0.001 | 足够 | 不足 |
| 0.005 | 足够 | 不足 |
| 实际 0.0103 | 足够 | 足够 |

有效价格到 1 gwei 时，此 Gas 余量的两笔费用约 0.004990370；2 gwei 时约 0.009980740，但部署单笔总费用会先触发 0.0085 的限制，因此当前策略会拒绝该情景，不会为完成任务放宽上限。5 gwei 情景直接超出单价保护，拒绝执行。

## 6. 本地签名准备与剩余阻塞

推荐签名路径为本机受控的 **Web3 加密 keystore**：`ANCHOR_KEYSTORE_PATH` 指向仓库外的加密文件，解锁口令只放在操作员当前进程的 `ANCHOR_KEYSTORE_PASSWORD` 中，不写模板、不发聊天。解密后账户地址必须匹配批准计划 sender。兼容原来的操作员私钥环境变量路径，但不是必须选择，也不要求在聊天中暴露 MetaMask 私钥。

当前只配置了钱包公开地址，没有受控 keystore 或私钥环境变量。只读检查未加载、解密或读取真实私钥；无法声称本机已经能够控制该钱包签名。合成测试账户的本地签名/恢复与 keystore 解密路径已测试。

当前 `ANCHOR_ALLOW_SIGNING=false`、`ANCHOR_ALLOW_BROADCAST=false`、`ANCHOR_APPROVED_PLAN_HASH` 为空。签名还须匹配完整 unsigned plan SHA-256；计划包含 sender、nonce、目标、calldata、Gas、费用、时间/区块等。报价 TTL=120 秒，超时或 nonce/费用/Gas 需求上升必须重新准备并重新批准，不能继承旧审批。

第二 RPC：当前 `ANCHOR_VERIFY_RPC_URL` 未配置。后续需不同运营者（例如 Infura 主 RPC + 另一独立服务商），两端必须各自只读验证 Chain ID=11155111；仅同一服务商两个不同 URL 不等于独立来源。`verify_external_anchor.py` 和 `verify_two_rpcs` 已保留双来源一致性校验。验收不需要钱包私钥。

剩余阻塞：

1. 本地受控签名账户尚未配置，尚未验证其地址是否为该公开钱包。
2. 第二个独立运营 RPC 尚未配置和验证。
3. 用户未批准任何具体链上交易；签名/广播仍关闭。
4. 合约尚未部署；实际代码、Publisher、receipt、首次 commit/readback/确认深度等需未来外部验收。本轮不伪造成功 proof。

## 7. 测试与交付证据

- Anchor 专项：**49/49 通过**（新增准备测试 26 + 原 Anchor/独立证据测试 23）。
- 全量回归：**693/693 通过，0 跳过，48 个模块，274.997 秒**；原 667 项全部保留。
- 覆盖错链/未知链、Gas/小费/总价上限、余额底线、产物/ABI/版本、代码 hash/Publisher、批准指纹与到期、nonce 改变、签名钱包不匹配、signed bytes 变更、默认广播拒绝、加密本地合成账户，以及原有签名/重放/注入/金融安全测试。
- 部署 CLI 默认路径已在真实 Sepolia RPC 上只读验证，主网拒绝亦经离线检查；本轮实际执行只读 readiness 路径。
- `.env` 已被原 Git ignore 规则忽略；JSON 报告未包含 RPC URL 或 Key。只输出公开钱包、hash、报价、nonce 等信息。

证据文件：`sepolia-deployment-readiness.json`、`sepolia-anchor-targeted-tests.txt`、`sepolia-readiness-full-tests.txt`、`sepolia-readiness-verification-manifest.json`。

## 8. 最终操作清单（不是本轮执行授权）

1. 在本机准备受控加密 keystore，核对公开地址；口令只在本机当前进程设置。禁止把私钥、Key 或口令发聊天。没有该钱包控制权时不要尝试其他账户替代。
2. 设置第二个独立 RPC，只读验证两边网络和一致性；外部 Worker 保持关闭。
3. 使用 `python scripts/deploy_audit_anchor.py --output data/sepolia-unsigned-deployment-plan.json` 重新生成 unsigned 部署计划；该命令默认只准备、不签名、不广播。
4. 向用户呈现目标链、sender、nonce、产物 hash、Gas/费用上限、余额保护及计划指纹。获得该笔交易明确批准后，才允许本地操作员设置对应的签名/广播开关与 exact plan hash。到期则重新准备和批准。
5. 授权后仅部署一笔；保存已知 hash/raw transaction，先独立检查部署 receipt、代码 hash、Publisher 和确认深度。异常不得自动换 nonce 重部署。
6. 部署验证后再配置 `ANCHOR_CONTRACT_ADDRESS` 与报告中的精确 runtime `ANCHOR_CONTRACT_CODE_HASH`。
7. 为真实签名 checkpoint 调用 ExternalBlockchainAnchor.prepare_commit() 生成全新的 commit unsigned plan，检查 root、checkpoint hash、标识、序号、版本和费用。通过 submit(..., approved_plan=原计划) 使用被批准的计划，禁止以新时间戳替换原审批。用户另行逐笔批准后才签名/提交；部署批准不包括 commit。
8. 通过两个独立 RPC 验证交易/收据/事件、历史及当前 readback、确认深度、规范区块和本地 checkpoint 签名，成功后再生成真实 external-anchor-proof。独立验证是只读操作，不另花链上 Gas。

本轮没有请求、获得或使用真实交易执行授权。当前报告中的报价指纹只作准备证据，随时间过期，不能作为未来广播凭证。
