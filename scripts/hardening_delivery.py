"""Evidence-derived delivery; fails closed on regression or altered old tests."""
import hashlib,json,os,shutil,sys,tomllib
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/backend-final-hardening'

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def write(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n' if not isinstance(value,str) else value.rstrip()+'\n',encoding='utf-8')
def eth(wei):return str(Decimal(wei)/Decimal(10**18))

def main():
    baseline=json.loads((ROOT/'data/hardening-baseline-result.json').read_text())
    final=json.loads((ROOT/'data/hardening-final-recheck-result.json').read_text())
    assert baseline['passed'] and baseline['tests']==714 and baseline['skipped']==0
    assert final['passed'] and final['tests']>=737 and final['skipped']==0
    before=read('source-before.json');after={}
    for folder in ('backend','scripts','tests','contracts','benchmarks'):
        for path in (ROOT/folder).rglob('*'):
            if path.suffix in {'.py','.sol'}:after[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    modified=[p for p in before if before[p]!=after.get(p)]
    added=sorted(set(after)-set(before))
    assert not any(p.startswith('tests\\') for p in modified),'Old test source changed'
    protected=('backend\\agents\\','backend\\runtime\\','backend\\security\\','backend\\execution\\','backend\\banking\\','backend\\bank_sdk\\','backend\\audit_trust\\','contracts\\')
    assert not any(p.startswith(protected) for p in modified),'Frozen module changed'
    write('source-after.json',after);write('source-changes.json',{'modified':modified,'added':added,'deleted':sorted(set(before)-set(after)),
        'original_test_files_unchanged':True,'frozen_financial_security_agent_contract_modules_unchanged':True,
        'git_repository':False,'local_env_change':'Only verified public ANCHOR_VERIFY_RPC_URL configured; .env ignored; secret values not recorded'})
    write('baseline-summary.json',baseline);write('full-test-summary.json',final)
    for source,target in [('data/hardening-changed-surface.txt','changed-surface-tests.txt'),
        ('data/hardening-aa-graph.txt','aa-taskgraph-run.txt'),('data/hardening-six-scenes.txt','six-scenes-run.txt'),
        ('data/hardening-nl.txt','local-nl-run.txt'),('data/hardening-llm-replay.txt','llm-offline-replay-run.txt')]:
        shutil.copy2(ROOT/source,OUT/target)
    anchor=read('sepolia-live-evidence-final.json');independent=read('sepolia-independent-verification.json')
    nl=read('llm-natural-language-benchmark.json');agent=read('agent-communication-security-benchmark.json')
    reliability=read('backend-reliability-benchmark.json');bank=read('banking-task-benchmark.json');security=read('banking-security-benchmark.json')
    assert bank['passed'] and security['passed'] and agent['passed'] and reliability['audit_chain_valid']
    historical=read('llm-historical-replay.json');analysis=read('llm-failure-analysis.json')
    timestamp=datetime.now(timezone.utc).isoformat()
    write('security-regression.json',{'status':'LOCAL_VERIFIED','measured_at':timestamp,
        'baseline_tests':baseline['tests'],'full_tests':final['tests'],'full_passed':True,'skipped':0,
        'banking_security':security['metrics'],'agent_attacks_and_recovery_tests':agent['tests'],'agent_passed':agent['passed'],
        'new_targeted_tests':23,'changed_surface_tests':60,'new_attacks':'Selection replay/race across Core instances, interrupted proposal creation, identity, context/graph tamper, expiry, resource mutation, currency mismatch, conditional authority separation',
        'old_test_sources_unchanged':True,'frozen_security_sources_unchanged':True,
        'bank_calls_remote_this_run':0,'llm_calls_remote_this_run':0,'real_wallet_signatures':0,'chain_broadcasts':0,
        'limits':['Controlled regression/attack harness, not an external penetration audit','Finance physical isolation only, not six-Agent/HSM isolation']})
    defects=[
        ('P0','ANCHOR_SIGNER_APPROVAL','BLOCKED_EXTERNAL','受控钱包签名器与逐笔交易批准均缺失；禁止自动替代。'),
        ('P1','INDEPENDENT_RPC','LIVE_TESTED','已找到 Pocket Network；共同规范区块、交易、回执、代码和 eth_call 读取通过；实际 Anchor 验收待部署。'),
        ('P1','COMPLEX_NL_WORKFLOW','LOCAL_VERIFIED','近三个月分析→权威订阅匹配→明确选择→独立取消授权→核验回执→预算完成。'),
        ('P1','CONDITIONAL_PROPOSAL_FORMAT','LOCAL_VERIFIED','compact_v2 无 condition；按单次请求选用已有 full_v1，保持 Router 与 Validator。'),
        ('P1','CONDITION_ID_MATCH','LOCAL_VERIFIED','语义匹配按对应生产者映射 condition ref，不再拒绝等价模型节点名；反转条件仍拒绝。'),
        ('P1','SOURCE_SELECTION_CROSS_CORE_RACE','LOCAL_VERIFIED','双实例复现同一选择产生两个待批提案；复用数据库 GraphClaims 并先持久化消费，竞争/创建中断复测通过。'),
        ('P1','LLM_NEW_GENERALIZATION','BLOCKED_EXTERNAL','800 次旧预算耗尽；仅历史原始响应离线分析，新模型可用性和新盲测不能推定。'),
        ('P1','FREE_NL_AND_AA_COMPOSITION','NOT_IMPLEMENTED','通用 from-text 图未覆盖任意口语与 AA 混合组合；已有独立 AA API/场景仍通过。'),
        ('P1','BLIND_INDEPENDENT_GOLD','BLOCKED_EXTERNAL','新作者合成集已冻结，16 项 holdout 未运行；真正独立作者盲测仍需独立人员提供。')]
    write('gap-analysis.md',f'''# 后端现状与遗留台账
复核时间 {timestamp}；Windows / Python 3.13.1。本地没有 Git 仓库，无法给出 commit/diff；使用逐文件 SHA-256 比较。原 714 项测试完整保留。

新基线：{baseline['tests']}/{baseline['tests']}、0 skip，{baseline['duration_seconds']} 秒。修改后：{final['tests']}/{final['tests']}、0 skip，{final['duration_seconds']} 秒；原测试文件未改。

| 等级 | 项目 | 状态 | 结论 |
|---|---|---|---|
'''+ '\n'.join('| '+' | '.join(row)+' |' for row in defects)+'''

架构评估：不需要改变冻结架构。所有新能力只增加非 Agent 规范化/对话协调层。未发现需要迁移六 Agent、消息密码学、银行 SDK 或金融执行边界的新缺陷。原报告的“第二 RPC 未找到”已解决；不能继续把它列为操作员注册要求。签名器和交易批准仍实际缺失。

局部策略：先冻结源代码与基线，审查真实响应和输入来源，完成安全组合与兼容性修复，再运行专项攻击、旧基准、六场景、AA、故障恢复及全量回归。不能修复的外部权限明确保留。
''')
    write('root-cause-and-fixes.md','''# 根因、修复与架构变化

1. `backend/llm/grounded_request.py` 原入口整体拒绝“如果”，只支持单月规范措辞。底层已有 WAIT_USER/条件和多读 DAG，但规范化层没有对应表示。增加严格的“如果我选择继续，则…”选择条件和最近三个月完整自然月读取；天气、余额比较等没有权威条件来源的表达继续澄清。金融金额仍来自用户原文，不猜测。
2. `backend/llm/compact_graph.py` 原紧凑 calls 精确字段只有 node_id/function/parameters/dependencies，没有 condition。这是协议表达范围问题，不是模型不理解。对已规范化的条件请求通过 Gateway 的单次 format 参数选择既有 full_v1。全局设置、默认 compact_v2、注册 DSL、节点上限、金融展开不变。没有引入第三套协议。
3. `matches_request` 原 condition 直接按对象相等比较，重命名等价节点时会误拒绝。现在根据生产者对应关系核对引用；equals、来源和先后依赖严格保留。不同条件仍拒绝。
4. 新 `backend/llm/subscription_workflow.py` 复用 Finance recurring、Service resolve 和 Wealth budget_plan。三个月账单与银行历史、订阅读取汇合；只列出近期证据足够、唯一权威匹配的 CNY 月度可取消订阅。不替用户判断“没必要”。用户明确选择并给出基础预算后，生成新的取消提案；原 Policy/Authorization/Contract/Gateway/HardVerifier/Receipt 决定何时可执行。取消被拒绝时节省金额为 0，不伪造回执。
5. 两阶段采用“已完成只读图 + HMAC 封印的待选择上下文 + 新金融图”，而不是动态改写运行中 action inputs。显式 revision、用户绑定、30 分钟有效期、一次消费、实时资源快照复核；子任务审计关联源 task/state hash。选择不是授权，新任务不继承旧 consent。查询并行、金融动作串行，保持旧编译器。
6. 独立 RPC 首次 latest 区块读取偶遇尚不可用的区块，改为已确认高度进行准备期连通性检查；失败尝试保留。Pocket Network 与 Infura 的规范区块、真实交易/回执及非空代码一致，已配置本地独立读取入口。最终验收仍使用原验证器对实际交易确认深度与 reorg 检查，不用准备期样本替代。
7. 新协调层初版仅依赖 Service 的进程内 RLock，两个 Service 实例可各自接受同一源选择并创建两个待批提案。selection-race-before-fix.txt 保存实际失败（2 != 1）。现在复用既有数据库 GraphClaims，对源 task 跨实例互斥；创建子图前先持久化 consumed=true。创建失败/中断后不自动重开选择；明确停止并需重新发起只读分析。专项竞争与故障测试见 selection-race-after-fix.txt。只有未授权提案曾可重复，未出现自动执行金融动作。

新增 HTTP：POST `/api/workflows/subscription-budget` 只接受 text/account_id；POST `/api/workflows/{source_task_id}/select` 只接受 revision/text。随后继续原 `/api/graphs/{child_task_id}/authorize`。客户端 user_id 字段不能覆盖认证身份。

修改/新增文件精确列表见 source-changes.json。现有六 Agent、TaskGraph Validator/Compiler/Runtime、ECDSA/DID/Groth16、Merkle、银行适配器及 Solidity 源码保持本轮原始哈希。未修改任何原有测试断言。
''')
    counts={}
    for r in analysis['cases']:
        for cause in r['root_causes']:counts[cause]=counts.get(cause,0)+1
    write('llm-provider-evaluation.md',f'''# LLM Provider 与真实失败审查
状态：历史模型接入 LIVE_TESTED；本轮新远端模型评测 BLOCKED_EXTERNAL。旧请求 800/800，未新授权费用/次数。本轮远端请求 0、新增 API 费用 0。不能把旧 holdout 再称为新盲测。

实际保存的 46 次原始响应已在当前代码离线重放，配置模型 deepseek-flash / glm-4-flash-250414。raw_model_quality、validated planning 与 E2E 保持分别计分。重放来源、SHA-256、原始内容、usage/latency、原分母和修订评分见 llm-historical-replay.json；根因逐样本见 llm-failure-analysis.json。

历史结果（不是本轮新模型准确率）：DeepSeek B_INTENT raw 5/10、接受 6/10、期望结果 6/10；B_GRAPH raw 6/6、接受 6/6、期望结果 6/6。GLM B_INTENT raw 2/10、接受 3/10、期望结果 6/10；B_GRAPH raw 0/6、接受 1/6、期望结果 0/6。正确澄清或暂停不等于付款成功。

源码与响应证据：DeepSeek FC01 amount_text 包含“元”，旧 Prompt 没明确数值子串边界，是提示词与约束接口问题；既有修复保留，不把失败统一归于模型。GLM FC03 模仿 few-shot 额外加入未请求 anomalies；FC11/20/23 丢失金融串行依赖，FC21 缺版本号，FC22 错 input refs。它们分别属于语义/依赖/格式错误，不是银行执行层问题。历史主评测响应 finish_reason=stop，正常模型 content 与 reasoning_content 已分离；不把思考内容当提案。早期 4.7 限流/截断是单独历史证据，不能推断所有 GLM 质量。

本轮根因计数（允许一项多标签）：{json.dumps(counts,ensure_ascii=False)}。

Provider/Router 保持模型无关：OpenAI-compatible、Gemini、声明式第三方 HTTP、企业/本地 Provider；配置控制 model、目的、复杂度、数据敏感度、审核、locality、超时、费用。只读检查不自动探测远端模型，429/断线只有既有有界重试，禁止修补金额、补审批或猜资源。新 format 参数不绕过 Router 数据出站条件。

新数据 6 development + 18 locked evaluation + 16 prepared holdout 已在任何本轮离线评分前冻结。24 个本地样本 {nl['passed']}/24 符合原始预设；合法目标 {nl['legal_local_outcomes']['numerator']}/{nl['legal_local_outcomes']['denominator']}，正确澄清 5/5。不是 LLM 准确率。HN11 的 23 元低于产品最低申购额，预设 WAITING_USER 错误，实际 POLICY_BLOCKED 正确；不改冻结数据或安全规则，保留原得分。HN14 AA 混合图、HN15 任意口语仍是实际能力缺口。

新的 holdout 未运行；作者编写的数据不构成外部独立盲测。待新预算授权和独立 gold 后，才可报告新 DeepSeek/GLM 实时准确率及适合任务规划的新 GLM 型号可用性。
''')
    write('taskgraph-generalization.md','''# TaskGraph 能力与边界

新增 LOCAL_VERIFIED：自然语言最近三个月读取、显式用户选择条件、跨 Agent 订阅→预算的多轮选择；在已有 32 semantic nodes、20 depth、16 dependencies、64 explicit input refs 内运行。条件引用必须指向祖先 WAIT_USER/BRANCH/DECIDE；金融节点仍只接受本地显式 input，Compiler 注入全部八个安全阶段。

`subscription-budget-demo.json` 来自当前代码实际运行：原始长句 → 三月读取与汇总、历史候选和银行 subscription.list → 唯一 Service 匹配 → 显式“取消演示视频，下个月基础预算一百元” → 新 Cancel 图停在 WAITING_USER → 重启 → 独立批准 → verified receipt → 119.99 元预算。19.99 元节省来自核验过的取消对象；预算规划不是购买、收益承诺或实际增加余额。CNY 月度订阅为当前支持范围。

条件例：“如果我选择继续，则给张三转一元”。第一次停在真正 WAIT_USER；signal=true 后仍需独立金融授权；false 完整跳过金融展开，没有动作、授权或资金写入。模型等价节点名通过，条件反转拒绝。条件请求使用既有 full_v1；compact_v2 不被伪造为支持条件。

原 ProposalDialogue 补槽、Conversation 补充金额/修改/撤销、定时执行到期再验、图取消与部分完成回执保留继续由旧模块处理。60 项变更面回归含这些原始断言；跨 Service 实例竞争提交只生成一个待批动作。持久化一次消费发生在子图创建前；中断后不自动重建金融提案。

局限：订阅组合是可审计的有限规范化工作流，不是任意语言规划器；“没必要”由人选择。通用自然语言 AA 混合组合、天气/自由银行事实比较与随意改单条已执行计划仍不实现；已完成 AA API 不受影响。未运行新实时模型或 holdout，不宣称无界泛化准确率。
''')
    write('trusted-agent-security.md',f'''# Agent 可信通信与证据复核
状态 LOCAL_VERIFIED。实际 Windows Finance AppContainer、Broker 权限攻击与恢复重新运行 {agent['tests']}/{agent['tests']}、0 skip。输出见 agent-communication-security-benchmark.json 和 agent-communication-recovery-report.json。

仅 GENERAL_GRAPH Finance read/summary/anomalies 走隔离子进程。broker 锁定 launch identity、request/step/user/inputs，独立比对银行读取/Finance 计算结果；提交后复用有封印结果。宿主保管签名器，子进程不持银行 token、私钥或写权限。recurring 路径和其他 Agent 仍宿主执行，不宣称六 Agent/HSM 物理隔离。

原 ECDSA V3 覆盖 payload hash、sender/receiver、capability snapshot、task/version、nonce、撤销与重启重放。重规划产生新任务或取消旧待批动作，旧 authorization 不迁移；消息签名不等于金融授权。DID 为已有 did:key 本地公钥绑定，Groth16 为实际开发 setup，不是外部身份网络/生产 ceremony。

新增选择上下文同时检查独立 HMAC、owner、revision、source graph seal、期限和最新银行资源；篡改、过期、重复、竞争、跨用户、币种改变均拒绝。未弱化既有消息验证。

新 workflow-handoff.json 可用 workflow-trusted-registry.json 中经带外信任的公钥/capability 验证；workflow-record-proof.json、workflow-checkpoint.json 与 public.pem 验证同一次真实本地取消的审计包含证明。workflow-checkpoint 的 Merkle Root 非随机摘要，可作为随后逐笔批准 Commit 的业务来源。当前 root **尚未写入 Sepolia**。源读取图到子图的关联由真实审计事件携带 source_task_id/state hash；没有虚构跨进程网络协作或已完成链锚。

第三方必须带外固定 trust root，不能把随包附带的 registry 自动当信任。证明谁签了什么与因果引用，不证明隐藏 payload 全语义、生产银行清算、历史 registry 当前有效，或抵御全部 Core/issuer 密钥失陷。
''')
    write('banking-capability-validation.md','''# 银行与六业务能力
当前本轮 LOCAL_VERIFIED：Banking Task 34/34；真实虚拟银行任务完成 23/23；Security 45/45，未经授权执行 0/45。six-scenes-http.json 六场景均通过，11 个真实回执/Merkle 证明；定时任务重启后再验，重复 tick 写入 0。aa-demo.json 三位参与者独立审批，收款 100.00 元，3 个回执及证明。AA 部分付款/拒绝/过期/重复等旧生命周期断言在本轮全量测试中重新通过。

官方 Revolut 历史证据保持原样：docs/final-backend/bank-live-read.json 的 5 账户与 GBP 账单；bank-payment-readback.json 唯一 0.01 EUR Acme Corp. 官方 Sandbox SETTLED 读回 14/14。本轮按指令未重复账户读取、历史支付或模拟结算。历史证据 LIVE_TESTED，不能当作本轮新远端联调。生产银行真实资金 NOT_IMPLEMENTED/本轮禁止。

源码/离线回归覆盖 Provider PENDING/UNKNOWN/SETTLED 映射、401 refresh 只重试 GET、限流/超时、币种精度与消费排除、外部未知写去重/对账、Manifest 支持判定、Sandbox/Production 隔离。原 Adapter/SDK/Agent 代码没有改。无授权财务状态变化的攻击检测持续通过。

Revolut 当前 manifest 不提供 AA、理财买赎、卡/订阅资金写能力；它们外部能力 BLOCKED_EXTERNAL/UNSUPPORTED，接口前拒绝，不能模拟成功。对应 Virtual Bank 已有六场景实际状态闭环。新增跨场景工作流仅 Virtual Bank CNY 月度订阅；预算不执行理财购买。

语言基准 HN11 POLICY_BLOCKED 是产品最低申购额规则的正确保护，不能为了预设成绩放宽。泛化模型质量与银行安全得分分开。
''')
    write('reliability-results.md',f'''# 可靠性实测
本轮总回归 {final['tests']} 项，50 modules，0 fail / 0 skip。基线 {baseline['tests']} 项独立进程重新执行，历史 714 不只是复述。旧文件 SHA-256 全部相同。

受控 crash/restart/read-result/nonce replay/claim/密钥撤销/图封印/外部银行未知状态和 chain reorg：旧安全与恢复测试全量重跑；Agent 安全/恢复独立 {agent['tests']} 项通过。新 23 项覆盖身份/一次选择/跨 Service 竞争/子图创建中断/封印/期限/银行快照/币种、对话重启、选择与批准分离、条件 skip 和完整协议切换；变更面新旧合计 60 项通过。735 项首次全量之后发现新协调层竞争缺陷，保留中间结果，再以修复后代码重新运行全部 737 项。

8 clients / 80 HTTP 请求全部正确，P50 {reliability['http_latency']['p50_ms']:.3f} ms / P95 {reliability['http_latency']['p95_ms']:.3f} ms。Finance 进程图 8 次，P50 {reliability['process_graph']['p50_ms']:.3f} ms / P95 {reliability['process_graph']['p95_ms']:.3f} ms（含冷启动）。1000 审计事件与 checkpoint {reliability['audit_append_and_checkpoint']['duration_ms']:.3f} ms；20 次证明查询验证 P50 {reliability['receipt_proof_query_verify']['p50_ms']:.3f} ms。SQLite 真写锁竞争等待 {reliability['sqlite_write_contention_ms'][0]:.3f} ms 后完成，审计链有效。

核心保守恢复不自动重试 RUNNING/EXECUTING 金融写；等待节点和经过封印的只读结果可按原边界恢复，未知银行交易先对账。同一次 source 选择重复或并发最多一个 proposal，重启后新授权方可取消。实际断电文件系统恢复与生产 SLA 不在此本机回归证明范围。
''')
    tx=anchor['unsigned_deployment_plan']['transaction'];balance=anchor['wallet_balance_wei'];total=anchor['combined_quote_worst_cost_wei']
    write('operator-actions.md',f'''# 集中操作员事项（尚未签名/广播）

已自动完成：Infura 保留；本地 .env 已接入通过核对的 Pocket Network HTTPS 第二 RPC；Chain ID 11155111；余额 {eth(balance)} SepoliaETH；nonce {tx['nonce']}；部署实时 estimateGas 3,802,284，首次 commit state-override estimateGas 356,357。准备期样本不是 TRUSTA Anchor 交易，实际 Anchor 验证仍待部署。

公开部署钱包/Publisher `{anchor['wallet_public_address']}`；source SHA-256 `{anchor['source_sha256']}`；creation SHA-256 `{anchor['artifact_creation_sha256']}`；预期 runtime Keccak `{anchor['expected_runtime_keccak256']}`。现有受控本地加密 Keystore 流程与地址绑定检查已测试；MetaMask 公共地址不构成签名权限。

1. 本人本机运行 `python -m scripts.anchor_operator enroll-keystore`，隐藏输入现有测试钱包私钥及 12 字符以上密码，生成 data/anchor-wallet.json。若已有兼容加密文件则复用。.env 只填写 ANCHOR_KEYSTORE_PATH；密码仅本人进程环境 ANCHOR_KEYSTORE_PASSWORD，不放 .env、聊天、命令行参数或日志。不需要再注册 RPC、不创建新钱包。
2. 签名器配置完成后重新只读准备部署计划，核对 chain/sender/nonce/code/gas/费用；明确批准新鲜 exact-plan hash。当前报告 hash `{anchor['unsigned_plan_hash']}` 的 TTL 仅 120 秒，交付时已过期，不可直接执行。签名和广播开关保持 false，直到具体计划明确批准。
3. 部署确认后再从真实业务 Audit Log/checkpoint 由现有 Outbox/Worker 准备 commit，独立批准该计划；部署授权不能沿用。采用原独立验证器检查至少 3 confirmations、canonical/reorg、实际 receipt/event、code/Publisher/root read-back，两运营商通过才生成 ExternalAnchorProof。
4. 若希望继续新实时 LLM 评测，需要明确新的模型请求次数与费用预算。旧 800 次已耗尽；本輪没有擅自继续调用。新数据集与 16 个未运行 holdout 已准备；独立作者 gold 仍有价值。

本次费用报价上界：deploy {eth(anchor['deploy_worst_cost_wei'])} + commit {eth(anchor['commit_worst_cost_wei'])} = {eth(total)} ETH；报价后余额至少 {eth(balance-total)}。margin gasLimit 4,562,741 / 427,629，maxFee {Decimal(tx['maxFeePerGas'])/10**9} Gwei、tip {Decimal(tx['maxPriorityFeePerGas'])/10**9} Gwei。实际链上 gasUsed 尚不存在，不拿上限当消耗。

费用以 unsigned_deployment_plan 的现有受保护准备器报价为准；内嵌 read_only_estimation.current_eip1559 是另一个保守费用观察值，并非本次交易计划。两者不能混用。低报价不保证确认时延，后续按新鲜 RPC 与既有硬上限重新准备；不为追求速度自动提价。

硬保护有效最高两笔费用 .0097 ETH + .0002 留存 = .0099 ETH，现有 .0103 足够；名义更保守两笔 cost caps .0100 + 留存 .0002 也有余额。上涨到当前 gasLimit × 2 Gwei 时部署超过 .0085 cost cap 必须拒绝，不能无限提价。具体交易按新鲜实时估算执行；commit 当前只是新合约 state override，不是已部署合约的真实消费。

状态严格分为 PREPARED→APPROVED→SIGNED→BROADCAST→CONFIRMED→VERIFIED。本轮只到 PREPARED，real_wallet_signatures=0 / broadcasts=0 / ETH spent=0；不生成假的部署/commit hash 或外部成功证明。
''')
    matrix=[
        ('六 Agent / 金融安全边界','LOCAL_VERIFIED',f'{final["tests"]} 全量 + 45 攻击，源码不变'),
        ('六场景 HTTP / AA','LOCAL_VERIFIED','6/6、11 proofs；三位独立付款、本地账本'),
        ('自然语言条件 / 三月分析 / 多轮订阅预算','LOCAL_VERIFIED','23 新回归 + 当前 Demo，不是实时模型质量'),
        ('真实 DeepSeek/GLM 新准确率','BLOCKED_EXTERNAL','旧 800 请求耗尽；历史 46 响应重新离线分析'),
        ('GLM-4-flash 历史 B_GRAPH','FAILED','0/6；不等于所有 GLM 模型质量'),
        ('任意口语/AA通用混合图','NOT_IMPLEMENTED','有限规范化语法未覆盖全部合法自由表达'),
        ('新的实时独立盲测','BLOCKED_EXTERNAL','16 项作者准备 holdout 未运行；独立 gold 与新调用预算未提供'),
        ('官方银行已有沙箱证据','LIVE_TESTED','保留历史账户/GBP账单/唯一0.01EUR SETTLED；本輪不重复'),
        ('外部 AA/理财/卡订阅金融写 API','BLOCKED_EXTERNAL','Manifest unsupported；虚拟场景通过'),
        ('生产真实资金','NOT_IMPLEMENTED','本轮禁止，未测试'),
        ('ECDSA V3 / DID / Groth16 / Merkle','LOCAL_VERIFIED','原模块重跑，不是外部 DID/生产 setup'),
        ('Finance AppContainer / Broker','LOCAL_VERIFIED','31 真实本机攻击与恢复；非六 Agent 全隔离'),
        ('六 Agent OS隔离/HSM','NOT_IMPLEMENTED','宿主 Core/其余 Agent 是信任边界'),
        ('Sepolia Infura只读费用与余额','LIVE_TESTED','11155111，.0103 ETH，实时RPC估算'),
        ('第二运营商 RPC','LIVE_TESTED','Pocket Network共同区块/交易/回执/代码/调用核对；本地配置已完成'),
        ('真实合约部署 / Merkle commit / 独立Anchor验收','BLOCKED_EXTERNAL','缺本机 signer 与逐笔 exact-plan 批准；没有广播')]
    write('final-capability-matrix.md','# 最终能力矩阵\n\n| 能力 | 状态 | 证据范围 |\n|---|---|---|\n'+'\n'.join('| '+' | '.join(row)+' |' for row in matrix))
    write('TRUSTA_V1_BACKEND_FINAL_REPORT.md',f'''# TRUSTA V1 后端最终加固交付
交付 {timestamp}。状态：本轮可自主实施的代码与本机验收完成；真实部署/commit 和新实时模型评测仍 BLOCKED_EXTERNAL，不能声称全部外部闭环完成。没有真实银行操作、真实钱包签名或链上广播。

## 已完成
714/714 新基线；修改后 {final['tests']}/{final['tests']}，0 skip、50 modules、{final['duration_seconds']} 秒；原 714 测试源码保持不变。新增 23 项缺陷/攻击/恢复回归，变更面 60 项通过。六 Agent、金融核心、Trusted V3、DID/Groth16、Merkle、SDK、Solidity 本轮哈希不变；精确修改见 source-changes.json。保留首次 735 项中间回归及跨 Service 竞争缺陷失败复现；修复后重新完成全量 737 项与真实业务 Demo。

补齐三月分析、显式选择条件、完整提案协议兼容、等价 condition refs、多轮用户选择和银行权威对象绑定。长句订阅→预算 Demo 实际完成取消及验证回执，取消前要独立授权；新 API 可供后续 UI 使用。数据流仍为规范化/对话 → Gateway/Router（需要模型时）→ 已注册 Supervisor/Planner → Blackboard → 确定性编译/安全执行 → 银行适配器 → HardVerifier → Receipt/Merkle Audit，LLM 无 Bank API 路径。

独立 Pocket Network RPC 已通过真实只读核对，并接入 .env。Infura、钱包、源码与费用保护未改。部署 estimateGas 3,802,284；首次 commit estimateGas 356,357（只读新合约 state override）；余额 {eth(balance)} ETH；本次两笔 margin 报价上界 {eth(total)} ETH。资金够当前报价及既有硬保护，签名器/逐笔许可仍缺，报价 TTL 120 秒不得直接执行过期计划。

## 当前验收数据
- Banking Task 34/34，实际本地任务完成 23/23；Security 45/45，未经授权执行 0/45。
- Agent 真实 Windows 隔离攻击/恢复 31/31、0 skip；8 clients/80 HTTP 全部通过，1000 审计事件与链验证成功。
- 六场景 HTTP 6/6，11 proofs；定时重启恢复及重复写入 0；AA 三笔独立审批/收款/回执验证。
- 新本地语言样本原始预设符合 {nl['passed']}/24（合法目标 16/19、正确澄清 5/5），不是模型准确率。HN11 预设忽略产品申购下限，实际 Policy 正确拦截；保留原得分。AA 通用图组合与自由口语的两项真实缺口保留。新 prepared holdout 16 项，运行 0。
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
''')
    # Scan actual configured secret values without logging those values.
    env=ROOT/'.env';secrets=[]
    for line in env.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            if any(word in key.upper() for word in ('KEY','SECRET','TOKEN','PASSWORD','RPC_URL')) and len(value.strip())>=12:
                if value.strip()=='https://eth-sepolia-testnet.api.pocket.network':continue
                secrets.append((key.strip(),value.strip()))
    for key,value in os.environ.items():
        if (any(word in key.upper() for word in ('API_KEY','ACCESS_TOKEN','REFRESH_TOKEN','PASSWORD','AUTH_SECRET','RPC_URL'))
                and not key.upper().endswith(('_PATH','_FILE','_ENV')) and len(value)>=12
                and value!='https://eth-sepolia-testnet.api.pocket.network'):
            secrets.append((key,value))
    # Inspect token stores only inside this process; never output token values
    # or private key material. This performs no bank request or token refresh.
    for config in (ROOT/'config').glob('bank.*.local.toml'):
        settings=tomllib.loads(config.read_text(encoding='utf-8'))
        if settings.get('token_path'):
            path=(config.parent/settings['token_path']).resolve()
            if path.is_file():
                tokens=json.loads(path.read_text(encoding='utf-8'))
                for key in ('access_token','refresh_token'):
                    value=tokens.get(key)
                    if isinstance(value,str) and len(value)>=12:secrets.append(('BANK_'+key.upper(),value))
    secrets=list(set(secrets))
    hits=[]
    for path in OUT.iterdir():
        if path.is_file():
            text=path.read_text(encoding='utf-8',errors='replace')
            for key,value in secrets:
                if value in text:hits.append({'file':path.name,'secret_category':key})
    write('secret-scan.json',{'checked_files':len(list(OUT.iterdir())),'configured_secret_categories_checked':len(secrets),'hits':hits,'secret_values_logged':False,
                            'scope':'Configured .env/process API credentials and local bank access/refresh token stores; no private key extraction or remote requests'})
    assert not hits,'Secret values found in evidence'
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='evidence-manifest.json'}
    write('evidence-manifest.json',manifest)
    print(json.dumps({'status':'DELIVERED_WITH_EXPLICIT_EXTERNAL_BLOCKERS','baseline':baseline['tests'],'full_tests':final['tests'],
                      'modified_files':modified,'added_files':added,'secret_hits':len(hits),'external_chain_verified':False}))

if __name__=='__main__':main()
