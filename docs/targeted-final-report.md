# TRUSTA V1 定向后端交付报告（2026-10-09）

本轮只修改 LLM 提案层和新增独立 Anchor 验证入口。最终完整回归 **667 项通过、0 失败、0 跳过**，耗时 334.1 秒；原 640 项断言和测试文件保留，新增 27 项。原金融与可信通信模块哈希未变。

## 状态

| 项目 | 状态 | 验收边界 |
|---|---|---|
| schema、工具语义、槽位续接、有界重规划 | IMPLEMENTED / LOCAL_VERIFIED | 原 Validator/Compiler、权限与执行链保持 |
| DeepSeek/Gemini 真实 API 配对与 Final | REAL_EXTERNAL_VERIFIED | 指真实模型 API；不等于模型目标全部达标或银行/链验证 |
| Banking Task / Banking Security / Phase4、5 Security | LOCAL_VERIFIED | 合成银行和受控攻击，不是生产金融审计 |
| Sepolia 真广播与独立验收 | BLOCKED_BY_EXTERNAL_CREDENTIAL | external_blockchain_verified=false |
| 生产银行付款、UI、新 Agent | NOT_TESTED | 不属于本轮范围 |

## 模型结果

| 阶段 | 模型 | 合法任务 | 缺字段澄清 | 攻击/不支持明确拒绝 | 未授权写 |
|---|---|---:|---:|---:|---:|
| Locked pre | deepseek | 27/36 | 11/12 | 12/12 | 0 |
| Locked pre | gemini | 21/36 | 3/12 | 12/12 | 0 |
| Locked post | deepseek | 35/36 | 12/12 | 12/12 | 0 |
| Locked post | gemini | 26/36 | 12/12 | 12/12 | 0 |
| Final（一次） | deepseek | 70/72 | 24/24 | 24/24 | 0 |
| Final（一次） | gemini | 49/72 | 22/24 | 18/24 | 0 |

- deepseek 的 ≥90% 可用性目标：达到。
- gemini 的 ≥90% 可用性目标：**FAILED，未达到；结果保留。**

等待用户确认没有计为资金完成。安全拒绝不计为合法业务完成。原始模型、验证接纳和端到端结果分别保留；原始输出不会用修复后的结果替代。

## 测试与安全证据

- Banking Task：34/34；其中期望完成任务 23/23。
- Banking Security：45/45 已定义攻击拒绝，未授权执行 0/45。
- Phase 4/5 Security：各 18 类、36 次攻击全部检测；有效消息和 Merkle 证明无误拒。
- Final 安全 Gate：两模型均24/24 BLOCKED且不安装图；真正越权/泄露意图均18/18明确拒绝。Gemini的6条不支持保险/贷款请求错误澄清，严格合并指标仍为18/24，保留为失败。
- 新测试覆盖：函数参数绑定、隐私投影、默认不重试、共享两次调用上限、权限注入与字面资金拒绝、槽位所有权/版本/篡改/防重复、独立 Anchor 签名/事件/链/回执/read-back/重组/幂等原测试。

首次640基线有1个 Windows WinError10055，未改代码单独复测6/6通过；第一次667前的666项全量有1个新测试数据生成错误（随机字符替换偶尔相同），已修正并保留原失败。最终667项全量重新运行通过。没有删除或放宽原测试。

## 修改与使用

原五个修改文件均在 backend/llm 下；新增 ProposalDialogue 和只读 independent_anchor。完整清单、前后哈希和原测试不变证据见 targeted-verification-manifest.json。部署可选 compact_v2 与 graph_repair_attempts=1；默认重试仍为0。模型与 endpoint 继续走 Provider 配置。

## 外部链阻塞

缺 Sepolia RPC、受控钱包、合约地址及独立复核 RPC；外部 Worker 未启用。余额和网络尚未核实，没有消耗测试 ETH。新增脚本与11项公开证据攻击测试已通过本地验收，不能提升外部真实性。配置条件补齐后仍需对具体部署/commit 另行批准，才可广播。

## 保留限制与下一步

- Gemini 的合法多目标/顺序规划仍是主要缺口；原始输出、失败节点、传输异常与 token 用量可逐条复查。Final 已封存，后续改进应另建数据版本，不再复测覆盖这一份。
- 本轮验证的是预先输入显式金融槽位后的自然语言任务规划，不宣称任意口语金额抽取已解决；多轮续接在后端 SDK 与离线回归验证，尚无真实多轮 Provider Benchmark 或 UI。
- Final 为同分布、同作者、一次输出盲测；母版语义重复，不能外推新任务泛化。金融任务每例仅一个提案，多笔串行安全依靠原运行时测试。
- RPC 真实性依赖可信节点；两个不同 URL 不自动等于不同运营商。公钥、合约和发布者须由操作员预先信任。
- 本轮累计 747 次模型请求，保守费用估计 $1.065693，均在 800 次/$5 限额内；实际账单未查询。

## 交付索引

- targeted-optimization-analysis.md：架构/根因/验收。
- llm-failure-root-cause.md、llm-optimization-changes.md：源码证据与方案。
- llm-development-results.json、llm-locked-evaluation.json、llm-final-blind-holdout.json、llm-final-comparison.md：完整评测与失败。
- external-anchor-validation.json、external-anchor-blockers.md：本轮外部状态；未生成新的成功 external-anchor-proof.json。
- targeted-security-regression.json、targeted-full-tests.txt、targeted-verification-manifest.json：安全回归、实际日志与哈希。
