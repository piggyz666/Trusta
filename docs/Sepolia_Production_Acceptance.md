# TRUSTA — Sepolia Production Acceptance

验收日期：2026-10-12（北京时间）。结论：**PASS**。

本报告中的 Production Acceptance 指真实公共 Sepolia 上的受控钱包交易验收，不代表 Ethereum 主网、生产银行资金或所有版本 MetaMask 的通用认证。

## 最终状态

```json
{
  "scope": "ETHEREUM_SEPOLIA_PUBLIC",
  "external_blockchain_verified": true,
  "production_compatible": true,
  "external_broadcasts_per_transaction": [1, 1],
  "external_broadcasts_total": 2,
  "final_acceptance": "PASS"
}
```

部署和 Root Commit 是两笔分别获得准确计划批准、由用户在 MetaMask 本人确认的交易。每笔广播一次，总计两次。最终属性来自 `data/sepolia-public-acceptance/final-evidence/Ingress_Delivery_Evidence.json`。独立链上证明中的 `production_compatible=false` 刻意不对钱包传输作认证；链上证明的 `external_blockchain_verified=true` 与本地持久化、浏览器证据共同形成上述完整验收结论。

## 实际架构流程

```text
用户逐笔准确计划审批 → MetaMask 本机签署（私钥不导出）
  → TRUSTA loopback ingress → 签名恢复 / Exact Plan 全字段匹配
  → durable raw transaction capture + nonce reservation
  → 持久化单次广播意图 → Infura Sepolia 广播
  → Sepolia receipt / canonical block / runtime / Publisher / Root read-back
  → Infura + Pocket Network 独立验证
  → 独立验证器 + 公开业务审计证明 + Etherscan 实际页面
```

MetaMask 活动 Sepolia RPC 实际切换到受控 ingress，并通过随机、短时、单次使用的 `eth_call` 路由挑战。没有因同 Chain ID 而信任网络名称，没有使用公共 RPC 绕过网关发送。原始交易签名恢复和字段匹配在 RPC 广播前完成；capture、nonce reservation 和发送意图先持久化。出现重复或不确定发送仅允许读回，不自动分配新 nonce 或再次广播。

## 真实交易与合约

目标链：Ethereum Sepolia，Chain ID **11155111**。钱包和 Publisher：`0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE`。

合约：`0x0A97FE534171aB6f1080Ba2F459f2DC54477322d`。

| 字段 | Deployment | Root Commit |
| --- | --- | --- |
| 交易哈希 | `0xb1188f5c170e84643d876c475e12d9829f6d1fcaf3df01eced5f81c8b8a7417f` | `0x51e78434bc887f1d8bf38cca63f168a7529aee06c74e9aa192322ecd86016b18` |
| Nonce / value / type | 0 / 0 / 2 | 1 / 0 / 2 |
| 区块 | 11892926 | 11892955 |
| Receipt Status | 1 | 1 |
| estimateGas | 3,802,284 | 356,369 |
| 批准的 Gas Limit | 4,562,741 | 427,643 |
| 实际 Gas Used | **3,771,187** | **352,405** |
| 实际 effectiveGasPrice | 1,000,017 wei | 1,000,017 wei |
| 实际费用 SepoliaETH | 0.000003771251110179 | 0.000000352410990885 |
| 准确计划最大费用 SepoliaETH | 0.000004562877882230 | 0.000000427655829290 |
| 最终双 RPC 确认深度 | 69 / 69 | 42 / 43 |
| 广播次数 | 1 | 1 |

部署区块哈希：`0xc7e57516378c5bab25a497dcb0f036d5ea0f8321cc3bc4a2b7a68345c1f5b303`。

Commit 区块哈希：`0xa37231b10961b582d8aa5a96a9dbe3a8862e5332ad65d3eb0f97311fcc24841f`。

两笔实际费用合计 **0.000004123662101064 SepoliaETH**。结束时只读余额为 **0.010295876337898936 SepoliaETH**，latest nonce=pending nonce=2。最低余额保护仍为 0.0002 SepoliaETH，未无限提高费用。

完整批准指纹：

- Deploy：`894a5f3b6c459eb215f26012c87a80639d02d5f21f83266ee4af4a85d2567451`
- Commit：`61ed17abef036bd39e0f43a753cb5f5d41823a8fdc46eba23d4e14c3733eb20b`

原始 creation bytecode SHA256：`c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`。

实际 runtime Keccak256：`0x8e36d0fcd7489c6f9a9352cbfbd01109f2d1f7d2bb8b8214cb5ebb02bc63efe9`，与原 Artifact 加真实 Publisher immutable 后完全匹配。历史区块和当前区块的代码、Publisher 均核验通过。

Etherscan 实际页面已经读取交易哈希、Success 状态与相同区块号：

- [部署交易](https://sepolia.etherscan.io/tx/0xb1188f5c170e84643d876c475e12d9829f6d1fcaf3df01eced5f81c8b8a7417f)
- [Root Commit](https://sepolia.etherscan.io/tx/0x51e78434bc887f1d8bf38cca63f168a7529aee06c74e9aa192322ecd86016b18)

这不等于 Solidity 源码已经提交 Etherscan 编译验证；本轮验证的是交易可公开查询、链上 runtime 与固定产物匹配。

## 真实业务审计 Root

Root 来源于原有多 Agent、Trusted Execution、Virtual Bank 实际完成的本地 0.01 元转账及其 50 个审计事件，**不是随机 bytes32，也不是外部银行支付**。金融写入仍经过原冻结的安全执行链。

- Task：`fd27b0e9-b2c5-4776-bc55-c5052dba4dc9`
- Receipt：`333e153b-7c98-4356-94c6-d5d8ca7938dd`
- Checkpoint：`e6f8ecb5-b75a-4032-aaca-25cdc8324fc6`
- Root：`7f8f330905d998e94816f9067912a603e3d82bcfb07c19bb8f4f942b97647e9a`
- Checkpoint hash：`1729d4648d6d7386dd01ac25a3d966c931d3f4d8ee1838032cdd29bd1b17057d`
- Sequence=50，Version=1，leaf_count=50，首末事件序号=1/50。
- Receipt event 的 payload commitment：`4b26815afac90bcfe04267ff32710878a87306ae1c275aa18c078f73619ce4dc`，按原规则排除 `audit_ref`，不是完整 JSON 含该字段的 hash。
- Pinned checkpoint key fingerprint：`0fb609bca93eed6db4d05ab946349c60dcf9d1f11c2f6392cdf9ded9f2534e29`。

独立验证器验证 Checkpoint ECDSA-P256 签名、RFC6962 Merkle Inclusion Proof、Receipt 与 Audit Event/task/action 对应、calldata、Commit Event、历史与当前 Root read-back。原始业务证明为不可覆盖的提交前快照，里面的 Anchor PENDING 是历史状态；现有 AnchorWorker 已通过原接口推进到 **CONFIRMED / externally_verified=true / verify_anchor=true**，详情在 `Core_Anchor_Worker_Verification.json`。该推进使用强制只读 RPC，不允许任何发送方法。

## 安全与实际联调修复

所有准确字段绑定均通过：chainId、sender、nonce、to、value、data、gas、type、EIP-1559 fees、accessList、计划指纹。签名恢复、低 s、原始交易 hash、nonce reservation、已批准且未过期的120秒报价、余额和费用硬上限均验证；两笔交易在允许窗口内只广播一次。链上成功不是依据网页 transaction hash 宣布。

真实联调仅修改两个 Anchor 验证辅助模块：

1. `backend/audit_trust/public_ingress.py`：RPC 的小写 `to` 字符串转换为同一20字节地址供 eth-account 重建交易，解决库的校验和字符串误拒绝。真实签名和全部字段检查保持不变。
2. `scripts/finalize_sepolia_evidence.py`：读取 Etherscan 交易专属 DOM 字段，避免把脚本内“Unable to locate a compatible web3 browser”误判为交易不存在。要求准确哈希、Success、准确区块，元数据或失败页面不能通过。

新增 `PublicSignatureEncodingTests` 与 `tests/test_sepolia_explorer_evidence.py`。六 Agent、银行执行、ECDSA/DID/ZKP/Merkle 核心算法未修改。

本次实际验证日志：

- `live-address-serialization-regression.txt`：26 项 Anchor 专项测试通过，13.301 秒。
- `explorer-signature-regression.txt`：4 项编码/浏览器正负例通过，0.121 秒；其中2项与上面重叠，不累计为30项独立测试。
- `Independent_Verifier_Execution.json`：独立 CLI 实际运行成功，exit code=0，reason=VERIFIED；不打开原业务数据库，不需要钱包私钥。
- 已有本任务全量日志 `data/zero-defects/sepolia-public-final-result.json` 为829项通过、0跳过、355.013秒；这在上述最后两个兼容修复之前执行。本次遵照当前要求没有重跑全量测试，不将旧结果冒充最终源码的全量结果。

## 证据交付与限制

最终证据目录：`data/sepolia-public-acceptance/final-evidence/`。

包含 Deployment/Commit Receipt、ExternalAnchorProof、Independent_RPC_Verification、Ingress_Delivery_Evidence、Business_Record_Proof、Pinned 公钥、Checkpoint、两份 Exact Plan、独立验证器结果、原 AnchorWorker 结果与 SHA256 Manifest。JSON 不含私钥、密码、API Key 或 ingress capability URL。原始签名交易是公开链上数据。

旧 LOCAL_PYEVM_ONLY acceptance 与第一次独立验收的失败结果均保留，没有覆盖成成功。当前最终结论只适用于这两笔 MetaMask 实际签署、准确批准且经受控网关送达的公共 Sepolia 交易；不是通用钱包路由认证。未来操作仍必须关闭 Smart Transactions、通过新的实际路由挑战、获得新 Exact Plan 逐笔审批。3个确认是本阶段约定确认门槛，不等于绝对不可逆；未来查询会继续检查 canonical block/reorg。

钱包私钥未导出，未调用主网，未操作外部银行资金。验收完成后 operator 已停止，session approved=false；不会自动续签、续报、提交新 Root 或再次广播。
