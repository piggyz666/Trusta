# 第二供应商实测：DeepSeek

2026-10-06。DeepSeek 已通过真实 API 接入现有 LLM Gateway、Model Router 与 TaskGraph Runtime。没有新增 Agent，没有改变金融执行架构。模型只生成不可信语义图提案，不能访问 Bank Adapter 或自行审批。

## 技术决策与配置

- 使用现有 `OpenAICompatibleProvider`，官方端点为 `https://api.deepseek.com/chat/completions`。没有新增专用 DeepSeek Provider。
- 本次账号模型目录实际返回 `deepseek-flash`、`deepseek-v4-pro`；实测使用前者。`DEEPSEEK_MODEL` 可替换测试模型，正式配置通过 `ProviderConfig.model` 指定。
- 增加可选 `json_output=false` 配置，开启时发送 `response_format={"type":"json_object"}`。这是 JSON 语法约束，不是 JSON Schema 保证；它与 `structured_outputs` 互斥。默认行为与原配置兼容。
- 模型不返回或调用银行工具；工具调用、截断输出和非法图仍拒绝。推理过程字段不保存为业务计划。
- API Key 只读取 `DEEPSEEK_API_KEY`，不写入代码、配置或报告。
- 新增 `config/llm.deepseek.public-example.toml`，默认 shadow、仅 public、未批准客户数据出域。默认服务推理仍关闭。实测临时配置的 restricted 审查仅针对本次隔离合成数据，不授权真实客户数据发送。
- 测试进程显式使用专用代理变量，正式 Provider 仍不自动采用系统代理。

协议依据：[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。官方 JSON 输出与 TRUSTA 本地严格校验分别承担格式约束和安全约束。

## 数据流

合成请求及输入引用名 → Gateway → Router → 配置选择 DeepSeek 或 Gemini → 不可信 JSON 候选 → 原 TaskGraph Validator → 原 Security Expansion Compiler → Blackboard / Scheduler / Business Agents。

输入值仍留在本地。请求目标文本本身可能包含用户输入，因此客户目标始终按 restricted 路由，不能因回退而降低敏感等级。

金融提案继续展开为 BankActionIR → Schema / Capability → Policy → Authorization → Execution Contract → Tool Gateway → Bank Adapter → Verification → Receipt / Audit。模型不生成可信审批、签名或执行合约。

## 实测结果

复用已冻结的 `taskgraph_holdout_v2.json`，每例一次真实 DeepSeek 请求；没有修补模型输出或改变 gold。该集合用于供应商回归，不能宣称是新独立 Gold 或任意自然语言准确率。

| 场景 | 达到预期 | 实际结果 |
|---|---:|---|
| 正常只读 | 10/10 | 账单、理财及账户查询完成 |
| 金融提案 | 2/2 | 转账及冻结卡停在 WAITING_USER |
| 缺参澄清 | 4/4 | 本地生成问题，不安装执行图 |
| 越权请求 | 7/7 | 阻止，不安装执行图 |
| 不存在资源 | 1/1 | 银行权威读取拒绝 |
| 合计 | 24/24 | 24 次真实返回，资金写入 0 |

7 项路由场景全部通过：DeepSeek 优先、Gemini 优先、两个方向故障回退、非法输出不回退、敏感数据不能回退到 public、shadow 不安装计划。其中 5 项有真实模型返回；两项应在本地直接阻止。故障由本地测试替身注入，不能称为观测到了远端限流或宕机。

共 30 次真实推理：预检 1 次、DeepSeek 回归 24 次、路由测试 5 次。所有使用临时虚拟银行，无用户金融审批，无资金写入。usage 保存模型实际返回的 tokens；没有验证价格，因此实际费用为未知。

上一轮 Gemini 23/24 与本轮 DeepSeek 24/24 来自不同时间、不同供应商格式能力，属于历史回归参考，不能据此判断模型总体优劣。原 100 条自然语言意图评测及其指标保持不变。

## Demo 流程

1. 账单：交易读取 → Finance 消费总览 → Supervisor 报告，状态 COMPLETED。
2. 理财：可读取权威风险画像与产品库 → Wealth 只读推荐 → Supervisor 报告，状态 COMPLETED。
3. 转账：明确输入 → Transaction 转账提案 → 固定可信链展开 → WAITING_USER，独立授权前无转账。

以上均有真实 DeepSeek 捕获记录，见回归报告 F01、F05、F11。路线 Demo 用 F01 验证两个方向切换及回退。

## 文件与验证

复现时先在本机环境配置凭据，再运行以下入口。它们仅使用隔离合成银行数据；每次运行会产生真实模型请求和新的本次结果，历史比较前应先另存已有报告。

```powershell
$env:DEEPSEEK_MODEL = "deepseek-flash"
python benchmarks/second_provider_evaluation.py --mode smoke
python benchmarks/second_provider_evaluation.py --mode regression
python benchmarks/second_provider_evaluation.py --mode routes
```

routes 还需要本机 `GEMINI_API_KEY`。`DEEPSEEK_MODEL`、`GEMINI_MODEL` 只影响测试模型选择，不改变 Agent 或金融权限。测试额度与期限由现有 Gateway 控制，Router 回退仍共享请求截止时间。

修改：`backend/llm/configuration.py`、`backend/llm/providers/openai_compatible.py`，仅增加通用 JSON 输出选项。

新增：配置示例、`benchmarks/second_provider_evaluation.py`、`tests/test_second_provider.py`、`scripts/second_provider_report.py` 及本次报告。原源码快照保存在 `data/phase6-second-provider-before/`。

验证入口与证据：

- [DeepSeek 逐例实测](phase6-second-provider-regression.json)
- [双供应商路由与故障注入](phase6-second-provider-routes.json)
- [全量回归日志](phase6-second-provider-tests.txt)
- [最终验证及捕获重放](phase6-second-provider-validation.json)

最终全量 **486 项测试通过**，新增9项检查。另在当前 Core 重放 24 份捕获候选，原状态与原 gold 一致、无用户授权、无写入，两项金融提案保留完整安全链。重放不新增模型调用。原 Validator / Compiler 及此前 22 个原始测试文件保持一致。

## 仍未完成

官方银行 Sandbox 真接通、Sepolia 真上链及独立 read-back 仍待外部配置和验证。DID / ZKP 仍为扩展接口，生产密钥隔离、真实身份 / KYC 仍待实施。这次完成的是第二供应商实测与替换 / 回退验证。
