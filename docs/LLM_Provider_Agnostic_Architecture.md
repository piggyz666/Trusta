# TRUSTA LLM Provider Agnostic Architecture

## 范围与架构

本轮仅更换 LLM Provider 边界和配置入口。业务 Agent、Runtime、Sepolia、Production Backend、Execution、Audit、Validator 保持原样。模型仍然只生成不可信候选，模型更换不产生任何新的银行执行权限。

```text
现有调用方
  → backend.llm.gateway.LLMGateway（身份、数据投影、ModelRouter、deadline、审计）
  → BackendProviderBridge（兼容既有 ProviderRequest/ProviderResponse）
  → BaseLLMProvider.generate(messages, response_schema, tools, config)
  → OpenAIProvider / GeminiProvider / OpenAICompatibleProvider
  → 既有 bounded HTTP transport
  → LLMResponse
  → 既有 grounded Validator / TaskGraph 校验
  → 不可信候选
```

`llm.gateway.LLMGateway` 是面向已投影数据的通用推理 facade，可单独验证 JSON Schema；它不拥有银行、身份或工具权限。银行业务继续使用原有 contextual Gateway，保留原 Validator、审计、数据策略、CAS、replay、防幻觉逻辑。本次仅替换其默认 Provider 工厂，没有重新实现安全决策。

复用旧 `backend/llm/providers/` 的网络传输与响应解析，保留响应长度限制、拒绝跳转、显式代理、固定目的地址、拒绝工具调用/截断/拒答，以及稳定错误码。旧模块继续兼容已发布的历史实验脚本；新的业务默认 Provider 全部经过统一抽象。没有 SDK 依赖，也没有业务代码直接调用 OpenAI/Gemini SDK。

## 新目录与修改文件

```text
llm/
  __init__.py
  base.py
  contracts.py
  configuration.py
  gateway.py
  bridge.py
  providers/
    __init__.py
    common.py
    openai.py
    openai_compatible.py
    gemini.py
config/llm.yaml
requirements-llm.txt
benchmarks/llm_provider_eval.py
tests/test_llm_provider_architecture.py
```

修改既有文件：`backend/llm/configuration.py` 增加 YAML 入口与显式 `openai` 协议类型；`backend/llm/gateway.py` 替换 Provider 构造工厂（所有验证、路由、审计流程保留）；`.gitignore` 排除本地 YAML 配置。未修改 Agent、银行、Production transaction backend、Sepolia、签名器或核心密码学算法。

## 统一数据契约与协议差异

`LLMRequest`：`messages`、`schema`、`tools`、`model_config`。

`LLMModelConfig`：既有 `ProviderConfig` + `timeout` + `max_output_tokens`，不存实际密钥。

`LLMResponse`：`content`、归一化 `usage.input_tokens/output_tokens`、`finish_reason`、实际返回的 `model`、单调时钟测量的 `latency`（秒）。非正常结束仍然抛稳定错误码，不把被截断输出伪装成成功响应。Gemini 思考 token 计入输出用量。

OpenAI 使用 Chat Completions 的 `max_completion_tokens`；兼容 API 保留 `max_tokens`，支持已有显式 JSON mode / structured output / thinking 配置；Gemini 保留原 generateContent 映射，不修改 System/User Prompt。OpenAI 字段依据：[官方 Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。

通用 Gateway 在本地验证完整 JSON Schema，包括本地 `$defs/$ref`、nullable、边界与额外字段；不取远端 Schema。原业务 Gateway 仍使用冻结的 grounded Validator，不以通用 Schema 校验代替金融语义校验。

`tools` 是统一契约的预留字段。本轮保持 TRUSTA V1 的禁用工具策略：非空声明与模型返回的工具调用均拒绝，不执行或转发银行工具。支持工具声明不代表允许工具执行。

## 配置与企业切换

安装新增可选依赖：`python -m pip install -r requirements-llm.txt`。

配置入口：`TRUSTA_LLM_CONFIG=config/llm.yaml`，继续由既有服务启动流程读取；TOML 仍兼容。修改配置后重新启动服务，或重新构造 Gateway；本轮不引入配置热更新竞态。

配置示例：

```yaml
provider: openai_compatible
name: enterprise-private
model: your-deployed-model
endpoint: https://your-approved-model-host/v1/chat/completions
api_key: ${ENTERPRISE_LLM_API_KEY}
timeout: 30
locality: private
max_sensitivity: restricted
data_policy_reviewed: true
json_output: true
gateway:
  mode: shadow
  max_output_tokens: 1024
  max_fallbacks: 0
```

`provider` 支持 `openai`、`gemini`、`openai_compatible`；`deepseek`、`glm` 是兼容协议别名。任何兼容第三方、企业私有与本地部署模型使用显式 endpoint/model，无模型名称硬编码分支。Gemini endpoint 必须指向配置模型的 generateContent URL。不同 API 格式应选择对应适配器，不依赖域名猜测协议。

`api_key` 只接受 `${ENVIRONMENT_VARIABLE}` 或空值；实际密钥由本机环境提供。拒绝明文密钥、重复 YAML 键、别名、危险 tag、未知字段、超大/超深配置。示例配置默认 `disabled`，只声明 public 范围；启用内部/受限数据前必须完成原有 data policy review。

可配置 `fallbacks`（最多两个完整 Provider 配置）和原有 `routes`。业务 Router 保留 purpose/sensitivity/security/complexity/latency/cost 检查。通用 Gateway 的 fallback 只允许相同数据策略与信任域，共享一个总 deadline；仅重试性 timeout/unavailable 可切换。格式错、Schema 错、拒答、工具调用、凭据错均终止。不会为了 GLM 分数修改 Prompt 或降低 Validator。

## 评测

```powershell
python -m benchmarks.llm_provider_eval --provider deepseek
python -m benchmarks.llm_provider_eval --provider glm
python -m benchmarks.llm_provider_eval --provider gemini
```

默认从原 GLM 对照实验读取 23 个主测试和 10 个补充上下文测试的真实响应，通过新抽象层和原 evaluator 回放。逐条核对 frozen request hash、协议 wire body、原测试集 hash，不生成新 Gold，不更改原结果。输出到 `data/model-evaluation/provider-agnostic/{provider}-replay.json`。

回放只证明适配与评分兼容，不代表新的真实模型调用或新的 live 多轮能力提升。三种模型共 99 条回放，全部原评分保持一致；历史失败保留在分母中。

显式 `--mode live --budget 已批准账本路径` 保留真实调用入口，使用原 Budget/CapturingProvider 和统一 Adapter。不会创建、清空或增加预算；已有案例重复调用、余额/请求额度不足会在发送前拒绝。当前旧账本已覆盖这些案例，所以本轮 live 入口实际拒绝，新增 API 请求为 0。未来真实重评需要明确批准的新预算账本，不能复用历史批准制造新费用。

## 验证与限制

2026-10-12 本机 Python 3.13.1 / Windows 验收：

| 检查 | 实际结果 | 日志 |
| --- | --- | --- |
| 最终全量回归 | 884 项通过，666.780 秒 | `final-full-regression.log` |
| 相关 Gateway/Provider/Router/评测回归 | 75 项通过，80.187 秒 | `final-related-tests.log` |
| 最后接口/Schema/配置/费用/边界复测 | 31 项通过，4.927 秒；含 24 项新增测试与 7 项既有 Router 测试 | `architecture-final-tests.log` |
| 首遍全量回归 | 882 项通过，721.202 秒（当时尚未新增最后两项） | `full-regression.log` |
| 依赖兼容 | `pip check` 通过 | `dependency-check.log` |
| 语法编译 | `compileall` 通过 | `acceptance.json` |
| 冻结文件 | 145 个文件 SHA256 全部保持一致 | `frozen-sources.json` / `acceptance.json` |
| 业务与 LLM 层 SDK 导入扫描 | 无直接 OpenAI/Gemini SDK 导入 | `sdk-import-audit.json` |
| 同数据真实响应回放 | 99 条，三种模型的每项评分与历史一致 | `replay-integrity.json` |

新增测试没有删改既有测试；当前发现总数为 884 = 原 860 + 新增 24。开发中曾发现工厂替换遗漏既有 `HTTPAdapter` 长度常量导入，已修复；另补齐本地 Schema 引用有效性检查，并对齐既有 Router 的请求长度定义，防止合法边界请求被误拒绝。最后两项局部修正已在上述 31 项专项复测中重新验证。

测试结果和冻结文件 hash 证据见 `data/llm-provider-architecture/acceptance.json`，完整日志同目录。专项覆盖接口、配置切换、协议请求字段、Schema、timeout、fallback、拒答/工具/截断、重定向、凭据隔离、并发、既有业务 Gateway 集成、回放漂移拒绝和费用保护。全量回归使用当前实际环境执行，不复述历史测试数。

本轮不进行外部模型新调用或 Sepolia/银行资金操作。测试本地 HTTP 的确定性响应是协议夹具，不被计为模型能力成绩。真实响应回放不消除历史 GLM/其他模型的能力缺陷。输出结构合法仍不等于授权或交易完成；冻结业务校验继续拥有最终决策权。测试覆盖不能证明不存在未知漏洞。
