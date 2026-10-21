# 后端现状与遗留台账
复核时间 2026-10-10T09:49:52.384579+00:00；Windows / Python 3.13.1。本地没有 Git 仓库，无法给出 commit/diff；使用逐文件 SHA-256 比较。原 714 项测试完整保留。

新基线：714/714、0 skip，287.836 秒。修改后：737/737、0 skip，402.688 秒；原测试文件未改。

| 等级 | 项目 | 状态 | 结论 |
|---|---|---|---|
| P0 | ANCHOR_SIGNER_APPROVAL | BLOCKED_EXTERNAL | 受控钱包签名器与逐笔交易批准均缺失；禁止自动替代。 |
| P1 | INDEPENDENT_RPC | LIVE_TESTED | 已找到 Pocket Network；共同规范区块、交易、回执、代码和 eth_call 读取通过；实际 Anchor 验收待部署。 |
| P1 | COMPLEX_NL_WORKFLOW | LOCAL_VERIFIED | 近三个月分析→权威订阅匹配→明确选择→独立取消授权→核验回执→预算完成。 |
| P1 | CONDITIONAL_PROPOSAL_FORMAT | LOCAL_VERIFIED | compact_v2 无 condition；按单次请求选用已有 full_v1，保持 Router 与 Validator。 |
| P1 | CONDITION_ID_MATCH | LOCAL_VERIFIED | 语义匹配按对应生产者映射 condition ref，不再拒绝等价模型节点名；反转条件仍拒绝。 |
| P1 | SOURCE_SELECTION_CROSS_CORE_RACE | LOCAL_VERIFIED | 双实例复现同一选择产生两个待批提案；复用数据库 GraphClaims 并先持久化消费，竞争/创建中断复测通过。 |
| P1 | LLM_NEW_GENERALIZATION | BLOCKED_EXTERNAL | 800 次旧预算耗尽；仅历史原始响应离线分析，新模型可用性和新盲测不能推定。 |
| P1 | FREE_NL_AND_AA_COMPOSITION | NOT_IMPLEMENTED | 通用 from-text 图未覆盖任意口语与 AA 混合组合；已有独立 AA API/场景仍通过。 |
| P1 | BLIND_INDEPENDENT_GOLD | BLOCKED_EXTERNAL | 新作者合成集已冻结，16 项 holdout 未运行；真正独立作者盲测仍需独立人员提供。 |

架构评估：不需要改变冻结架构。所有新能力只增加非 Agent 规范化/对话协调层。未发现需要迁移六 Agent、消息密码学、银行 SDK 或金融执行边界的新缺陷。原报告的“第二 RPC 未找到”已解决；不能继续把它列为操作员注册要求。签名器和交易批准仍实际缺失。

局部策略：先冻结源代码与基线，审查真实响应和输入来源，完成安全组合与兼容性修复，再运行专项攻击、旧基准、六场景、AA、故障恢复及全量回归。不能修复的外部权限明确保留。
