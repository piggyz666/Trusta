# 后端修改与架构说明
审查日期：2026-10-09T19:44:08.228566+00:00。Windows / Python 3.13.1。证据状态与冻结策略分别记录。

数据流：User → Conversation/显式 Intent Normalization → LLM Gateway/Router → Supervisor/Planner 语义候选 → 来源及完整请求语义核对 → Blackboard → 原确定性 Compiler/Runtime → BankActionIR → Capability → Policy → Authorization → Execution Contract → Tool Gateway → Adapter → HardVerifier → Receipt/Audit。LLM 没有银行句柄、签名密钥或审批权限。

新增非 Agent 的 grounded_request 层：分句编号绑定独立 input refs；中文金额复用既有确定性转换；参数值保持本地；模型不能互换两笔金额、漏任务或去掉“先后”依赖。图安装核对完整语义后才发生。支持显式顺序组合，不将有限语法称为开放式银行自然语言理解。原节点数量 32、深度 20、注册 capability、金融串行规则保持不变。

通用图取消停止调度并撤销待审批动作，保留已经完成的金融回执，拒绝在不确定执行阶段强制取消。安装在原锁内再次检查 revision；并行 READ 在无外层递归锁时运行，避免持锁等待子线程。封印式 ProposalDialogue 暴露为 HTTP，补槽产生新提案，不携带旧 consent。

GLM 使用原 OpenAI-compatible Adapter；可选 thinking_mode 不影响默认请求，不把 reasoning_content 当可执行候选。生产模型仍由配置与 Router 决定，测试脚本的固定模型只是本轮对照对象。外部真实客户数据未被授权出站，public shadow 模板会被 restricted 请求拒绝；评测专用配置只处理合成数据。

新增目录/文件：backend/llm/grounded_request.py；config/llm.glm.public-example.toml；benchmarks/final_live_evaluation.py；scripts/anchor_operator.py、final_local_acceptance.py、amend_final_llm_scores.py、final_backend_delivery.py；tests/test_final_convergence.py；docs/final-backend/ 全部交付及原始证据。

修改原文件：backend\server.py, backend\service.py, backend\llm\compact_graph.py, backend\llm\configuration.py, backend\llm\prompts.py, backend\runtime\graph_runtime.py, backend\llm\providers\openai_compatible.py, scripts\phase4_demo.py, scripts\phase5_demo.py, scripts\phase6_demo.py。逐文件 SHA-256 与新增清单见 source-changes.json / source-after.json。docs/architecture.md 仅更正状态入口。原 693 项断言保留；新增 21 项，最终 714 项全通过。金融核心和合约哈希未变化。

验证：基线 → 新测试失败定位及修复 → 真实双模型与只读银行/RPC → 三身份 AA 和六场景 HTTP → 受控安全攻击及可靠性 → 最新全量回归。失败尝试日志保留；不删除历史失败、不把本机测试说成外部验收。无性能优化收益宣称，测量只描述当前能力。
