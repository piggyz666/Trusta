# Third-Party Verification Report

结论：**VERIFIED**。已实际运行 `scripts.verify_sepolia_acceptance`，exit code=0。最终结果在 `data/sepolia-public-acceptance/final-evidence/Independent_Verifier_Execution.json`。此程序没有读取操作员私钥或原业务数据库，仅使用公开证据、预先固定的公钥和两个独立 RPC。

第三方可在项目根目录使用以下命令复验。先在自己的本机环境安全配置 `ANCHOR_RPC_URL` 与 `ANCHOR_VERIFY_RPC_URL`，必须为不同服务商的 Ethereum Sepolia HTTPS RPC。无需钱包、签名、余额或发送权限。

```powershell
python -m scripts.verify_sepolia_acceptance `
  --deployment-plan data/sepolia-public-acceptance/final-evidence/Deployment_Exact_Plan.json `
  --deployment-tx 0xb1188f5c170e84643d876c475e12d9829f6d1fcaf3df01eced5f81c8b8a7417f `
  --commit-plan data/sepolia-public-acceptance/final-evidence/Commit_Exact_Plan.json `
  --commit-tx 0x51e78434bc887f1d8bf38cca63f168a7529aee06c74e9aa192322ecd86016b18 `
  --bundle data/sepolia-public-acceptance/final-evidence/Business_Record_Proof.json `
  --public-key data/sepolia-public-acceptance/final-evidence/audit-public-key.pem `
  --output data/sepolia-public-acceptance/third-party-recheck.json
```

必须将收到的公钥指纹通过可信渠道与已固定值核对：`0fb609bca93eed6db4d05ab946349c60dcf9d1f11c2f6392cdf9ded9f2534e29`。不能单凭证明自己附带的公钥宣布签名可信。证据 SHA256 Manifest 可检测文件变化，其自身也需可信分发，不替代链上或签名验证。

实际执行覆盖：目标链11155111、部署签名恢复及准确字段、低s与交易hash、成功回执、合约 CREATE 地址及固定runtime、Publisher、双RPC区块/回执一致性、至少3确认、canonical block稳定读回、业务Checkpoint签名、Receipt/Event因果绑定、Merkle Inclusion、Commit准确计划、事件、历史与当前Root读回。

独立链上验证器的 `production_compatible=false` 表示它不负责证明 MetaMask 的传输路线；不能把它解释为链上验证失败。钱包传输的持久化记录与签名恢复由 `Ingress_Delivery_Evidence.json` 单独验收，完整阶段结论为 PASS / production_compatible=true，范围限于这两笔真实操作。独立验证器不能通过本地日志证明未来交易不会绕路。

公开交易可在 [Deployment](https://sepolia.etherscan.io/tx/0xb1188f5c170e84643d876c475e12d9829f6d1fcaf3df01eced5f81c8b8a7417f) 与 [Commit](https://sepolia.etherscan.io/tx/0x51e78434bc887f1d8bf38cca63f168a7529aee06c74e9aa192322ecd86016b18) 查询；本轮实际网页的准确交易哈希、Success与区块号核验均通过。未宣称完成 Etherscan Solidity 源码发布认证。
