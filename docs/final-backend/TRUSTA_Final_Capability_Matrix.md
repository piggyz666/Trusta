# 最终能力矩阵
状态采用 EXTERNAL_VERIFIED / LIVE_TESTED / LOCAL_VERIFIED / IMPLEMENTED_NOT_VERIFIED / BLOCKED_EXTERNAL / FAILED / NOT_IMPLEMENTED / NOT_APPLICABLE。FROZEN 是变更策略，不是证明等级。

| 能力 | 状态 | 实际范围 / 证据 | 变更策略 |
|---|---|---|---|
| 六 Agent / Blackboard / Bus / Scheduler | LOCAL_VERIFIED | 全量回归、六场景 HTTP | FROZEN |
| 金融执行安全管线 | LOCAL_VERIFIED | 45 银行攻击、真实账本与回执 | FROZEN |
| 六场景虚拟银行 | LOCAL_VERIFIED | six-scenes-demo.json，11 证明及 TIME 恢复 | FROZEN |
| 官方 Revolut 账户/余额/流水/账单 | LIVE_TESTED | bank-live-read.json，本轮 5 账户 | FROZEN |
| 官方沙箱历史付款/SETTLED 读回 | LIVE_TESTED | bank-payment-readback.json，14 检查；模拟器曾经获准推进 | FROZEN |
| 生产银行真实资金 | NOT_APPLICABLE | 本轮禁止，企业资格及银行合同也未具备 | 不宣称支持实测 |
| 多银行抽象接口 | LOCAL_VERIFIED | SDK/manifest/字段映射/unsupported 拒绝 | FROZEN；不是全球银行兼容证明 |
| 外部银行 AA/理财/卡写能力 | BLOCKED_EXTERNAL | 当前 Revolut manifest 不提供相应能力 | 不伪造支持 |
| AA 实际本地收款 | LOCAL_VERIFIED | 独立付款授权/余额/回执/取消/重试测试 | FROZEN |
| DeepSeek 接入及新显式多目标 NL | LIVE_TESTED | 6/6 B_GRAPH，小型作者合成集；无自动资金审批 | 有限范围 |
| GLM Provider 接入 | LIVE_TESTED | 23 次真实 JSON/usage；免费 4-flash | 保留兼容 |
| GLM 合法规划质量 | FAILED | B_GRAPH 0/6；合法 graph 不等于完整正确任务 | 不作为可靠默认规划器 |
| 中文金额提示词格式修复 | LIVE_TESTED | 修复后 DS 1 个 dev 样本成功；新泛化未测 | 继续质量门控 |
| 自然语言自由条件/任意重规划 | NOT_IMPLEMENTED | 有限 NL 语法以外仍拒绝；Core 有注册 DAG 分支 | 不夸大 |
| 通用注册 DAG / 多轮补槽 / 取消 | LOCAL_VERIFIED | 32 节点/20 深度上限，21 新测试、19 原图测试 | 安全编译器 FROZEN |
| 每 Agent 密钥 / ECDSA V3 | LOCAL_VERIFIED | 原通信攻击及持久化、31 隔离/恢复测试 | FROZEN |
| Finance 子进程隔离 | LOCAL_VERIFIED | 实际 Windows AppContainer，窄只读 broker | 有限范围 FROZEN |
| 所有 Agent 操作系统隔离/HSM | NOT_IMPLEMENTED | 其他 Agent 和可信 Core 仍在宿主 | 不夸大 |
| DID_KEY_VERIFIED | LOCAL_VERIFIED | did:key 公钥/身份/能力绑定与撤销；非外部 DID 网络 | FROZEN |
| REAL_ZKP_VERIFIED | LOCAL_VERIFIED | 实际 Groth16 电路/证明/公有输入/重放/过期测试 | 开发 setup，非生产 ceremony |
| LOCAL_MERKLE_VERIFIED | LOCAL_VERIFIED | 签名检查点、固定信任公钥、包含证明、篡改拒绝 | FROZEN |
| 协作 handoff 证据 | LOCAL_VERIFIED | 签名因果依赖链/导出/本机受信 registry 验证 | 不证明银行事实本身 |
| Sepolia 主 RPC / 余额 / Gas 准备 | LIVE_TESTED | 11155111、0.0103 ETH、RPC estimateGas | 已有保护 FROZEN |
| 本机加密 signer 工具 | LOCAL_VERIFIED | 地址匹配、加密、ACL、禁止覆盖；仅临时测试密钥 | 未登记真实钱包 |
| 真实钱包签名与具体 plan 审批 | BLOCKED_EXTERNAL | 只有公开地址；signing/broadcast=false | 必須操作员参与 |
| 独立第二运营商 RPC | BLOCKED_EXTERNAL | 已尝试公开服务，未通过链/共同区块检查 | 不将两 URL 当独立供应商 |
| EXTERNAL_BLOCKCHAIN_VERIFIED | BLOCKED_EXTERNAL | 未部署、未 commit，External_Anchor_Proof.json 不生成 | 不假造链证明 |
| 当前可靠性和安全 | LOCAL_VERIFIED | 714 全量、80 HTTP、1000 审计事件 | 非生产 SLA |
