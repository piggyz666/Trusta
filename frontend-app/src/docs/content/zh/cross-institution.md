# 跨机构协作：WeID · 零知识准入 · 链上承诺

当多家机构的 Agent 需要共同完成一件事（例如几家银行的 Agent 交叉核对同一份风险结论），只靠本机签名还不够：对方怎么确认“你是谁”“你够不够格参与”“最后的结论有没有被改过”？TRUSTA 在[多 Agent 签名通信](#/agent-communication)的基础上叠加了一层可以在联盟链上公开核验的信任层。

![图 2 跨机构协作：身份、资格、结论都能在链上公开核验](/figs/fig2.webp)

## 四个部分

### 链上身份：WeID

每个参与协作的 Agent 在 **FISCO BCOS** 联盟链上登记 **WeID**（微众银行开源的分布式身份），其 P-256 消息密钥与 WeID 绑定。身份可以注册、解析、停用和恢复。协作轮次只认链上 WeID；链不可用时直接拒绝，不会退回本地自签的 `did:key`。

### M1：零知识能力准入

Agent 用 **Groth16** 零知识证明说明：自己在固定评测集上的成绩（由 TP / FP / FN 按固定公式算出的 F1）达到策略阈值，同时**不公开评测明细和预测结果**。证明绑定策略、周期（Epoch）、模型承诺和评测记录；可以撤销，会到期。Agent 不能自己声明分数获得准入。

### M2：协作消息绑定

在原有 V3 签名之外，协作消息还要绑定链上身份、准入凭证、协作轮次和 Epoch。错误签名、跨轮次 / 任务 / Epoch、已撤销、模型不符、重放都会被拒绝。

### OTC：全窗口收集

协作采用 k-of-n：到截止时间才冻结证据，收到的有效结论不足 k 份则本轮作废；迟到的消息只用于遥测，不进入结论。

### M3：链上决策承诺

协作结论的 **Merkle 根**与成员快照写入 `CollaborationAnchor` 合约，并由两个节点的 RPC 分别读回核对。提交结果未知时按原哈希查找恢复，绝不盲目重发。

## 重要边界

- 链上身份和签名都**不授予任何银行写入权限**；资金操作仍须用户在确认卡上授权。
- 当前环境是**单机 8 节点测试联盟链**（Chain ID 101），两个验证 RPC 属于两个节点但在同一主机，不代表独立机构。
- Groth16 使用**开发用可信设置**（单机 Phase-2），不是生产级 MPC。
- 零知识电路证明的是受限的统计关系，不在电路里执行大模型推理。

这些限制都如实记录在仓库的 `docs/ANCHOR_Integration_Final_Acceptance.md` 中。

## 已登记的 Agent

| Agent | WeID |
|---|---|
| 财务 Finance | `did:weid:101:0xa968103852c7eae1a245b77e98660730d8ccc3d3` |
| 理财 Wealth | `did:weid:101:0xa8fac68a926fb16debb085af40a1bb5d840df7de` |
| 交易 Transaction | `did:weid:101:0xf9fb420c820cf567b5250f816dd9a21c80f88c31` |

## 开启方式

这一层默认关闭，需要先在 WSL / Linux 中运行测试联盟链：

```bash
bash scripts/anchor_network_lifecycle.sh run
```

相关配置项：`TRUSTA_DID_ENABLED`、`TRUSTA_ZKP_ENABLED`、`TRUSTA_ZKP_ARTIFACT_DIR`、`TRUSTA_ZKP_MANIFEST_SHA256`，见[配置项](#/configuration)。第三方可以用 `python -m scripts.verify_anchor_collaboration` 复验公开证据包。
