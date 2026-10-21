# TRUSTA LLM Intelligence Layer v2.0

## 架构及安全边界

本轮新增的是非可信候选规划层，不是新的 Agent 或金融执行器。

```text
已投影 UserRequest + 本地可信 PlanningContext
  → Prompt Registry（新规划模板 2.0.0，固定版本/哈希）
  → ModelRouter（任务、复杂度、成本、延迟及原有数据策略）
  → LLMGateway → 配置化 Provider
  → 严格 JSON + 统一 TaskPlan/TaskNode Schema
  → PlanValidator（任务绑定、能力、逐请求权限、参数引用、DAG）
  → 原 TaskGraphValidator（事实来源、金额、串行金融写入等）
  → 原 SecurityExpansionCompiler（确定性安全步骤）
  → ValidatedPlan / TaskPlan 候选结果
```

规划结果本身没有银行授权。后续调用方仍必须通过既有 BankActionIR、Capability、Policy、Authorization、Execution Contract、Tool Gateway、HardVerifier 和 Audit。本轮没有调用银行执行器，也没有接入新 HTTP 业务接口或修改现有 Agent Runtime。

原有 `backend/`、合约、Sepolia/MetaMask 脚本、历史基准脚本与旧测试共 244 个文件已建立 SHA-256 对照；最终复核未变化。105 份历史模型证据文件也逐项匹配原哈希。历史评测 Prompt、数据和 GLM 分数不修改。新规划模板是独立的新任务契约，不用于重算旧模型分数。

## 契约

- `TaskPlan`: `task_id`, `goal`, `steps`。
- `TaskNode`: `node_id`, `agent`, `action`, `parameters`, `dependency`。
- Agent 使用原六个注册标识：`supervisor/planner/transaction/finance/wealth/service`。
- Schema 关闭额外字段；最多 32 节点、16 个直接依赖；图深度最多 20。
- 参数仅可引用可信 `input:*` 或祖先节点的 `*:result`。金融提案参数必须来自显式可信输入，模型不得编造金额、账户或授权标识。
- 金额沿用原任务图的整数最小货币单位，例如 0.01 EUR 的可信输入是整数 `1`，不是模型生成的小数字符串。
- 能力从原 CapabilityRegistry 和原 TaskGraph 契约导出；逐请求权限由可信调用方提供，不能读取 LLM 的权限声明。共享读能力下的 `card.list` 和 `card.get` 保持不同操作权限。
- `analysis`、`detect_anomalies`、`create_transfer_plan` 是既有能力的显式别名。原系统没有 `forecast` 契约，因此明确拒绝，未虚构能力或新增 Agent。
- 提案由原编译器插入 `SCHEMA → CAPABILITY → POLICY → AUTHORIZATION → CONTRACT → EXECUTE → VERIFY → RECEIPT`，后续节点依赖 Receipt。模型不能生成这些安全节点。

`TaskGraph.parallel_groups()` 只描述依赖就绪层，不授予执行权限。并行金融写入仍由原校验器拒绝。

## 使用方式

```python
from llm.planner import Planner, UserRequest
from llm.prompts import PromptRegistry
from llm.router import ModelRouter, RoutingContext
from llm.runtime import LLMRuntime
from llm.trace import LLMTraceStore
from validator import PlanningContext

router = ModelRouter.from_config('config/llm.yaml')
runtime = LLMRuntime(PromptRegistry(), router, LLMTraceStore('data/planning-trace.sqlite3'))
planner = Planner(runtime)
context = PlanningContext(
    'task1',
    {'input:account': 'trusted-account-id', 'input:month': '2026-10'},
    [('finance', 'transaction.list'), ('finance', 'analysis')],
)
request = UserRequest(
    'request1', '分析已指定月份的账单', context,
    RoutingContext('BILL_ANALYSIS', complexity='standard', max_latency_ms=10000),
)
validated = planner.generate_validated(request)
# validated.plan / validated.graph / validated.compiled 均不执行银行操作。
```

调用方负责认证、可信输入解析、数据投影及正确的 sensitivity/security_level。账户输入值保留本地，只给 Provider 输入键名；用户请求文本仍受原数据策略约束，不能因为使用本接口就认定任意文本已脱敏。

`llm/router.py` 迁入 `llm/router/__init__.py`，保留旧导入和接口。路由及回退算法不改：仅 timeout/unavailable 可在共享截止时间、预算与相同信任域内回退，Schema 或 Plan 拒绝绝不回退。

## Memory 与 Trace

Memory 使用独立 SQLite，范围为调用方提供的规范 UUID，保留数量可配置。只保存枚举化偏好、通过校验的规划结构投影、白名单失败代码。删除 goal、账户/金额/产品参数、原节点标识、原始文本和模型输出。`SUCCESSFUL_PLAN` 表示规划校验成功，明确保留 `execution_status=NOT_RUN`。Memory 不自动改变 Prompt，不授予权限，不作为风险画像或银行事实来源；用户身份到 UUID 的授权绑定由调用方负责。

沿用 LLM 独立 Trace：唯一 request_id、原子终态、Provider/Model/Latency、Prompt/Response Hash，新增 `validation_result={schema,plan}`，并纳入原哈希链。仅两项验证通过才能记录 Planner 成功和返回结果。数据库/Trace 失败时不返回未记录的候选结果。

根目录 `trace/` 不含 `__init__.py`，避免遮蔽 Python 标准库。独立验证命令：

```powershell
python trace/planning_trace.py --db '现有Trace数据库路径' --tail '独立保留的64位尾哈希'
```

Hash Chain 需要外部保留的可信尾哈希才能检测整条链替换，不等同于原银行 Audit 或链上证明。

## Evaluation 与验收

评测区分 Schema、TaskGraph、Validator 拒绝、符合预期任务的规划成功率和执行对齐。缺少预期计划时不计算规划成功；缺少实际执行证据时执行对齐为 `NOT_RUN/null`。执行证据必须由可信独立验证函数核实，逐条匹配计划哈希、全部编译步骤、Owner、依赖和确认状态；LLM 自报 Receipt 不能通过。

可复现本地接口验收：

```powershell
python -m evaluation.llm.local_acceptance --output data/llm-intelligence-v2/protocol
```

该工具向真实 loopback HTTP 服务发出 24 次合成协议请求，覆盖 OpenAI、Gemini、DeepSeek、GLM Adapter；每个 Provider 的六个用例和 Prompt/Schema/Validator 保持相同。包括两个正确计划、非法 JSON、非法 Action、循环图，以及通过结构校验但指向另一个可信输入的错误意图计划。它验证协议及系统拒绝行为，不测真实模型能力，不使用真实密钥、不产生费用。

本次协议运行报告：`data/llm-intelligence-v2/protocol/protocol-acceptance-6e97bc8d28bb4405840c5f2279bc062d.json`。四个 Adapter 行为及对应 Prompt Hash 一致，Trace 验证通过，并逐例符合锁定的正确/拒绝结果；不能仅因四个 Adapter 同样失败就通过验收。每个 Provider：预期规划成功 2/6，图合法 3/6，Schema 合法 5/6，Validator 拒绝 3/6；后三个比例包含故意攻击样本，不能解读为模型能力得分。执行对齐未运行。真实模型调用、银行写入、链上广播均为 0。

新增专项测试 34 项已通过；全量实际执行 **953 项全部通过，耗时 653.044 秒**，原有 919 项测试标识全部保留（包含最初 884 项及此前 Runtime 的 35 项），新增 34 项。未删除旧测试、调整既有 Validator 或放宽金融规则。专项覆盖 Schema/任务绑定、引用来源、原金额单位、并行写入拒绝、图循环、权限隔离、真实 loopback Provider 请求、超时回退、非法输出不回退、Memory 并发及重启、Trace 篡改和执行证据负例。

代码编译检查及 `pip check` 通过，未新增依赖。新增模块的静态导入复核未发现直接模型 SDK、银行执行器或链上签名模块依赖。全量日志、保护范围复核、源文件哈希和实际结果见 `data/llm-intelligence-v2/acceptance.json`、`full-regression.log`、`protection-check.json`、`source-integrity.json`。

## 文件变更

新增：

- `llm/planner/__init__.py`, `llm/planner/prompt.v2.yaml`
- `runtime/__init__.py`, `runtime/task_graph/__init__.py`
- `schemas/__init__.py`, `schemas/task_plan.json`
- `validator/__init__.py`, `validator/plan_validator.py`
- `memory/__init__.py`, `memory/store.py`
- `trace/planning_trace.py`
- `evaluation/__init__.py`, `evaluation/llm/__init__.py`, `evaluation/llm/metrics.py`, `evaluation/llm/local_acceptance.py`
- `tests/test_llm_intelligence_v2.py`
- 本文及 `data/llm-intelligence-v2/` 验收记录。

修改：`llm/runtime.py`（返回前规划校验与终态）、`llm/trace.py`（向后兼容的校验元数据）。

迁移：`llm/router.py` → `llm/router/__init__.py`，接口兼容。

## 当前限制

1. 未运行新的真实付费模型泛化评测；协议用例不能证明自然语言理解能力。既有模型评分保持原样。
2. 本层仅交付受约束计划，未引入任何银行/链上交易；实际执行对齐指标需要后续独立验收证据，不能伪造。
3. 未注册的 forecast、任意工具、任意 Agent、模型自定义条件表达式均拒绝。沿用原业务能力及确定性金融步骤。
4. 不把 Memory 当成金融事实，也不接入新的 API 身份管理。部署方应由既有可信服务建立用户、输入与权限上下文。

测试及攻击样本只能证明覆盖范围内的行为，不证明没有未知漏洞。
