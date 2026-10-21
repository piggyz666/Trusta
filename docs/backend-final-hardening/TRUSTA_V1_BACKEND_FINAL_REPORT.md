# TRUSTA V1 后端最终加固交付
交付 2026-10-10T09:49:52.384579+00:00。状态：本轮可自主实施的代码与本机验收完成；真实部署/commit 和新实时模型评测仍 BLOCKED_EXTERNAL，不能声称全部外部闭环完成。没有真实银行操作、真实钱包签名或链上广播。

## 已完成
714/714 新基线；修改后 737/737，0 skip、50 modules、402.688 秒；原 714 测试源码保持不变。新增 23 项缺陷/攻击/恢复回归，变更面 60 项通过。六 Agent、金融核心、Trusted V3、DID/Groth16、Merkle、SDK、Solidity 本轮哈希不变；精确修改见 source-changes.json。保留首次 735 项中间回归及跨 Service 竞争缺陷失败复现；修复后重新完成全量 737 项与真实业务 Demo。

补齐三月分析、显式选择条件、完整提案协议兼容、等价 condition refs、多轮用户选择和银行权威对象绑定。长句订阅→预算 Demo 实际完成取消及验证回执，取消前要独立授权；新 API 可供后续 UI 使用。数据流仍为规范化/对话 → Gateway/Router（需要模型时）→ 已注册 Supervisor/Planner → Blackboard → 确定性编译/安全执行 → 银行适配器 → HardVerifier → Receipt/Merkle Audit，LLM 无 Bank API 路径。

独立 Pocket Network RPC 已通过真实只读核对，并接入 .env。Infura、钱包、源码与费用保护未改。部署 estimateGas 3,802,284；首次 commit estimateGas 356,357（只读新合约 state override）；余额 0.0103 ETH；本次两笔 margin 报价上界 4.9919669184E-7 ETH。资金够当前报价及既有硬保护，签名器/逐笔许可仍缺，报价 TTL 120 秒不得直接执行过期计划。

## 当前验收数据
- Banking Task 34/34，实际本地任务完成 23/23；Security 45/45，未经授权执行 0/45。
- Agent 真实 Windows 隔离攻击/恢复 31/31、0 skip；8 clients/80 HTTP 全部通过，1000 审计事件与链验证成功。
- 六场景 HTTP 6/6，11 proofs；定时重启恢复及重复写入 0；AA 三笔独立审批/收款/回执验证。
- 新本地语言样本原始预设符合 21/24（合法目标 16/19、正确澄清 5/5），不是模型准确率。HN11 预设忽略产品申购下限，实际 Policy 正确拦截；保留原得分。AA 通用图组合与自由口语的两项真实缺口保留。新 prepared holdout 16 项，运行 0。
- 46 个历史真实模型响应在当前代码离线重放，原始内容/usage/费用与分母保留；本轮远端模型请求 0、新费用 0。DeepSeek 旧6/6和GLM旧0/6不替代新泛化证据。
- 新 workflow-handoff / signed checkpoint / receipt Merkle proof 离线验证及篡改拒绝通过；对应业务 root 未上链。

## 外部证据与边界
官方银行已验证证据保留，不重复账户读取与付款。它是官方 Sandbox 返回/历史模拟结算，不是生产真实清算或 bank-signed attestation。独立 RPC 的样本交易/代码不是 TRUSTA 部署，实际合约 code/Publisher/commit event/root 和链确认必须待真实交易后核验。

## 文件与复现入口
本目录含用户指定14份文档/数据及真实原始日志、源码哈希、冻结新数据、失败分析、银行/Agent/HTTP/AA/Graph证据。阅读 gap-analysis → root-cause-and-fixes → llm-provider-evaluation/taskgraph-generalization → trusted-agent-security/banking-capability-validation → reliability-results → final-capability-matrix。full-tests.txt 是本轮实际全量日志，baseline-tests.txt 是本轮实际旧基线。

本轮脚本：benchmarks.hardening_natural_language（一次冻结本地评估，重复拒绝）；scripts.hardening_llm_offline（历史保存响应离线重放）；scripts.hardening_workflow_demo；scripts.hardening_local_acceptance；scripts.hardening_rpc_probe（只读探测及验证后的本地第二RPC配置）；旧 sepolia_deployment_readiness 继续只读准备。上述回归与 Demo 使用临时 Virtual Bank，不碰真实资金。常规测试仍使用 unittest discovery；本机全量采用2worker、按模块独立进程避免Windows socket耗尽。

## 仅需集中操作员完成
见 operator-actions.md：本人本机受控导入已有测试钱包签名器；重新生成并明确批准具体部署计划，再单独批准真实业务 root Commit。第二 RPC 已完成，无需新增注册。新的模型评测需新请求/费用预算与适当独立数据。审批缺失源自既有 exact-plan 钱包边界，本报告不能替代批准；私钥/密码不发聊天。

后续优先在权限到位后完成两笔链交易与独立 read-back，再用新预算测试合适 GLM/DeepSeek 的自由语言和新holdout；保留 backend current API/安全边界供 UI 产品设计。生产资金与全部 Agent OS/HSM隔离没有被本轮测试证明，不作为比赛版完成声明。
