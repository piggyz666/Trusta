# 集中的剩余阻塞及操作员步骤
不重复注册 Infura、不创建新钱包；使用当前 Sepolia 公开地址。当前没有批准的 exact plan，也没有真实 signer。登记 signer 不代表批准交易。

1. **独立 RPC**：在已忽略 .env 的 ANCHOR_VERIFY_RPC_URL 填另一运营商 HTTPS Sepolia endpoint；不要发到聊天。Infura 换一个 URL 不能算独立。验证 11155111 和共同区块 hash；若失败继续阻塞，不能关闭 TLS 绕过。
2. **现有钱包 signer**：可用既有兼容加密 keystore，或本人在本机运行 `python -m scripts.anchor_operator enroll-keystore`，在隐藏控制台输入现有钱包 key，生成 data/anchor-wallet.json。绝不发聊天/命令行 argv/LLM。该工具只核对地址、加密与文件权限，没有签名或广播。12+字符密码只通过本人进程环境 ANCHOR_KEYSTORE_PASSWORD，.env 仅填 ANCHOR_KEYSTORE_PATH，别保存密码或明文 key。若不愿导出钱包，请保留阻塞，另做已批准兼容钱包签名器，不换资金账户。
3. **准备与核对**：`python -m scripts.sepolia_deployment_readiness --output data/sepolia-readiness.json`；再 `python -m scripts.deploy_audit_anchor --output data/anchor-deploy-plan.json` 仅生成未签名计划。查看 chain、sender、nonce、源码/字节码 hash、gas、费用上界及残余余额。TTL 120 秒；不执行本次过期报告中的 plan。
4. **逐笔明确批准**：批准全字段 exact plan hash 后，操作员才临时设置 ANCHOR_APPROVED_PLAN_HASH、ANCHOR_ALLOW_SIGNING=true、ANCHOR_ALLOW_BROADCAST=true；未批准时保持 false。随后现有部署入口 `--execute-approved-plan data/anchor-deploy-plan.json` 才能签名/广播。这里给出的是将来批准后的流程，本轮未执行，也不是本报告自动批准任何交易。过期/费用/nonce变化必须重新提案和明确批准，不静默提价。
5. **核实部署然后单独准备 commit**：真实 deployment receipt/code/publisher 与主、第二 RPC 对齐；用真实业务 Audit Log 的签名 checkpoint，经过原 ExternalBlockchainAnchor.prepare_commit 与 durable outbox/worker。commit 的金额/目标/calldata/nonce/hash 单独批准，不能把部署批准沿用到 commit。若广播回应丢失先查既有 tx hash，禁止重发新 nonce。
6. **独立链验收**：原 `python -m scripts.verify_external_anchor` 使用导出的实际 checkpoint、公钥 pin、真实合约/Publisher/tx hash/code hash、两个独立运营商，至少 3 confirmations 并检查 canonical/reorg、receipt、event、root readback。全部通过才生成 External_Anchor_Proof.json，并将 external_blockchain_verified 置 true。

以上是一次集中的操作流程，但包含两笔各自精确批准；密码与 key 不发送给助手。外部阻塞所需的不可替代权限来自钱包和现有 exact-plan 安全规则，本报告不能替代人类授权。

**模型质量**：本轮累计请求 800/800，费用预算未耗尽但次数上限已到，不继续付费调参。GLM-4-flash 规划质量 FAILED；4.7 曾限流，新模型/更大新数据评测需要新预算授权。不要为了提升通过率放宽 Validator。DeepSeek 新多目标显式语法样本可展示，真实多轮理解/自由条件仍不具备泛化证明。

**外部银行 AA/六场景写能力**：当前沙箱 manifest 缺这些能力，继续使用虚拟银行实际状态演示；未来需银行提供有授权的 Connector/Capability。生产企业开户和真实资金不在本轮授权范围。无须为比赛本轮重新开企业账户。
