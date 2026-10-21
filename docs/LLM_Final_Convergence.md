# TRUSTA LLM Model-Agnostic Final Convergence

## 最终判定

| 维度 | 结果 | 范围 |
|---|---|---|
| ENGINEERING | PASS | 原 1048 项保留；当前 1090 项通过，新增 42 项，无失败/错误/跳过 |
| PROVIDER COMPATIBILITY | PARTIAL | 三类协议的本地配置化切换通过；新参数在真实远端模型上的复验未运行 |
| REAL MODEL ROBUSTNESS | NOT_RUN | 本轮新预算未授权；历史真实模型仍 PARTIAL，未改旧分数 |
| EXECUTION ALIGNMENT | PASS | 原执行链、本地合成业务与已保存真实响应回放，独立证明通过 |
| SECURITY INVARIANTS | PASS | 受测范围未发现越权写入；Validator/金融执行/审计边界未修改 |

不是总 PASS，也不宣称三种模型经过修复后的真实任务完成率相同。新付费调用 0，费用 0；原 v2.1 的 60 次/USD0.93405 预留账本保持原状。网络状态未知保留 null，真实攻击泛化仍 NOT_RUN。

## 根因与处理

根因矩阵见 data/llm-final-convergence/root-cause-matrix.json，包含症状、条件、代码位置、证据、修复和验收结果。

DeepSeek 部分完成预算完全用于 reasoning：11 份存档记录 reasoning_tokens=768。不能据此认定它无法规划。评测默认输出容量改为 2048，可配置至 4096；不会在截断后自动加预算或重试。Chat Completions 的总输出可能包含未单列的格式 token，因此 completion 减 reasoning 记为 non_reasoning_tokens，不伪称精确可见文本 token 数。参数语义核对了 [OpenAI 官方接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 和 [token 计数说明](https://developers.openai.com/api/docs/guides/token-counting)。

GLM 的 18 份保存响应完整结束，输出与冻结 Schema 的结构一致，只有 $schema 声明被写成 https://-schema.org/draft/2020-12/schema，故不能称为逐字段完全相等。原始 exact-echo=false 保留；新增诊断字段仅说明除该声明外的结构一致。JSON mode 保证语法，不保证任务语义。没有发现可通过无损响应解析恢复的 TaskPlan。原 Prompt、Gold 与 Validator 均未改，也没有启用未经外部验证的 native Schema 来宣称修复成功。

新增协议配置 token_parameter 只控制输出预算字段；不按品牌分支。既有 OpenAI 映射本来正确，不能把这个增强虚称为旧 DeepSeek 失败的已证实根因。支持范围为非流式 Chat Completions 与 Gemini generateContent；Responses、其他原生协议及特殊 Schema 方言需要独立适配，不能宣称任意模型只填 Key 即支持。

已修复无凭据远端请求、防止原生 Schema 输入费用漏算、等价 Purpose 顺序误拒绝，以及本地模型凭据回显遗漏；原请求截止、显式代理、受限 Fallback 和独立进程隔离复用。Purpose 按集合比较仅消除排列差异，范围增减仍拒绝。Schema、拒答、工具调用、金融参数错误不触发跨 Provider Fallback，不增加同 Provider 结构修复机制。直接同步 HTTP 的 DNS 阻塞仍受操作系统行为影响；现有评测进程截止能终止它，且迟到响应不得执行。这是运输环境限制，不包装成实时模型验收。

## 真实证据与回放

原 60 行真实评测与原分数未修改。49 份原始协议 payload 可回放，其余 11 份缺少 payload，标记 NOT_RUN，未补造。raw 使用 768，stabilized 使用 2048，在两套新目录分别通过现有 Gateway/Planner/Validator/Runtime/Virtual Bank。每个请求与原 frozen-requests 的 prompt Hash 相等，原响应不改；两套评分一致。提高回放预算不会延长已截断的历史响应，所以不声称模型表现提高。

| Provider | 可回放响应 | 回放完整目标匹配 | 回放独立证明 | 目标与证明同时通过 |
|---|---:|---:|---:|---:|
| deepseek | 18 | 0 | 2 | 0 |
| glm | 18 | 0 | 0 | 0 |
| gemini | 13 | 2 | 3 | 2 |

这里是本地回放指标，与原真实分数属于不同证据范围：DeepSeek 回放能生成 2 份新本地执行证明，二者均未满足完整任务意图，不能把它们添加到历史真实调用得分。Gemini 的 3 份回放证明只有 2 份符合完整目标。等待逐笔授权不是语义澄清，所有 WAITING_USER 单独记录为 waiting_for_authorization。

本轮又运行冻结的 20 正常/20 攻击离线验收，受控本地业务全部一致，攻击越权率 0。真实模型攻击输入未发送，不标 PASS。独立 CLI 新进程共验证 53 份证明：23 份历史证明、20 份本地正常业务、10 份分属于 raw/stabilized 的真实响应回放；后 10 份对应 5 个存档响应，不是 10 次新模型成功。

## 变更及复验

新增 config/llm-convergence.example.yaml、llm_evaluation/convergence.py、tests/test_llm_final_convergence.py。示例默认 disabled，凭据只用环境变量引用，敏感度/Purpose/预算/超时仍由原 Router 控制。已有源码变化清单见 acceptance.json；没有第二套 Provider、Gateway、Runtime、Planner 或执行器。

配置化接入测试通过相同业务代码完成三种协议的余额和转账场景，真实 Virtual Bank 付款仍需独立受信测试控制器核对完整意图及逐笔 Action Hash；模型没有银行实例或用户授权权限。原 BankActionIR、SecurityExpansionCompiler、Policy、Authorization、Execution Contract、Tool Gateway、HardVerifier、ECDSA/Merkle/独立证明均复用。

全量日志 full-regression.log 与 JUnit full-regression.xml；专项 targeted.log；逐例 archive-accepted/cases/{raw,stabilized} 与 offline-final/cases；历史源文件/评测/Prompt/响应 Hash 清单 baseline.json。未覆盖或删除失败 case。原银行、六 Agent、Sepolia、MetaMask、Anchor、Validator 与 Audit 源码均不变。

Ruff F 无问题，编译/pip check 通过。Bandit 4 条原始发现已复核为误报：SafeLoader 子类被误识别为不安全 YAML，公开协议枚举被误识别为密码；原扫描与复核理由均保留，未通过屏蔽扫描获取通过。测试和扫描不能证明不存在未知漏洞。

运行入口（不发送外部请求）：

```powershell
python -m llm_evaluation.convergence --output <新的本地输出目录>
python -m alignment.verify --proof <公开 JSON> --pinned-key <可信 PEM> --expected-plan-hash <计划 Hash> --expected-request-hash <请求 Hash>
```

不可公开 banks/ 私有密钥或数据库。证明及公钥 pin 应从可信渠道分开传递。

本轮 LLM 架构内可确认的协议/凭据/容量配置/费用预留缺陷已经修复并验收。剩余外部阻塞是新的独立 API 预算；没有新预算不发送请求，也不擅自提高旧额度。GLM Schema 复述和模型语义差异如实保留，不能以安全拒绝充当业务完成。新实时正确率、网络可靠性与真实攻击泛化均不作保证。
