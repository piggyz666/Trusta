# Phase 6 Gap Matrix（编码前）

| 模块 | 基线状态 | 需要交付 | 外部验证条件 |
|---|---|---|---|
| Canonical SDK | NOT_IMPLEMENTED | 规范能力、Auth/Request/Response/Error/Manifest/Webhook/Idempotency/Verification；Core兼容桥 | 本地合同及实际HTTP协议测试 |
| Reference bank | NOT_IMPLEMENTED | Starling Public Sandbox Adapter、签名、状态查询、manifest、安全模式 | BANK_ACCESS_TOKEN；写还需签名钥及沙箱来源/收款人配置 |
| 银行外部验证 | BLOCKED_BY_EXTERNAL_CREDENTIAL | 只读探测及授权小额付款脚本、真实分级报告 | 不注册/伪造用户身份，不输出Secret |
| LLM | PARTIAL | ≥100人工案例、Provider原输出/系统结果、真实双Provider尝试 | 环境有OPENAI_API_KEY/GEMINI_API_KEY，实际有效性需探测 |
| Router | PARTIAL | live调用+故障注入明确区分，private不得fallback至未批准public | 不捏造真实429/超时事件 |
| External Anchor | NOT_IMPLEMENTED | 合约+RPC submit/inspect/read-back/Proof+worker | RPC/wallet/gas/deployed contract缺失，外部提交BLOCKED_BY_EXTERNAL_CREDENTIAL |
| AA | PARTIAL | 三分摊规则、四领域、完整状态机、参与者独立付款和真实收款、恢复 | 先本地多客户沙箱，不假称外部收款 |
| TaskGraph | NOT_IMPLEMENTED | 闭合IR/validator/compiler/Blackboard运行/≥50案例 | 金融动作必须走既有链 |
| Regression | IMPLEMENTED | 原371完整保留+增量+三安全与业务基准重新测 | 以实际退出码/日志/银行指纹为证据 |

具体审查证据、安全设计和实施顺序见 phase6-architecture-review.md。最终状态在 phase6-validation.json 和 phase6-final-report.md更新，本文件保留编码前基线。

2026-10-08 后续更新：Revolut官方Sandbox已完成只读业务及一笔逐笔授权的0.01 EUR付款、官方模拟结算、独立读回与原Core补充回执闭环。Starling和本表编码前基线保持原记录；当前银行真实性见[最新验证](revolut-banking-validation.json)，边界见[接入说明](revolut-banking-integration.md)。生产清算、Sepolia外部证明、DID/ZKP等不能由该Sandbox结果推定完成。
