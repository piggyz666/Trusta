# 可信 Agent 与安全评估
金融安全和密码核心保持 FROZEN，证据 LOCAL_VERIFIED，非生产渗透测试。

ECDSA P-256 V3 核查主体、元数据、sender/receiver、capability commitment、task/graph/version、nonce、密钥轮换/撤销和持久化重放。合法签名之后仍需编译器依赖与 capability 检查，绝不等于动作授权。没有新增 Agent 或让 LLM 持有 bank handle。

受控本机进程隔离/攻击/恢复 31 项全通过，0 skip，实际 Finance AppContainer 尝试高权限访问、凭据和 broker 越权、跨进程签名、重放/重启复用结果测试；仅覆盖 GENERAL_GRAPH Finance 读取/汇总/异常路径。其他 Agent 与可信 Core/issuer 仍宿主执行；每 Agent 密钥文件分离不等于全部 Agent 操作系统隔离或 HSM。

银行攻击基准 45/45 通过，完整银行状态检查未经授权变化 0。全量覆盖身份伪造、receiver/payload替换、金额/收款人/authorization hash、图和编译器篡改、审计事件/root/proof、错误链/伪 receipt、过期/重放、provider injection、未知银行交易幂等。新 NL 模型输出需语义核对；GLM 错计划被拒绝，不能以“验证器挡住”当作模型正确。

did:key 实际公钥/身份绑定并校验，是 DID_KEY_VERIFIED 本地能力；非链上 DID 注册/外部凭证生态。Groth16 采用实际 snarkjs 电路和公有输入，6 项 privacy proof 测试不跳过，检查 issuer、权限/用户/动作绑定、过期、重放、当前权威余额与风险；REAL_ZKP_VERIFIED 限本地开发 setup，不是多方 ceremony。ZKP 不能替代 Policy、Authorization 或 Contract。

Merkle 校验含签名 checkpoint、受信钥 pinning、实际业务事件包含证明与篡改拒绝；LOCAL_MERKLE_VERIFIED 不等于 EXTERNAL_BLOCKCHAIN_VERIFIED。签名 handoff 因果链证明“谁对何数据签名并依赖什么”，不证明每个银行事实都正确，也不能抵御掌控全部 Core 和 issuer 私钥的对手。

真实模型/银行/RPC实验分开存证。不存在外部链证明；无真实钱包签名/广播/生产银行转账。本轮检查 secret 实值不进入交付证据，Secret_Scan.json 只记录类别/命中数。开发密钥、数据库、keystore 被忽略；不能凭本报告承诺生产密钥管理、银行合规或整体环境安全。
