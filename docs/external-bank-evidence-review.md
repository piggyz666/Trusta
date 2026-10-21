# 外部银行证据复核

本轮仅离线复核此前 Revolut 官方 Sandbox 的同一笔0.01 EUR→Acme Corp.，未再次调用银行 HTTP 或发起付款。固定 `revolut-transfer-audit-public.pem` 验证回执签名检查点与 Merkle 包含证明，回执 task/transaction/receipt ID 与历史 settled read-back 一致；历史20项检查全部记录为 true。新复核结果和来源 SHA-256 见同名 JSON。

原任务 MANUAL_REVIEW 与第一次 PENDING 记录仍保留，后续 supplementary verification 为 VERIFIED。银行侧模拟结算由单独操作员控制推进，付款 claim 仅1次。不能把模拟结算称为生产清算，也不能把本地 audit signer 称为银行签章。历史记录中的银行字段是受控连接器当时读回，本轮证明的是该历史证据完整性，不是银行当前状态或新银行实测。

Revolut Adapter、OAuth、原生币种读取、单次写登记、独立核验、补充回执已存在并纳入原测试。本轮保留 SDK 默认 Virtual，不改变资金开关和原银行权限。未验证生产账户/KYC、生产凭据、真实资金、生产银行 SLA、共享限流或额外银行高级业务支持；Starling 官方 live 仍未验证。

Sepolia 的 RPC、合约地址和钱包配置均缺失，gas/合约部署也未确认。`external-anchor-status.json` 标明 BLOCKED_BY_EXTERNAL_CREDENTIAL，没有 tx_hash；本地 EVM 测试不替代外部链。DID 为现有 P-256 did:key 绑定；Groth16 为真实本机证明但开发单机 setup、本地事实签发，未提升为生产隐私资质。
