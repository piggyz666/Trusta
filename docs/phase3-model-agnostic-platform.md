# TRUSTA Phase 3.1：模型无关推理基础

Phase 3.1 建立统一 Provider Interface、LLM Gateway、确定性 Model Router、候选结果与调用追踪。该阶段默认关闭推理；启用后仅支持影子模式。现有五个 Agent、三个业务 DAG、金融执行链和银行权限保持。

本文保留 Phase 3.1 交付记录；当时仅支持 disabled / shadow。当前 Phase 3.2 的 Conversation、受约束辅助意图、模板计划和有据说明已完成，`mode="assisted"` 需明确启用，见 [Phase 3.2](phase3.2-conversation.md)。Phase 3.3 的类型化 Memory、V2 消息、消息密钥版本轮换与关闭的扩展接口也已完成，见 [Phase 3.3](phase3.3-memory-communication.md)。以下后续描述保留原阶段规划背景；真实密码学扩展、外部模型质量与生产隔离尚未完成。

## 架构与数据流

现有请求仍由 Intent Normalization → Supervisor → Planner → Scheduler / Blackboard → Transaction / Finance / Wealth 处理。金融写操作始终经过：

BankActionIR → Schema / Capability → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → Verification → Audit。

任务完成或等待用户时，运行时可创建独立影子请求：

1. 从所属任务生成所有者、Agent、场景与任务版本上下文。
2. 按用途投影数据：转账发送基础脱敏的请求文本；Finance 发送月份、净支出和记录数；Wealth 发送风险等级和已筛选产品的必要字段。不发送账户、完整流水、合同、审批、银行凭据或服务端身份上下文。
3. Gateway 校验用途权限、数据分类、所有者和任务版本。
4. Router 过滤不符合约束的部署，再选择 Provider。
5. Provider 推理；应用校验严格 JSON、封闭 schema、模板一致性或证据引用。
6. 记录调用元数据与审计哈希，返回候选结果。
7. Blackboard 比较任务版本、期限和状态，仅向 `llm_candidates` 提交仍有效的结果，标记 `authority="NONE"`。

模型不会创建或批准 BankActionIR，不会修改账单统计、产品筛选或现有推荐理由，不控制 Scheduler。输出格式合规不代表事实正确，影子候选没有业务决策权。

模型网络等待不占用 `TrustaService.lock`。等待期间任务发生授权、取消、步骤变化或其他更新时，旧响应被丢弃。单个 request_id 不能重复推理或再次提交已经接受 / 丢弃的候选。

## Provider Interface

统一操作：`describe_capabilities`、`infer`、`health`。Provider 接收投影 messages、输出 schema、输出预算与传输期限，不接收银行工具、数据库或执行身份。

| 适配器 | 能力与边界 |
|---|---|
| MockProvider | 显式离线样例，不代表真实模型理解质量 |
| OpenAICompatibleProvider | Chat Completions JSON 协议；原生结构化输出需配置确认 |
| HTTPAdapter | 配置 model / messages 字段与嵌套响应文本路径；其他原生协议实现 Provider |

第三方、企业私有与本地模型通过对应适配器接入，不是所有原生 API 都能仅靠字段映射工作。当前不实现 Responses、流式输出、SDK 绑定、自动 function call、工具循环或任意脚本映射。模型返回工具调用会被拒绝。

`health` 仅返回配置状态和 `network_checked=false`，不在启动时付费调用模型。真实部署可用性由实际调用与部署验证确定。

## 配置与替换

```powershell
Copy-Item -LiteralPath config/llm.example.toml -Destination config/llm.toml
# 编辑 config/llm.toml，将 mode 改为 "shadow"。
$env:TRUSTA_LLM_CONFIG = (Resolve-Path -LiteralPath config/llm.toml).Path
.\start.ps1
```

未设置配置路径时默认 disabled。也可在 `.env` 设置 `TRUSTA_LLM_CONFIG`。关闭时不执行推理、不产生调用记录。配置变更在服务重启后生效；已有动作、授权和合同不重写。用途路由与模型部署分开配置，Agent 不写死模型名称。

真实 Provider 使用管理员配置的完整 endpoint，模型标识由部署配置决定。Bearer 密钥只从 `api_key_env` 指定的环境变量读取。URL 禁止内嵌凭据与查询参数，不接受用户或模型提供 endpoint。远程必须 HTTPS，明文 HTTP 仅允许回环地址。适配器不跟随重定向，不自动使用环境代理。

真实 Provider 接收非公开数据前必须配置 `data_policy_reviewed=true`，表示管理员已核对位置、保留策略与数据处理条件；该字段不是对供应商行为的技术证明。基础文本脱敏不能识别任意自然语言秘密。

## Router

决策维度：用途、复杂度、敏感级别、安全级别、输入容量、响应期限和可选成本预算。

硬过滤先检查用途、数据级别、部署安全级别、复杂度、上下文、配置的预计延迟和估算成本。请求文本、Finance 和 Wealth 投影最低 restricted；模板计划最低 internal，调用方不能降低为 public。

显式 `[routes]` 保持该用途的部署顺序；否则按 priority、预计延迟、估算成本和名称排序。成本使用配置单价及 UTF-8 字节估算输入预算，不是实际计费金额；单价未知时不能声称满足成本上限。预计延迟由配置提供，实际耗时另行记录，尚不自动调整路由。

只对超时与临时不可用进行有界回退，默认最多两次尝试，上限三次。备用部署必须满足同样的数据与安全约束。认证错误、拒绝、工具调用、格式错误、截断和证据错误不自动回退。推理回退不会重试金融写操作。

## Blackboard、持久化与通信

- 新任务增加 schema_version、revision、llm_call_refs、llm_candidates。
- Blackboard 更新、计划安装和 Scheduler 迁移递增 revision；旧任务缺失版本时按 0 处理，不重写动作哈希。
- 财务字段按现有模块职责限制写入；身份、任务类型、DAG、版本与候选字段不能通过通用 update 修改。
- 增量新增 `llm_calls` 表与索引，不修改已有金融记录。
- 调用元数据包含用途、配置版本、排除原因、尝试、成本估算、有效用量与耗时。原始提示及响应体不进入调用表或审计；验证后的候选另存于所属任务。返回的模型标识只有与配置一致时记录，否则为未知。
- 调用完成、候选接受 / 丢弃各自与其审计事件使用同一 SQLite 事务。
- 初始化时将超期 PENDING 调用标记失败，不重发；其他实例未到期的调用不会被取消。
- Registry 增加推理用途权限，原银行 Capability Grants 不变，未新增 Agent。
- Agent 消息先校验签名、载荷、nonce 与合法场景通信方向，再写入共享结果。

旧消息格式、算法及可信进程内失败阻断行为保留。`accept_message` 不是公开的外部消息入口；载荷引用版本、密钥版本与隔离身份留到 Phase 3.3。不能据此宣称具备外部 Agent 安全通信。

## 新目录与修改清单

```text
backend/llm/
  __init__.py
  configuration.py
  contracts.py
  provider.py
  gateway.py
  router.py
  projection.py
  validation.py
  providers/
    __init__.py
    mock.py
    http_adapter.py
    openai_compatible.py
config/llm.example.toml
tests/test_llm_gateway.py
tests/test_model_router.py
tests/test_llm_providers.py
tests/test_phase3_scenarios.py
scripts/phase3_demo.py
docs/phase3-model-agnostic-platform.md
docs/phase3.1-demo.json
```

修改现有文件：`backend/config.py`、`backend/persistence.py`、`backend/audit.py`、`backend/service.py`、`backend/agents/registry.py`、`backend/runtime/blackboard.py`、`backend/runtime/scheduler.py`、`backend/security/signature.py`、`.env.example`、`.gitignore` 和关联说明文档。

技术决策：保留标准库、单进程与 SQLite；使用协议适配器隔离供应商；确定性 Router；封闭用途 schema；影子结果独立存储；暂不增加 Memory、向量库或消息中间件。

## 测试与三个 Demo

```powershell
python -m unittest discover -s tests -v
python -m scripts.phase3_demo --output docs/phase3.1-demo.json
```

Demo 使用隔离持久化 Virtual Bank 与真实 HTTP 银行接口，推理部分为明确标注的离线样例：

- TRANSFER：offline-a 创建待确认动作；切换 offline-b 并重启；再次影子推理后，以原哈希授权。最终一笔交易，余额 800000 分，借贷各 200000 分，VERIFIED。
- BILL_ANALYSIS：2026-09 净支出 542500 分，候选解释不覆盖统计。
- WEALTH_RECOMMENDATION：银行风险等级 R2，筛选 sandbox-r1 / sandbox-r2，没有购买或赎回。

四次影子调用完成，任务和业务结果重启后保留，审计链有效，只读场景余额不变。本地 HTTP 协议服务器验证兼容与映射适配器、超时、429、拒绝、工具调用、截断、重定向及大小限制。测试结果见 `validation.md`。

未调用真实模型或使用真实密钥，未验证供应商实际模型质量、部署延迟或条款。API 协议测试与真实模型评估必须分开报告。

## 后续与限制

Phase 3.2 实现 Conversation、LLM 辅助意图与模板候选计划，以及受业务事实约束的解释。Phase 3.3 实现类型化 Memory、版本化消息、密钥轮换和默认关闭的扩展接口。

仍为可信单进程与沙箱身份。HTTP 有 socket 超时及读期限检查，迟到结果不会提交；DNS 与底层 I/O 的实际中断可能晚于推理预算，未实现强制进程取消。不可信插件 / Provider 代码需要未来进程隔离。长期 Memory、生产多租户认证和真实供应商端到端评估尚未实现。
