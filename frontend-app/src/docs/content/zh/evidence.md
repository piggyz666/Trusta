# 证据包离线验证

TRUSTA 的每张结果卡都可以导出一个**签名证据包**。拿到证据包的人不需要相信这台服务器，也不需要连接银行或大模型，就能独立验证这一次处理过程中 Agent 之间的交接是否真实、完整、按顺序发生。

## 导出

1. 在网页“设置 → 外观”里打开开发者模式。
2. 在一张结果卡上打开“查看决策过程”，点击**下载证据包**，得到 `trusta-evidence-xxxxxxxx.json`。

## 验证

```bash
python -m scripts.verify_handoff_evidence trusta-evidence-xxxxxxxx.json --trusted-registry registry.json
```

成功时输出：

```json
{"verified": true, "scope": "RUNTIME_CAUSAL_ATTESTATION_NOT_BANK_AUTHORIZATION"}
```

退出码为 0；验证失败时 `verified` 为 `false`，退出码为 1。改动证据包里任何一个字节都会验证失败。

仓库里带有一份示例，可以直接试：

```bash
python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json
```

## 验证了什么

- 每条交接证据的签名，以及签名者在注册表中的身份和密钥。
- 证据之间的因果父子关系：拒绝未知或缺失的父证据、重复或重排、跨任务混入。
- 身份、能力承诺、密钥归属没有被替换。
- 审计记录的 Merkle 包含证明。

## 没有验证什么

- `scope` 写得很清楚：这是**运行时因果声明**，不是银行授权。资金操作的授权来自你在确认卡上的决定。
- 隐藏的负载内容不会导出；输入输出哈希一致不等于内容语义正确。

## 公钥注册表从哪来

`--trusted-registry` 是各 Agent 公钥和能力的注册表。验证者应当**独立取得并固定**这份注册表（例如事先从可信渠道拿到并保存），而不是从证据包本身读取。历史快照只说明当时的状态，不表示密钥现在仍未被吊销。
