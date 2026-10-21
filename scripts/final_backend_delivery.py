"""Evidence-derived final documents; no model, bank or chain calls."""
from pathlib import Path
import json,hashlib,os,platform
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/final-backend'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def write(name,text):(OUT/name).write_text(text.strip()+'\n',encoding='utf-8')
def main():
    from scripts.estimate_sepolia_costs import load_local_environment
    load_local_environment()
    baseline=json.loads((ROOT/'data/final-backend-baseline-result.json').read_text(encoding='utf-8'))
    final=json.loads((ROOT/'data/final-backend-frozen-result.json').read_text(encoding='utf-8'))
    if not final['passed'] or final['skipped']:raise SystemExit('Do not publish an accepted test result.')
    llm=read('LLM_Score_Amendment.json');live=read('LLM_Live_Benchmark.json');probe=read('LLM_Postfix_Dev_Probe.json')
    sep=read('sepolia-live-readiness-final.json');bank=read('bank-live-read.json');pay=read('bank-payment-readback.json')
    aa=read('aa-demo.json');six=read('six-scenes-demo.json');perf=read('backend-reliability-benchmark.json')
    security=read('banking-security-benchmark.json');communication=read('agent-communication-security-benchmark.json')
    before=read('source-before.json');after={}
    for directory in ('backend','scripts','benchmarks','tests','contracts'):
        for p in (ROOT/directory).rglob('*'):
            if p.is_file() and p.suffix in {'.py','.sol'}:after[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    changed=[p for p in before if before[p]!=after.get(p)];added=[p for p in after if p not in before]
    if any(p.startswith('tests') for p in changed):raise SystemExit('Original tests changed.')
    (OUT/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    (OUT/'source-changes.json').write_text(json.dumps({'modified':changed,'added':added,'original_tests_modified':[],'git_repository':False},indent=2),encoding='utf-8')
    usd=probe['budget']['cost_usd_upper_estimate'];request_count=probe['budget']['requests']
    header=f'审查日期：{datetime.now(timezone.utc).isoformat()}。Windows / Python {platform.python_version()}。证据状态与冻结策略分别记录。'
    write('TRUSTA_Backend_Gap_Audit.md',f'''# TRUSTA 后端缺口审查
{header}

未找到同名 Engineering Spec，已按实际 architecture、phase4/5/6、backend-hardening、targeted、Sepolia 文档及源码交叉检查。工作目录没有 Git 元数据，因此交付源文件前后 SHA-256 清单，不伪造 Git diff/commit。原始 {baseline['tests']} 项测试重新通过，0 跳过，{baseline['duration_seconds']} 秒；不是复述历史 693。

| ID / Module | Current / Evidence | Root Cause | Severity | Fix / Acceptance | Final Status / Change Policy |
|---|---|---|---|---|---|
| G01 自然语言组合 | 旧 graph API 需要先填槽位；grounded_request、真实评测 B_GRAPH | 缺少非 Agent 的分句、参数来源、候选语义核对入口 | P1 | 新入口；六条 DeepSeek 真实任务通过，原编译器未放宽 | LIVE_TESTED / 新层 |
| G02 DAG 取消 | service.cancel_draft 原先将 GENERAL_GRAPH 送入旧业务授权路径 | 任务类型分派不完整 | P1 | 专用取消、claim/revision 检查、保留已完成回执；21 项新增测试 | LOCAL_VERIFIED / 定向修复 |
| G03 安装竞态 | 模型返回到安装之间可改变 revision | 检查与安装不是同一临界区 | P1 | create 内检查 owner、PENDING、revision；不在外层锁内运行 READ workers | LOCAL_VERIFIED |
| G04 双模型 | 46 次真实调用及完整原始 JSON | GLM 免费模型格式、语义质量弱；4.7 探测截断/429 | P1 | 可选 thinking 配置、真实 GLM 模板；未将弱模型投入可靠规划 | LIVE_TESTED 接通 / FAILED 质量 |
| G05 金额提取 | dev FC01 包含“元”而被拒绝 | 提示词未明确字段单位边界 | P1 | 补充精确格式；DS 开发样本真实复测通过；不重跑 Holdout | LIVE_TESTED 小样本 / 泛化未验证 |
| G06 DAG 多轮补槽 | ProposalDialogue 原有 SDK 已实现 | 缺 HTTP 暴露 | P2 | dialogue/continue 复用封印、身份、版本和单次消费；无授权继承 | LOCAL_VERIFIED |
| G07 第二 RPC | PublicNode、dRPC、BlockPI、1RPC 探测记录 | 当前环境返回 403/不可用；非同运营商验收未通过 | P1 | 主链检查完成；集中操作员清单，未将不同 URL 当独立运营商 | BLOCKED_EXTERNAL |
| G08 Sepolia 部署与 commit | estimateGas、代码哈希、硬保护已实现 | 缺受控本地签名器与具体 exact-plan 批准；未部署 | P1 | 仅新增本机加密 keystore 登记工具；真实签名/广播均 0 | BLOCKED_EXTERNAL / 既有安全模块 FROZEN |
| G09 银行转账历史状态 | 本轮 14/14 远端读回检查，历史 0.01 EUR 为 SETTLED | 早期 403 与待结算记录已被后续授权试验解决 | P2 | 不重复付款；区分官方沙箱模拟结算与生产结算 | LIVE_TESTED / FROZEN |
| G10 AA | 三身份 HTTP、实际虚拟账本扣增、三个回执及证明 | 外部银行 manifest 无 AA 能力，不能夸大 | P1 | 本地闭环重新验证；外部 AA 显式阻塞 | LOCAL_VERIFIED 本地 / BLOCKED_EXTERNAL 外部 |
| G11 运行和证据 | 31 攻击/恢复、80 并发 HTTP、1000 审计事件 | 无新增 Core 可靠性缺陷；仅两个 Demo 直接调用 import 失败 | P2 | 修正 Demo 根路径初始化，保留历史失败日志 | LOCAL_VERIFIED |
| G12 文档 | 旧 architecture 写 DID/ZKP 未实现 | 历史状态未同步 | P2 | 指向当前能力矩阵；did:key/Groth16 真实本地验证与链上状态分开 | LOCAL_VERIFIED |
| G13 评测记分 | 控制意图空槽位、flattened clarification | 评分器 KeyError/取错 reason 层级；不是 Provider 故障 | P2 | 单独保存评分修订，原始输出/锁定集不变、无新增调用 | LOCAL_VERIFIED |
| G14 泛化自然语言条件/自由重规划 | Core 注册 DAG 支持分支/等待；NL 新层为有限显式语法 | 业务条件需要可靠事实来源和语义设计；不能靠模型猜 | P1 | 现阶段拒绝含糊条件；列为后续能力，不宣称任意自然语言理解完成 | NOT_IMPLEMENTED 该入口扩展 |

无必须改变冻结架构的冲突。金融执行、六 Agent、ECDSA/DID/ZKP、Merkle、Solidity、银行 Adapter 没有无故重写；原测试未删除或修改。没有 P0 被隐藏为成功。GLM 质量与链上独立验收仍是发布限制。
''')
    write('TRUSTA_Backend_Optimization_Report.md',f'''# 后端修改与架构说明
{header}

数据流：User → Conversation/显式 Intent Normalization → LLM Gateway/Router → Supervisor/Planner 语义候选 → 来源及完整请求语义核对 → Blackboard → 原确定性 Compiler/Runtime → BankActionIR → Capability → Policy → Authorization → Execution Contract → Tool Gateway → Adapter → HardVerifier → Receipt/Audit。LLM 没有银行句柄、签名密钥或审批权限。

新增非 Agent 的 grounded_request 层：分句编号绑定独立 input refs；中文金额复用既有确定性转换；参数值保持本地；模型不能互换两笔金额、漏任务或去掉“先后”依赖。图安装核对完整语义后才发生。支持显式顺序组合，不将有限语法称为开放式银行自然语言理解。原节点数量 32、深度 20、注册 capability、金融串行规则保持不变。

通用图取消停止调度并撤销待审批动作，保留已经完成的金融回执，拒绝在不确定执行阶段强制取消。安装在原锁内再次检查 revision；并行 READ 在无外层递归锁时运行，避免持锁等待子线程。封印式 ProposalDialogue 暴露为 HTTP，补槽产生新提案，不携带旧 consent。

GLM 使用原 OpenAI-compatible Adapter；可选 thinking_mode 不影响默认请求，不把 reasoning_content 当可执行候选。生产模型仍由配置与 Router 决定，测试脚本的固定模型只是本轮对照对象。外部真实客户数据未被授权出站，public shadow 模板会被 restricted 请求拒绝；评测专用配置只处理合成数据。

新增目录/文件：backend/llm/grounded_request.py；config/llm.glm.public-example.toml；benchmarks/final_live_evaluation.py；scripts/anchor_operator.py、final_local_acceptance.py、amend_final_llm_scores.py、final_backend_delivery.py；tests/test_final_convergence.py；docs/final-backend/ 全部交付及原始证据。

修改原文件：{', '.join(changed)}。逐文件 SHA-256 与新增清单见 source-changes.json / source-after.json。docs/architecture.md 仅更正状态入口。原 {baseline['tests']} 项断言保留；新增 21 项，最终 {final['tests']} 项全通过。金融核心和合约哈希未变化。

验证：基线 → 新测试失败定位及修复 → 真实双模型与只读银行/RPC → 三身份 AA 和六场景 HTTP → 受控安全攻击及可靠性 → 最新全量回归。失败尝试日志保留；不删除历史失败、不把本机测试说成外部验收。无性能优化收益宣称，测量只描述当前能力。
''')
    write('TRUSTA_Final_Capability_Matrix.md','''# 最终能力矩阵
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
''')
    model_rows=[]
    for p in ('deepseek','glm'):
        for mode,v in llm['summary'][p].items():model_rows.append(f"| {p} | {mode} | {v['count']} | {v['raw_correct']} | {v['validator_accepted']} | {v['e2e_expected_outcome']} |")
    write('GLM_DeepSeek_Live_Evaluation.md',f'''# DeepSeek / GLM 真实对照
{header}

真实 endpoint 为 DeepSeek OpenAI-compatible 与智谱官方 OpenAI-compatible。测试模型 deepseek-flash 与 glm-4-flash-250414，返回模型/usage/输出均逐请求保存。GLM-4.7-Flash 早期一次截断、随后探测不可用，原始诊断 HTTP 429/1305；不能据此断言不支持 JSON。实际可用 free 4-flash 质量不等于 4.7 或其他 GLM 型号质量。

新数据 3 dev / 8 locked / 12 holdout，各模型 23 次；全部数据在任一模型评测前哈希锁定，Holdout 只运行一次、未复用旧 Final Gold。该 Holdout 由本工程作者编写，不是外部独立作者的盲测，不夸大“完全未见”的统计含义。0 repair / 0 fallback。原始冻结 JSON 保留，两个评分器缺陷在 LLM_Score_Amendment.json 单独修订，不修改数据/响应/安全规则。

| 模型 | 任务入口 | 样本 | 原始语义正确 | 合法图/字段接受 | 期望 E2E 结果 |
|---|---|---:|---:|---:|---:|
{chr(10).join(model_rows)}

A_GRAPH 是预提供结构化槽位：5 合法任务 + 2 不支持/攻击拒绝；合法执行成功只到 COMPLETED 只读或 WAITING_USER 提案。B_INTENT 是直接自然语言意图/槽位与对话：包含否定、条件、未知收款人、缺字段与一个多轮样本；E2E 一栏包含正确澄清，不等于付款完成。B_GRAPH 是从直接 NL 分句与确定性参数归一化，再真实模型规划、语义核对、运行；6 条是明确语法内的合法组合，不代表自由条件或无界 NL 泛化。

B_INTENT 的原始 intent/slot/clarification：DS {llm['summary']['deepseek']['B_INTENT']['intent_correct']}/10、{llm['summary']['deepseek']['B_INTENT']['slots_correct']}/10、{llm['summary']['deepseek']['B_INTENT']['clarification_correct']}/10；GLM {llm['summary']['glm']['B_INTENT']['intent_correct']}/10、{llm['summary']['glm']['B_INTENT']['slots_correct']}/10、{llm['summary']['glm']['B_INTENT']['clarification_correct']}/10。多轮样本两模型均未完成付款提案，不能宣称多轮模型成功率达标。结构合法与语义正确单独记录，GLM B_GRAPH 1 个合法图仍因语义不匹配被挡住。

对话 E2E 使用同一次真实响应经公共 Conversation 协调器本地回放；不是第二次实时 Provider 对话，也不是生产银行联调。B_GRAPH 为真实 Provider → Core 实际运行。所有样本未发出银行审批；未经授权 transfer=0，独立银行攻击基准另核查完整金融状态。

延迟：DS P50 {live['summary']['deepseek']['p50_ms']}ms / P95 {live['summary']['deepseek']['p95_ms']}ms；GLM P50 {live['summary']['glm']['p50_ms']}ms / P95 {live['summary']['glm']['p95_ms']}ms。46 次主评测 API error 0；GLM 早期 5 次探测失败另列，不从报告抹去。主评测 DS 用量保守估算 ${live['summary']['deepseek']['cost_usd_estimate']:.8f}；GLM 免费型按官方公示价格估算 $0，非账号账单验证。参考[智谱官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing)。

累计历史 747 + GLM 探测 5 + 主评测 46 + 修复后 dev 2 = {request_count} 次，保守累计 ${usd:.8f}，低于 $5，但请求次数 800 已用尽，停止进一步调用。历史 ledger 文件未改，账目上限采用既有预算保护；DS .3/.12? 实际计算是每百万 input $0.3、output $1.2 的保守预算参数，不当作最新账单单价。unknown usage 保留预留值，未当成免费。

修复仅针对 dev FC01 的 amount_text 单位后缀，补提示词实例与确切子串规则。最后两次只重测同一 dev 请求：DeepSeek 成功，GLM 字段正确但仍请求澄清。这不是新的 Holdout 结果，未提高上表成绩。GLM 质量门仍 FAILED；下一轮需新的独立数据和获准预算/更适合模型，不能放宽 Validator 得高分。

模型 Router 仍按目的、复杂度、数据敏感度、locality、审核标志、安全级别、延迟和费用约束选择。演示模板是 public shadow，真实客户 restricted 数据默认不会发给未审核外部供应商。本轮显式 benchmark 配置仅允许作者合成数据，未修改生产出站规则。
'''.replace('DS .3/.12? 实际计算是','DS 实际计算是'))
    tx=sep['unsigned_deployment_plan'];g=tx['estimated_gas'];c=sep['commit_estimate_gas'];fee=tx['transaction']['maxFeePerGas'];tip=tx['transaction']['maxPriorityFeePerGas'];combined=sep['combined_quote_worst_cost_wei']/1e18
    nominal=(sep['policy']['deploy_max_cost']+sep['policy']['commit_max_cost'])/1e18
    effective=(sep['policy']['deploy_max_cost']+min(sep['policy']['commit_max_cost'],sep['policy']['commit_max_gas']*sep['policy']['max_fee_per_gas']))/1e18
    budget_rows=[]
    for balance in (.0005,.001,.005,.0103):
        budget_rows.append(f"| {balance} | {'足够' if balance>=combined+.0002 else '不足'} | {'足够' if balance>=effective+.0002 else '不足'} |")
    write('Sepolia_Deployment_and_Verification.md',f'''# Sepolia 部署准备与外部验证
状态：主 RPC LIVE_TESTED；真实部署、真实 commit、独立链验收 BLOCKED_EXTERNAL。external_blockchain_verified=false。没有 External_Anchor_Proof.json，避免把本地证明冒充链验收。

RPC 观测：{sep['read_only_estimation']['timestamp_utc']}，Ethereum Sepolia 11155111。公开部署钱包 / constructor Publisher：`{sep['wallet_public_address']}`；余额 0.0103 SepoliaETH，pending nonce={tx['transaction']['nonce']}。该地址只是本轮报价绑定，不代表持有已获准真实签名器。

创建字节码 SHA-256：`{sep['artifact_creation_sha256']}`；Solidity source SHA-256：`{sep['source_sha256']}`；钱包 immutable Publisher 替换后预期 runtime Keccak：`{sep['expected_runtime_keccak256']}`。尚未部署，链上代码/publisher 未验证。合约与冻结保护未改。

实测 RPC estimateGas：部署 {g:,}，首次 commit {c:,}；20% 余量 gasLimit {tx['transaction']['gas']:,} / {sep['commit_gas_with_margin']:,}。commit 用 Sepolia fresh-contract state override，只读估算，非已经部署的合约执行消耗；不同实际 root 字节分布及未来链状态仍须重新 estimate。未拿 5,000,000 / 600,000 上限冒充实际消耗。

执行保护报价：priority={tip/1e9:.9f} Gwei，maxFee={fee/1e9:.9f} Gwei；结合当前 baseFee、RPC tip 及原安全策略，不追逐 feeHistory 高报价。read_only_estimation 的信息性建议价不是获准签名策略，真实受控报价以 unsigned_deployment_plan 为准。当前无余量估计最低总费约 {(g+c)*(tip+14)/1e18:.12f} ETH；有余量的本次报价最坏上界 {combined:.12f} ETH；余额预计至少 {sep['balance_remaining_after_quote_worst_wei']/1e18:.12f} ETH。

| 钱包余额 ETH | 当前报价含 0.0002 余额底线 | 硬保护有效最高总费用含余额底线 |
|---:|---|---|
{chr(10).join(budget_rows)}

保护：deploy ≤5m gas、≤.0085 ETH；commit ≤600k gas、≤.0015 ETH；priority ≤.1 Gwei、maxFee ≤2 Gwei、最低余量 .0002。独立 per-transaction cost 名义和 {nominal:.4f} ETH；结合 commit gas×maxFee 的有效总费用上限 {effective:.4f} ETH，加保留余额需 .0099 ETH。当前 .0103 足够覆盖该有效最坏预算，余量 .0004；即便按更保守名义 .0100+保留 .0002，也还多 .0001。

相同余量 gas 在有效价格 .1 Gwei 时总费约 {(tx['transaction']['gas']+sep['commit_gas_with_margin'])*.1e9/1e18:.9f} ETH；1 Gwei 约 {(tx['transaction']['gas']+sep['commit_gas_with_margin'])*1e9/1e18:.9f} ETH。2 Gwei 会令当前部署超过 .0085 上限，必须拒绝而非提高预算。小余额“当前够”并非涨价情况下也够。

签名：现有受控加密 keystore 机制；新增本地隐藏输入登记工具经过临时测试钥验证，真实钱包尚未登记。没有真实签名、广播、部署或 ETH 消耗。报价 TTL 120 秒，本报告 plan 已过期不能执行；未来重新报价、逐笔 exact hash 批准，禁止自动换 nonce 或调高 fee。

第二运营商：PublicNode(Allnodes)、dRPC、BlockPI、1RPC 等只读探测未成功，保留 diagnostic 与 runner 错误及更正记录。错误 URL/403/不可用不能算独立验证；本轮没有改写 ANCHOR_VERIFY_RPC_URL 成未经核实的服务。主 Infura 正常。操作员可使用已有第二运营商的安全本机 endpoint，不用重新创建钱包或重复注册 Infura。

后续验收：第二运营商 chainId + 同高度 canonical hash 匹配；精确批准部署并核验 receipt/code/Publisher；从真实 TRUSTA Audit Log 生成受签名 checkpoint，经原 outbox/worker 准备、单独批准 commit；≥3 确认、两运营商 receipt/event/read-back/root/current状态、canonical/reorg 检查全部通过才生成外部证明。当前这些链上字段均为空，不生成替代数据。
''')
    write('Bank_Integration_Final_Report.md',f'''# 银行集成最终报告
官方 Revolut Sandbox 状态 LIVE_TESTED；生产银行资金操作 NOT_APPLICABLE。本轮无银行写入和模拟器调用，没有重复付款。

新 OAuth refresh + 真实账户/余额/交易远端读取，发现 {bank['account_count']} 个账户；GBP 账单净支出 {bank['summary']['net_spend']} minor units，排除 {bank['summary']['excluded_count']} 条非消费数据，Core Finance 与 TaskGraph 汇总一致；V3 accepted={bank['signed_messages_accepted']}，审计链与签名 checkpoint 均通过。没有把 GBP 交易套 CNY 大额阈值；类别保留 UNCLASSIFIED 而非编造商户类别。bank-live-read.json 记录请求分类及前后金融表行数。

历史获准唯一 0.01 EUR → Acme Corp. 官方沙箱付款：bank transaction `{pay['bank_transaction_id']}`；本轮 GET 读回 SETTLED，金额/币种/recipient/request_id/action_hash/reference/fee/余额/回执/对账/唯一性/审计/证明共 14/14 检查通过。Receipt `{pay['receipt_id']}`。早期 403 已在原操作员获准 READ,WRITE 模拟结算试验后解决，不再按早期 PENDING 报告。

结算是官方远端沙箱返回，且历史使用官方沙箱模拟器推进；不是生产真实清算，也没有 bank-signed attestation。单银行回执读回不是第三方独立银行证明。本轮 simulator_calls=0，business_write_calls=0。

银行抽象仍使用 Adapter Interface → Capability Manifest → Provider Connector → Trusted Gateway；Revolut 特定字段在 Connector/映射层，unsupported 能力在 HTTP 前拒绝，凭据不发给模型。SDK 的两实现及其他 manifest 测试说明扩展方式，不能宣称已接全球所有银行。外部 AA、理财买赎、卡与订阅写操作当前 manifest 不支持，标为 BLOCKED_EXTERNAL；对应虚拟银行已完成实际账本状态验证。
''')
    write('AA_Collection_E2E_Report.md',f'''# AA 收款端到端
本地状态 LOCAL_VERIFIED；外部 AA BLOCKED_EXTERNAL。不是仅做分摊计划。

输入：10000 分，alice/bob/carol；原 split largest-remainder 分配 3334/3333/3333。三套本机 HTTP 身份分别准备动作和逐笔授权，独立 authorization_id={aa['independent_authorizations']}。发起人不能代替参与者批准。

真实虚拟银行结果：参与者初始各 100000 分，终值 {aa['participant_balances']}；实际共扣 10000 分，发起人 1000000→{aa['receiver_balance']}。Collection COMPLETED。aa-demo.json 包含动作哈希、任务和独立授权 ID、ledger transfers、三个 Receipt、签名检查点的 Merkle 包含证明与公开验证钥；本机固定信任来源，非外部链证明。

原 15 项 split 测试覆盖 1 分分配、舍入、各自授权、真实收款、partial restart、拒绝、取消已付保留、过期、参与者读取隔离、重复不扣；HTTP 并发/幂等与重启测试仍在最终全量中。没有将一次批准用于不同付款。未接支持 AA 的真实银行 Connector，不能把虚拟到账当外部收款。
''')
    write('TaskGraph_Generalization_Report.md','''# TaskGraph 泛化与证据
原受约束 Compiler/Runtime 为 LOCAL_VERIFIED；明确 NL 顺序组合为 LIVE_TESTED；开放式条件 NL 仍 NOT_IMPLEMENTED。

新 POST /api/graphs/from-text 接收 text/account_id，通过非 Agent 确定性分句及中文金额/资源归一化后，给 Supervisor/Planner 的 LLM 只有目标、输入 reference 与类型/分句元数据。候选必须匹配完整操作数、Agent/capability、输入来源、结果来源和先后依赖，才能装入 Blackboard。

新增 /api/graphs/dialogue 与 /api/graphs/{id}/continue 复用原 ProposalDialogue：closed fields、实际 HTTP 身份、revision、seal、允许缺失字段白名单、补槽单次消费，新提案不继承 authorization。一般图修改采用取消未执行部分并产生新提案；已完成不可逆动作的 Receipt 保留。不是随意修改运行中金融图。

原注册图支持并行 READ/JOIN、分支、WAITING_USER、WAITING_TIME、失败传播、重启等待恢复、跨 Agent handoff、多银行动作独立审批。原 19 图测试及新 21 测试证明真实并行重叠、来源/输出/图封印、防假 capability、取消和实际两笔付款审批。18 条顺序 READ + report 的新测试说明不限两个目标，但仍遵守原 32 节点/20 深度。

phase6-taskgraph-demo.json 展示分析 → 待确认退订 → verified receipt → wealth budget，重启后批准才继续。六场景 Demo、banking-task-benchmark.json 包含复杂业务依赖。agent-communication-demo.json 与可信 registry 导出提供实际签名 handoff 证据。LLM 新六条显式多目标：DS 6/6，GLM 0/6，GLM 有合法格式却错误顺序的失败样本。

限制：NL 层拒绝条件、否定、含糊金额、未知组合；当前不能将任意人类条件准确编译到受信事实分支。年报和 AA 自身已有业务 API，但其任意 NL 混合组合不在新有限语法中。多轮图补槽是本地/HTTP 验证，真实模型多轮样本未成功，不冒充通用自然语言完成。RUNNING 异常任务默认不自动恢复资金写操作；等待/只读结果恢复有明确范围。
''')
    write('Trusted_Agent_Security_Assessment.md',f'''# 可信 Agent 与安全评估
金融安全和密码核心保持 FROZEN，证据 LOCAL_VERIFIED，非生产渗透测试。

ECDSA P-256 V3 核查主体、元数据、sender/receiver、capability commitment、task/graph/version、nonce、密钥轮换/撤销和持久化重放。合法签名之后仍需编译器依赖与 capability 检查，绝不等于动作授权。没有新增 Agent 或让 LLM 持有 bank handle。

受控本机进程隔离/攻击/恢复 {communication['tests']} 项全通过，0 skip，实际 Finance AppContainer 尝试高权限访问、凭据和 broker 越权、跨进程签名、重放/重启复用结果测试；仅覆盖 GENERAL_GRAPH Finance 读取/汇总/异常路径。其他 Agent 与可信 Core/issuer 仍宿主执行；每 Agent 密钥文件分离不等于全部 Agent 操作系统隔离或 HSM。

银行攻击基准 {len(security['cases'])}/{len(security['cases'])} 通过，完整银行状态检查未经授权变化 0。全量覆盖身份伪造、receiver/payload替换、金额/收款人/authorization hash、图和编译器篡改、审计事件/root/proof、错误链/伪 receipt、过期/重放、provider injection、未知银行交易幂等。新 NL 模型输出需语义核对；GLM 错计划被拒绝，不能以“验证器挡住”当作模型正确。

did:key 实际公钥/身份绑定并校验，是 DID_KEY_VERIFIED 本地能力；非链上 DID 注册/外部凭证生态。Groth16 采用实际 snarkjs 电路和公有输入，6 项 privacy proof 测试不跳过，检查 issuer、权限/用户/动作绑定、过期、重放、当前权威余额与风险；REAL_ZKP_VERIFIED 限本地开发 setup，不是多方 ceremony。ZKP 不能替代 Policy、Authorization 或 Contract。

Merkle 校验含签名 checkpoint、受信钥 pinning、实际业务事件包含证明与篡改拒绝；LOCAL_MERKLE_VERIFIED 不等于 EXTERNAL_BLOCKCHAIN_VERIFIED。签名 handoff 因果链证明“谁对何数据签名并依赖什么”，不证明每个银行事实都正确，也不能抵御掌控全部 Core 和 issuer 私钥的对手。

真实模型/银行/RPC实验分开存证。不存在外部链证明；无真实钱包签名/广播/生产银行转账。本轮检查 secret 实值不进入交付证据，Secret_Scan.json 只记录类别/命中数。开发密钥、数据库、keystore 被忽略；不能凭本报告承诺生产密钥管理、银行合规或整体环境安全。
''')
    write('Backend_Reliability_and_Performance.md',f'''# 运行可靠性与性能
当前本机受控结果 LOCAL_VERIFIED，非生产 SLA。

最新全量 {final['tests']}/{final['tests']}，0 skip，{final['duration_seconds']} 秒；基线 {baseline['tests']}/{baseline['tests']}，{baseline['duration_seconds']} 秒。模块隔离进程、2 worker 保持同一断言和 Windows 资源约束；未为提分移除测试。

现有硬化 benchmark 再执行：8 次含冷启动 Finance 进程图，P50 {perf['process_graph']['p50_ms']:.2f} ms；8 HTTP clients / 80 次身份隔离请求，80/80，P50 {perf['http_latency']['p50_ms']:.2f} ms / P95 {perf['http_latency']['p95_ms']:.2f} ms。SQLite 写锁真实等待后成功，审计链一致；1000 次 append + checkpoint 耗时 {perf['audit_append_and_checkpoint']['duration_ms']:.2f} ms；20 次 Receipt Proof 查询验证 P50 {perf['receipt_proof_query_verify']['p50_ms']:.2f} ms。原始样本保留，测试并行运行会带来资源竞争，不声称吞吐已优化。

31 项进程攻击/恢复：持久化 read delivery、结果落库再 ACK、限定只读重试；不自动重试金融写、不给 claim TTL 自动接管。Anchor outbox 将已知 tx/raw hash 在广播前持久化；真实不确定交易只查原 tx，不自动换 nonce。waiting/time 与 AA partial restart 测试通过；Core 默认对 RUNNING crash 保守停止，需可信人工维护，未覆盖断电文件系统损坏和分布式 failover。

实际修复为取消分派、图安装 revision 原子核对、避免外层锁跨 READ worker，以及 Demo 直接入口 import。没有新性能优化收益宣称，因此不编造优化前后加速比。原异常/失败尝试保留；最新成功证据单独存档。配置隔离、外部出站及 API 超时/限流未扩大。未来生产还需迁移/备份、负载容量、可用性策略和密钥管理评审。
''')
    write('Final_Blockers_and_Operator_Actions.md','''# 集中的剩余阻塞及操作员步骤
不重复注册 Infura、不创建新钱包；使用当前 Sepolia 公开地址。当前没有批准的 exact plan，也没有真实 signer。登记 signer 不代表批准交易。

1. **独立 RPC**：在已忽略 .env 的 ANCHOR_VERIFY_RPC_URL 填另一运营商 HTTPS Sepolia endpoint；不要发到聊天。Infura 换一个 URL 不能算独立。验证 11155111 和共同区块 hash；若失败继续阻塞，不能关闭 TLS 绕过。
2. **现有钱包 signer**：可用既有兼容加密 keystore，或本人在本机运行 `python -m scripts.anchor_operator enroll-keystore`，在隐藏控制台输入现有钱包 key，生成 data/anchor-wallet.json。绝不发聊天/命令行 argv/LLM。该工具只核对地址、加密与文件权限，没有签名或广播。12+字符密码只通过本人进程环境 ANCHOR_KEYSTORE_PASSWORD，.env 仅填 ANCHOR_KEYSTORE_PATH，别保存密码或明文 key。若不愿导出钱包，请保留阻塞，另做已批准兼容钱包签名器，不换资金账户。
3. **准备与核对**：`python -m scripts.sepolia_deployment_readiness --output data/sepolia-readiness.json`；再 `python -m scripts.deploy_audit_anchor --output data/anchor-deploy-plan.json` 仅生成未签名计划。查看 chain、sender、nonce、源码/字节码 hash、gas、费用上界及残余余额。TTL 120 秒；不执行本次过期报告中的 plan。
4. **逐笔明确批准**：批准全字段 exact plan hash 后，操作员才临时设置 ANCHOR_APPROVED_PLAN_HASH、ANCHOR_ALLOW_SIGNING=true、ANCHOR_ALLOW_BROADCAST=true；未批准时保持 false。随后现有部署入口 `--execute-approved-plan data/anchor-deploy-plan.json` 才能签名/广播。这里给出的是将来批准后的流程，本轮未执行，也不是本报告自动批准任何交易。过期/费用/nonce变化必须重新提案和明确批准，不静默提价。
5. **核实部署然后单独准备 commit**：真实 deployment receipt/code/publisher 与主、第二 RPC 对齐；用真实业务 Audit Log 的签名 checkpoint，经过原 ExternalBlockchainAnchor.prepare_commit 与 durable outbox/worker。commit 的金额/目标/calldata/nonce/hash 单独批准，不能把部署批准沿用到 commit。若广播回应丢失先查既有 tx hash，禁止重发新 nonce。
6. **独立链验收**：原 `python -m scripts.verify_external_anchor` 使用导出的实际 checkpoint、公钥 pin、真实合约/Publisher/tx hash/code hash、两个独立运营商，至少 3 confirmations 并检查 canonical/reorg、receipt、event、root readback。全部通过才生成 External_Anchor_Proof.json，并将 external_blockchain_verified 置 true。

以上是一次集中的操作流程，但包含两笔各自精确批准；密码与 key 不发送给助手。外部阻塞所需的不可替代权限来自钱包和现有 exact-plan 安全规则，本报告不能替代人类授权。

**模型质量**：本轮累计请求 800/800，费用预算未耗尽但次数上限已到，不继续付费调参。GLM-4-flash 规划质量 FAILED；4.7 曾限流，新模型/更大新数据评测需要新预算授权。不要为了提升通过率放宽 Validator。DeepSeek 新多目标显式语法样本可展示，真实多轮理解/自由条件仍不具备泛化证明。

**外部银行 AA/六场景写能力**：当前沙箱 manifest 缺这些能力，继续使用虚拟银行实际状态演示；未来需银行提供有授权的 Connector/Capability。生产企业开户和真实资金不在本轮授权范围。无须为比赛本轮重新开企业账户。
''')
    write('TRUSTA_V1_Backend_Final_Acceptance.md',f'''# TRUSTA V1 后端最终验收
{header}

结论：**本地安全后端通过；有限范围真实模型与官方银行沙箱通过；全面后端无条件发布与外部链验收尚未完成。** 不把“714 单元测试通过”当作所有外部能力完成。

真实测试：基线 {baseline['tests']}/{baseline['tests']}，最终 {final['tests']}/{final['tests']}、49 模块、0 skip；新增 21 测试，原测试哈希不变。银行业务 benchmark 34/34，受控银行攻击 45/45，隔离/恢复 31/31，六场景 HTTP 全通过、11 audit proofs，TIME 重启后完成且重复 tick 写入 0，AA 三独立付款及真实虚拟余额变化。完整日志见 Final_Backend_Test_Results.txt，历史失败与评分更正独立保留。

外部证据：DS+GLM 主评测各 23 请求，数据/原始输出/usage/延迟/费用可审计；A/B 两入口分开，不把安全澄清当合法业务完成。DS B_GRAPH 6/6，GLM 0/6，GLM 不达标保留 FAILED。修复后 DS 1 条 dev 通过非泛化证明。官方 Revolut 本轮 5 账户读取及历史 0.01 EUR SETTLED 14/14 读回核查；没有新付款，没有生产资金。Sepolia 主 RPC/Gas/余额 LIVE_TESTED；真实部署和双 RPC 根验证 BLOCKED_EXTERNAL，链证明文件不生成。

状态策略：六 Agent/原金融边界/身份密码与审计核心 FROZEN；本轮定向入口/取消/锁与格式修复 LOCAL_VERIFIED。GLM 规划质量、泛化自然语言条件、多轮真实理解、全 Agent OS 隔离未被误标 FROZEN 或外部成功。DID_KEY_VERIFIED、REAL_ZKP_VERIFIED、LOCAL_MERKLE_VERIFIED 为真实本机密码验证，不能等价为链上信任。

后端交付入口：Gap Audit → Optimization Report → Capability Matrix → LLM / Bank / AA / TaskGraph / Security / Reliability → Sepolia → Blockers。source-before/after/changes、所有 JSON 和原始日志支持复核；Evidence_Manifest.json 固定交付文件哈希，Secret_Scan.json 不含密钥实值。

下一步：先集中完成独立 RPC 与现有钱包受控 signer，并逐笔批准部署/真实审计 commit；另在新授权预算下用未见新数据提升模型质量与自然语言条件/多轮能力。生产资金与 UI 继续单独规划，当前不为了展示而突破能力或资金授权。
''')
    # Check only actual credential values, not harmless field names/fixtures.
    sensitive={k:v for k,v in os.environ.items() if len(v)>=12 and any(word in k for word in ('API_KEY','PRIVATE_KEY','PASSWORD','SIGNING_KEY','STRONG_AUTH_SECRET','RPC_URL'))}
    leaks=[]
    for p in OUT.glob('*'):
        if not p.is_file():continue
        raw=p.read_bytes()
        for key,value in sensitive.items():
            if value.encode() in raw:leaks.append({'file':p.name,'credential_variable':key})
    (OUT/'Secret_Scan.json').write_text(json.dumps({'actual_secret_values_checked':len(sensitive),'hits':leaks,'status':'LOCAL_VERIFIED' if not leaks else 'FAILED','limits':'known current process credential values only; not comprehensive DLP'},indent=2),encoding='utf-8')
    if leaks:raise SystemExit('Credential leak detected; do not deliver.')
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*') if p.is_file() and p.name!='Evidence_Manifest.json'}
    (OUT/'Evidence_Manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'final_tests':final['tests'],'passed':True,'files':len(manifest),'chain_external_verified':False,'llm_total_requests':request_count,'original_tests_modified':0,'secret_hits':len(leaks)}))
if __name__=='__main__':main()
