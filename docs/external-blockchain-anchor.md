# External Audit Anchor

选择 Ethereum Sepolia（chainId 11155111），保留 LocalDevelopmentAnchor。Sepolia 用于应用测试；Hoodi 更侧重验证器/网络测试；许可链需要额外运维与身份配置。本阶段只实现一个 EVM testnet Adapter，没有添加第二条链。依据：[官方测试网络说明](https://ethereum.org/developers/docs/networks/)、[JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/)。

最小 AuditAnchorContract 仅写 checkpoint hash、Merkle root、checkpoint ID commitment、序号、schema version。publisher 为部署者；重复同内容幂等，冲突拒绝；发 Committed 事件，提供 read/verify。源码、solc 0.8.24 ABI/bytecode 均在 contracts/。不写客户资料、账户、金额、模型提示或凭证。

审计产生签名 checkpoint 与 outbox → 独立 AnchorWorker → ExternalBlockchainAnchor → JSON-RPC。部署由 `python -m scripts.deploy_audit_anchor` 本地操作员触发；worker 使用现有 `python -m scripts.audit_worker`。任何 RPC/链失败均不进入银行执行主路径。DID/ZKP 原扩展边界继续保留，没有虚构实现。

提交前保存 nonce 已签名交易、已知 tx hash、根/目标链/合约/输入绑定；丢失响应不分配新 nonce 或重发新交易。现有 worker 明确重试后仍返回同一 known hash；未广播而已落盘的情况需要操作员调查，未实现自动 rebroadcast。

独立 inspect 同时查 tx 和 receipt，并检查 chainId、目标合约、publisher、输入、成功状态、checkpoint/root 事件、receipt 区块是否 canonical、确认深度、可选 finalized 高度、合约该区块和 latest read-back、再次读取 receipt 防重组。任何不一致为 SUBMITTED/未验证；只有 tx_hash 不够。证明每次查询重新核验，不能永久缓存 CONFIRMED。

已实际编译合约，并用 EthereumTester/PyEVM 测试提交/读回/事件、重复/冲突、非 publisher 拒绝；JSON-RPC 证明测试包含错链/合约/失败收据/事件/读回/确认数/重组。**这些是本地证据。** 当前缺 RPC URL、测试钱包、gas 资金与已部署合约，外部验证为 BLOCKED_BY_EXTERNAL_CREDENTIAL。证据文件 external-anchor-proof.json 中 external_blockchain_verified=false。

配置：ANCHOR_RPC_URL、ANCHOR_CONTRACT_ADDRESS、ANCHOR_WALLET_PRIVATE_KEY、Sepolia gas；可选 ANCHOR_CONFIRMATIONS、ANCHOR_REQUIRE_FINALIZED；服务启用 TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED=true。私钥不能进入报告/日志。未安装可选依赖时使用本地 anchor；外部和 EVM 测试依赖见 requirements-phase6.txt。
