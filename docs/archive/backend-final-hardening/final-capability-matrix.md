# 最终能力矩阵

| 能力 | 状态 | 证据范围 |
|---|---|---|
| 六 Agent / 金融安全边界 | LOCAL_VERIFIED | 737 全量 + 45 攻击，源码不变 |
| 六场景 HTTP / AA | LOCAL_VERIFIED | 6/6、11 proofs；三位独立付款、本地账本 |
| 自然语言条件 / 三月分析 / 多轮订阅预算 | LOCAL_VERIFIED | 23 新回归 + 当前 Demo，不是实时模型质量 |
| 真实 DeepSeek/GLM 新准确率 | BLOCKED_EXTERNAL | 旧 800 请求耗尽；历史 46 响应重新离线分析 |
| GLM-4-flash 历史 B_GRAPH | FAILED | 0/6；不等于所有 GLM 模型质量 |
| 任意口语/AA通用混合图 | NOT_IMPLEMENTED | 有限规范化语法未覆盖全部合法自由表达 |
| 新的实时独立盲测 | BLOCKED_EXTERNAL | 16 项作者准备 holdout 未运行；独立 gold 与新调用预算未提供 |
| 官方银行已有沙箱证据 | LIVE_TESTED | 保留历史账户/GBP账单/唯一0.01EUR SETTLED；本輪不重复 |
| 外部 AA/理财/卡订阅金融写 API | BLOCKED_EXTERNAL | Manifest unsupported；虚拟场景通过 |
| 生产真实资金 | NOT_IMPLEMENTED | 本轮禁止，未测试 |
| ECDSA V3 / DID / Groth16 / Merkle | LOCAL_VERIFIED | 原模块重跑，不是外部 DID/生产 setup |
| Finance AppContainer / Broker | LOCAL_VERIFIED | 31 真实本机攻击与恢复；非六 Agent 全隔离 |
| 六 Agent OS隔离/HSM | NOT_IMPLEMENTED | 宿主 Core/其余 Agent 是信任边界 |
| Sepolia Infura只读费用与余额 | LIVE_TESTED | 11155111，.0103 ETH，实时RPC估算 |
| 第二运营商 RPC | LIVE_TESTED | Pocket Network共同区块/交易/回执/代码/调用核对；本地配置已完成 |
| 真实合约部署 / Merkle commit / 独立Anchor验收 | BLOCKED_EXTERNAL | 缺本机 signer 与逐笔 exact-plan 批准；没有广播 |
