# TRUSTA V1 后端深化：实施前审查（2026-10-08）

## 复现与源码证据

本轮从当前文件系统重新执行 42 个 unittest 模块（每模块独立进程，4 个测试进程并发）：609 项 / 220.575 秒 / 0 失败 / 0 跳过。原始日志 `backend-hardening-baseline-tests.txt`，逐模块记录 `data/hardening-baseline-result.json`。Windows 11 build 26100、CPython 3.13.1；工作目录没有 Git 元数据，使用 `data/hardening-before.json` 的文件 SHA-256 作为修改前标识。测试进程并发不是 Agent 进程隔离证据。

已完整读取用户两份报告。报告中的风险大体成立，但下面三项不能照搬：普通同用户 subprocess 不能称为密钥隔离；接收事务不能持有数据库写锁等待 Agent/LLM；签名与 Merkle inclusion 不能单独证明任务依赖或银行事实正确。

## 五个域的判断

1. **LLM/TaskGraph**：`llm/graph_protocol.py` 要模型填写 11 个字段，参数 schema 与 capability 之间没有判别绑定；节点名、重复 refs、owner 与参数组合容易出错。`graph_proposal.py` 单次受约束推理，`taskgraph.py` 验证来源、祖先依赖和权限，Compiler 生成金融安全节点，`graph_runtime.py` 再做银行 grounding。这些验证不能放宽。新增紧凑函数提案格式，确定性生成重复元数据，再调用原 Validator；保留完整 V1 接口。不能从模型文本猜测金额、收款人或银行记录。新开发集和冻结 holdout 独立于历史评测，结果记录所有请求，拒绝不等于完成。
2. **通信**：`service.py::_agent_message` 中 nonce/audit 接收先提交，agent_results 后提交，存在崩溃间隙。`CryptoIdentityRegistry.store` 在可信宿主中缓存全部私钥；六 Agent 目前是同进程对象，线程池只提高只读并发。新增事务性 inbox/outbox、固定 Finance 子进程和窄签名 broker。Windows AppContainer 不授网络能力，只允许读取单独 staging 的解释器与 Finance 代码；不向其传递数据库、配置、其他 Agent 私钥或 bank token。无法创建容器时明确失败，不能悄悄回退。
3. **协作证据**：现有 audit event 只有 payload hash，V3 验证身份/nonce/版本，Merkle 验证 inclusion；尚无版本化因果包连接具体节点、父证据、输入输出和权威读。由 Runtime 生成 evidence，绑定现有 V3 和 audit hash；离线工具要求调用者提供可信公钥/registry 快照，不接受包内自报公钥为根。
4. **外部集成**：当前 Revolut 证据属于官方沙箱，不能提升为生产银行资金接入，也不能用模拟 SETTLED 覆盖原 MANUAL_REVIEW。本轮只核查历史证据，不重新付款。Sepolia RPC/合约/钱包环境变量当前缺失；外部链独立列 blocked，保留已有 EVM/RPC 实现及本地测试。
5. **可靠性**：SQLite WAL/BEGIN IMMEDIATE、Core 银行幂等已存在。GraphRuntime 的 `_gates` 和全局 RLock 仅在单实例生效；需要数据库竞争控制、故障注入和重启验证。任务版本必须在接收/落盘同一事务内核对；外部写不确定继续人工复核，不用自动重试提高完成率。Merkle 的全链扫描成本需以真实样本报告，不能凭小样本宣称生产容量。

历史 `phase6_llm_metrics.py` 已有统一重算修正逻辑。复核当前 `scripts/phase6_reports.py` 后确认它已使用 candidate_validation.raw_unsafe，并说明候选标记不等于危险执行；不需要再次修改这个生成器。实施前怀疑被源码证据排除。保留历史捕获记录，单独给出复算审查，明确区分 envelope 字段、候选不合法/grounding 与真实未授权执行。

## 取舍、故障模式与实施顺序

冻结六 Agent、Blackboard、V3、BankActionIR、Policy/Authorization/Contract/Gateway/HardVerifier/Merkle；增加 transport/evidence 模块而不重造资金链。Finance 首先接入 GENERAL_GRAPH 的 READ 和纯分析节点，其他业务保留原路径。默认兼容原单进程配置；显式进程模式必须失败关闭，部署可关闭新模式但不能仍显示隔离成功。

实施分四批：原子 handoff 与 evidence；AppContainer + Finance broker + DAG 纵向切片；紧凑模型提案/开发集/新冻结 holdout；攻击、恢复与负载基准、既有 benchmark、全量回归和交付。重点验证错误接收方/版本/签名/Capability、nonce 重放、重复/丢 ACK、接收前后崩溃、旧 key/撤销、恶意父引用、双 worker、子进程读取宿主密钥/DB/调用写工具，以及跨客户和并发授权。

可信宿主仍管理签名、Gateway、银行凭据和数据库。AppContainer 防护范围是子 Agent 对宿主资源的越权，不覆盖宿主管理员/内核/可信 broker 被攻陷，不等于 HSM。DID/ZKP 保留当前真实性等级（P-256 did:key；单机开发 Groth16 setup、本地银行事实签发），不作为授权渠道。

Windows API 依据：[Microsoft AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)、[进程安全属性](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[CNG ECC 公钥导入](https://learn.microsoft.com/en-us/windows/win32/api/bcrypt/ns-bcrypt-bcrypt_ecckey_blob)。实际平台支持与攻击结果以之后实测为准。
