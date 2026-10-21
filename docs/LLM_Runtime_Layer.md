# TRUSTA Enterprise LLM Runtime Layer

本轮只扩展 `llm/`。银行 contextual Gateway、Agent Runtime、Validator、Execution、Audit、Sepolia 和现有签名安全流程保持不变。通用 Runtime 返回不可信候选，不持有银行适配器、执行回调、签名器或用户授权。其结构校验不能代替金融语义 Validator。

## 数据流

```text
显式 prompt name + version + 已投影变量
    → PromptRegistry / PromptTemplate（模板哈希、渲染后 prompt_hash）
    → JSON Schema / ToolDefinition 候选契约
    → ModelRouter（任务类型、复杂度、成本、延迟、目的与数据安全范围）
    → LLMTraceStore.claim（持久化 request_id，避免并发重复请求）
    → LLMGateway（共享 deadline，同安全配置的最多两个 fallback）
    → 原有 OpenAI / Gemini / OpenAI-Compatible Adapter
    → JSON Schema + format + 工具候选字段校验
    → LLMStructuredResponse + ToolCall 候选
    → LLMTraceStore.finish（成功、拒绝或失败元数据）
```

只有 Trace 终态持久化成功才返回候选。结构错误不会触发 fallback；没有任何从 ToolCall 到银行 API 的直接执行路径。业务接入仍必须走原有身份、Capability、Validator 和 Trusted Execution 边界，本轮不改业务接入。

## 新增与修改文件

新增：

```text
llm/hashing.py
llm/prompts/__init__.py
llm/prompts/templates/structured_candidate.v1.yaml
llm/structured.py
llm/tools.py
llm/router.py
llm/trace.py
llm/runtime.py
tests/test_llm_runtime.py
docs/LLM_Runtime_Layer.md
```

修改：

| 文件 | 修改内容 |
| --- | --- |
| `llm/contracts.py` | LLMResponse 增加 Gateway 填写的实际 provider 名称，原字段与位置兼容 |
| `llm/gateway.py` | 可选 attempt_timeout、响应/错误观察接口，保持原有共享总 deadline、fallback 白名单、schema 拒绝与原生工具禁用 |
| `llm/__init__.py` | 导出 LLMStructuredResponse、ToolCall、ToolDefinition |

未修改 `backend/`、合约、Anchor/Sepolia/MetaMask 脚本、原评测集、原 Prompt 或已有测试。无需新增依赖；复用已安装并声明的 PyYAML 6.0.3 和 jsonschema 4.26.0。

## Prompt Management

PromptTemplate 的版本显式指定，不支持默默选择 `latest`。同一 name/version 的不同内容会被拒绝。模板及渲染结果保存独立快照；修改调用方的原字典不会改变模板或哈希。

`template_hash` 绑定 name、version、原始消息；`prompt_hash` 绑定实际发送的 messages，包含变量展开结果。使用 SHA-256 和 UTF-8 canonical JSON。同一渲染内容在换 Provider 时保留相同 prompt_hash。

模板只允许 `{identifier}`，禁止属性访问、索引、转换和格式规格；变量必须完整匹配且为字符串。用户变量中的花括号不会再次展开。模板、变量及渲染输出有大小限制。YAML 版本文件拒绝重复键、别名、未知顶层字段和过深嵌套。

`structured_candidate.v1.yaml` 是新的可选通用候选模板；不替换历史评测或银行 System Prompt。

## Structured Output 与工具契约

`LLMStructuredResponse.parse(response, schema, tools=())` 严格解析 JSON，拒绝重复键、NaN、非法 JSON、schema 不匹配、已知 format 不匹配和非正常 finish_reason。允许受限本地 `$defs/$ref`；拒绝远端引用和 `$id`，不会访问 schema URL。

结果包含独立 data 快照、schema_hash、原始响应文本的 response_hash 和 tool_calls。hash 是数据完整性标识，不代表授权或操作成功。

工具采用 JSON 候选 envelope：

```json
{
  "data": {"amount": 3},
  "tool_requests": [
    {"name": "read_account", "arguments": {"account": "synthetic"}, "permission_scope": "account:read"}
  ]
}
```

工具名称、参数 schema 和 permission_scope 来自本地可信 ToolDefinition。模型不能增加工具、提升 scope 或添加额外调用字段。最多 32 个定义、16 个候选请求；重复工具定义被拒绝。嵌入 envelope 时重新定位本地引用，保留原参数 schema 的语义。

`permission_scope` 是待核验的请求范围，绝不是 Capability 或用户授权。ToolCall 没有 execute 方法、执行器或银行句柄。即使候选契约合法，也必须由既有安全执行链另行授权和验证。

原生 SDK/API `tools` 参数仍为空，模型返回原生 tool_calls/function_call 仍拒绝。模型如何得知候选契约：调用方显式将 envelope schema 填入模板的 output_contract，或使用已配置支持的 Provider 原生 structured-output shaping。Runtime 不自动改写原评测 Prompt。

## Model Router

ModelRoute 使用原 ProviderConfig / LLMModelConfig，不绑定供应商。可从现有 `config/llm.yaml` 加载；复用目的 allowlist 和 disabled 配置。任务类型映射可通过 `task_routes={provider_name: [task_type, ...]}` 指定，默认 `*`；映射不产生业务权限。

RoutingContext 支持 task_type、complexity、max_cost_usd、max_latency_ms，并保留 purpose、sensitivity、security_level。先过滤能力及安全范围、上下文长度和预算，再按估算成本、预期延迟、配置 priority、名称稳定排序。不会按“模型最强”评分。

成本采用与既有路由相同的 UTF-8 输入字节数保守 token 估计，加 max_output_tokens；若配置成本上限但价格未知，拒绝选择。fallback 累计预留估算也不能超过该请求成本上限。此估算是路由预算，不能保证第三方计费硬上限；真实付费评测仍必须经过原独立评测预算账本。

预期延迟是配置估值；实际超时由共享单调时钟 deadline 约束，未知估值不会被当作零延迟。Runtime 按候选数量分配单次超时份额，预留 fallback 时间，绝不延长总 deadline。旧 Gateway 未指定 attempt_timeout 时保持原行为。

## Provider Fallback

仅 `retryable=true` 且错误为 LLM_TIMEOUT / LLM_PROVIDER_UNAVAILABLE 时可切换。结构错误、非法 JSON、format 错误、scope 不匹配、拒答、原生工具调用、凭据错误和配置错误不切换。

fallback 必须保持 locality、max_sensitivity、data_policy_reviewed、security_level、purposes 一致；不会在失败后扩大数据出境范围、关闭 Validator 或放宽 schema。每次尝试会记录配置 Provider、配置 model、响应哈希和稳定错误码。

## LLM Trace、并发与恢复

Trace 位于独立 SQLite 文件，WAL + synchronous=FULL + BEGIN IMMEDIATE。request_id 唯一，claim 与起始事件原子提交；终态 CAS 与事件原子提交。不同线程、不同进程或重启后复用 request_id 都被拒绝，不产生额外模型调用。

记录 request_id、provider、model、latency、prompt_hash、response_hash，另有 template_hash、request_hash、attempts、status、timestamp 和哈希链。request_hash 绑定渲染消息、有效 schema、实际路由配置与 deadline 设置。Trace 不存 Prompt/响应明文、API Key、银行身份或执行凭据；model 使用受信配置值，响应报告的 model 保留在 LLMResponse 中。

`verify(expected_tail)` 核对哈希链、序号、请求状态投影和 Prompt 绑定。保留 tail 能检出截断或整库替换。未固定可信 tail 的本地哈希链不能抵抗有权限重写整个数据库的攻击者；它不是签名审计证明，不替代既有 Merkle Audit。

进程在请求后崩溃或终态写入失败时，该请求保持 RUNNING，`unfinished()` 可查询。该状态代表结果不确定，不会自动重发、自动判成功或授予工具执行权限。需要调用方明确进行结果核查。此模块不提供跨供应商自动补偿或凭空恢复模型响应。

## 使用入口

下例展示构造方式，默认 YAML mode=disabled 会拒绝发出请求；本轮没有执行付费 API。

```python
from llm.prompts import PromptRegistry
from llm.router import ModelRouter
from llm.trace import LLMTraceStore
from llm.runtime import LLMRuntime

prompts = PromptRegistry()
prompts.load('llm/prompts/templates/structured_candidate.v1.yaml')
router = ModelRouter.from_config('config/llm.yaml')
runtime = LLMRuntime(prompts, router, LLMTraceStore('data/llm-runtime/llm-trace.sqlite3'))
# runtime.generate(request_id=..., prompt_name='structured_candidate',
#   prompt_version='1.0.0', variables={'request': projected_text, 'output_contract': schema_json},
#   response_schema=output_schema, routing=RoutingContext(...), tool_definitions=definitions)
```

## 验证与证据

本轮新增 35 项测试：Prompt 版本/文件/变量攻击、schema/format/本地引用、工具 scope、任务/复杂度/成本/延迟路由、YAML 切换、真实本地 HTTP 超时及 503 fallback、终态错误不 fallback、Trace 并发/重启/篡改/写入失败。

此外，独立验收程序用真实本地 HTTP 对比 Gateway/Runtime 延迟，执行 1,000 次持久化 Trace 请求，并启动 8 个实际 Python 进程验证唯一处理权和重启后的不确定状态。数据位于 `data/llm-runtime/performance-and-process.json`，复现入口 `python data/llm-runtime/runtime_acceptance.py`。

本地性能样本（各 30 次、3 次预热，完整回归并行运行期间采集）：Gateway p50=30.50 ms、p95=41.60 ms；Runtime p50=43.70 ms、p95=53.30 ms。1,000 次 claim+finish 用时 13.034 秒，约 76.72 请求/秒。该数据是 Windows 本地 HTTP/SQLite 开销，不是远端模型吞吐或线上 SLO。

正式全量结果、日志哈希和冻结文件校验以 `data/llm-runtime/acceptance.json` 为准。完整日志：`data/llm-runtime/full-regression.log`；专项日志：`runtime-tests.log`、`provider-regression.log`。本轮真实外部模型调用、外部银行资金操作和公链广播均为 0；全量回归仍包含原有本地虚拟银行与 PyEVM 场景。

## 最终验收结果

- 全量回归：919 / 919 PASS，682.639 秒。
- 原有 884 个测试标识逐一保留；新增 35 项，不删除、不降低旧测试。
- Runtime 专项：35 / 35 PASS，2.368 秒；旧 Provider 专项：24 / 24 PASS，6.213 秒。
- 8 进程并发：1 个 CLAIMED，7 个 LLM_DUPLICATE_REQUEST；重启后不确定请求可查询且不重发。
- 1,000 次 Trace 持久化及 hash-chain / retained-tail 校验 PASS。
- 140 个冻结源文件哈希一致；历史三模型评测输入及原始响应哈希一致。
- 编译检查通过；pip check 无依赖冲突。未安装独立 lint 工具，不将编译检查称为完整静态安全审计。
- 真实付费模型调用、外部银行资金操作、公链广播：均为 0。

结论：本轮 LLM Runtime 功能与覆盖的安全、并发及恢复边界验收 PASS。该结论不代表真实模型能力提升、生产负载认证或不存在未知漏洞。
