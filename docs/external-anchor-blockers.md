# Sepolia 外部验收阻塞条件

当前状态：**BLOCKED_BY_EXTERNAL_CREDENTIAL**，`external_blockchain_verified=false`。当前没有运行真实部署、广播、银行付款或消耗测试 ETH。

本地缺少 ANCHOR_RPC_URL、ANCHOR_CONTRACT_ADDRESS、ANCHOR_WALLET_PRIVATE_KEY；独立复核还缺 ANCHOR_VERIFY_RPC_URL。External Anchor Worker 未启用。因此链 ID、实际钱包余额、已部署合约、确认深度均尚未在外部网络核实。不要把钱包变量存在当成已确认 Gas 足够。

最小补齐步骤：

1. 用户在本地配置 Sepolia RPC、受控测试钱包和少量免费测试 ETH；不要在聊天中发送私钥/助记词。
2. 配置现有 AuditAnchor 合约地址；若未部署，另行批准固定目标网络与钱包的部署。发布者必须与受控钱包一致。
3. 为只读验证脚本配置另一个 RPC endpoint；两个地址不同不自动证明运营商独立，报告仍须说明各自来源。
4. 先运行只读配置检查，核对 chain_id=11155111、合约字节码、发布者和余额；再针对具体 checkpoint/合约批准 Worker commit。
5. Worker 继续走现有 outbox；等待至少 3 个确认，再导出公开签名 checkpoint，并从原提交进程之外运行独立 verifier。签名公钥必须提前信任，不能仅接受证据自带公钥。

既有代码上限（不是操作授权）：commit gas≤200,000、gasPrice≤100 gwei，最大 0.02 测试 ETH；deploy gas≤1,000,000、相同价格上限，最大 0.1 测试 ETH。实际 gas 估算与合约目标应在审批时重新给出。本轮没有请求广播批准，因为连可核实的目标合约和钱包都未配置。

已新增 `scripts/check_external_anchor.py`（默认仅检查配置，可选只读探测）与 `scripts/verify_external_anchor.py`（公开证据、两个 RPC、无原数据库、无钱包、无广播）。独立验证覆盖签名、链 ID、目标、发布者、calldata、回执、canonical block 中的交易成员、事件绑定、确认深度、历史/当前 read-back 与重复读取稳定性。

现有 outbox 幂等测试和真实本地 EVM 的重复提交/冲突回滚测试保持不变。新增 fixture 验证只标记 LOCAL_VERIFIED；它们不能证明已提交 Sepolia。上一轮 `external-anchor-proof.json` 保留原状，不以本地假交易哈希覆盖。
