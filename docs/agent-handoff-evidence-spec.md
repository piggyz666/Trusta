# Handoff Evidence V1

这是附加于 Trusted Message V3 和原 Merkle Audit 的 Runtime 因果声明，不改变 V3 字段或 BankActionIR。`agent_handoff_evidence` 表记录不可更新/删除的 JSON，和结果接收在同一 SQLite 事务提交。

每条签名声明包含 schema_version、evidence_id/message_id、task/step/revision、完整 V3 envelope、输入 hash（进程任务）、输出 payload hash、实际父证据 hash、编译依赖、Blackboard steps hash、金融 action_records 各阶段 hash、进程安全信息及 Supervisor transport_request。使用既有 audit signer、独立域 `TRUSTA-HANDOFF-EVIDENCE-V1` 签名。审计事件绑定完整声明 hash；导出携带原 records inclusion proof。隐藏 payload 不导出，输入输出 hash 不等于内容语义校验。

父证据来自 Runtime 实际先前记录中依赖节点的声明；无依赖时以先前声明维持序列。父 hash 对 `{evidence,audit_event_id}` 计算，不含后来添加的 inclusion proof。GENERAL_GRAPH 包额外包含 semantic plan 和初始化编译 steps，以及 GRAPH_INSTALLED 的 Merkle proof；离线核验编译 hash、节点主体及依赖，并拒绝未知/缺失父证据、重复或重排、跨任务、身份/capability/key owner 替换。

金融链 hash 在相应 graph action_records 已存在时填入，后续 REPORT 可以绑定完整动作/授权/合约/核验/回执。旧非图路径没有同等完整因果覆盖，不能据此声称所有旧交易已有完整协作证明。示例是一条 Finance READ → Summary → Transfer proposal → 独立确认 → 原全部金融安全阶段 → Report 的本地虚拟银行实际执行链。

```powershell
python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json
```

离线工具不连接银行、不调用模型、不签发授权。验证者须独立取得并固定 registry/capability 快照；文件 hash 是交付完整性标识，不自动证明首次下载可信。历史快照不表示当前未撤销状态，历史 Runtime 签名不等于银行签章或 Sepolia 锚定。
