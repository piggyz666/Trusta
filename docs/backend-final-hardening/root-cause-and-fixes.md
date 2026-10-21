# 根因、修复与架构变化

1. `backend/llm/grounded_request.py` 原入口整体拒绝“如果”，只支持单月规范措辞。底层已有 WAIT_USER/条件和多读 DAG，但规范化层没有对应表示。增加严格的“如果我选择继续，则…”选择条件和最近三个月完整自然月读取；天气、余额比较等没有权威条件来源的表达继续澄清。金融金额仍来自用户原文，不猜测。
2. `backend/llm/compact_graph.py` 原紧凑 calls 精确字段只有 node_id/function/parameters/dependencies，没有 condition。这是协议表达范围问题，不是模型不理解。对已规范化的条件请求通过 Gateway 的单次 format 参数选择既有 full_v1。全局设置、默认 compact_v2、注册 DSL、节点上限、金融展开不变。没有引入第三套协议。
3. `matches_request` 原 condition 直接按对象相等比较，重命名等价节点时会误拒绝。现在根据生产者对应关系核对引用；equals、来源和先后依赖严格保留。不同条件仍拒绝。
4. 新 `backend/llm/subscription_workflow.py` 复用 Finance recurring、Service resolve 和 Wealth budget_plan。三个月账单与银行历史、订阅读取汇合；只列出近期证据足够、唯一权威匹配的 CNY 月度可取消订阅。不替用户判断“没必要”。用户明确选择并给出基础预算后，生成新的取消提案；原 Policy/Authorization/Contract/Gateway/HardVerifier/Receipt 决定何时可执行。取消被拒绝时节省金额为 0，不伪造回执。
5. 两阶段采用“已完成只读图 + HMAC 封印的待选择上下文 + 新金融图”，而不是动态改写运行中 action inputs。显式 revision、用户绑定、30 分钟有效期、一次消费、实时资源快照复核；子任务审计关联源 task/state hash。选择不是授权，新任务不继承旧 consent。查询并行、金融动作串行，保持旧编译器。
6. 独立 RPC 首次 latest 区块读取偶遇尚不可用的区块，改为已确认高度进行准备期连通性检查；失败尝试保留。Pocket Network 与 Infura 的规范区块、真实交易/回执及非空代码一致，已配置本地独立读取入口。最终验收仍使用原验证器对实际交易确认深度与 reorg 检查，不用准备期样本替代。
7. 新协调层初版仅依赖 Service 的进程内 RLock，两个 Service 实例可各自接受同一源选择并创建两个待批提案。selection-race-before-fix.txt 保存实际失败（2 != 1）。现在复用既有数据库 GraphClaims，对源 task 跨实例互斥；创建子图前先持久化 consumed=true。创建失败/中断后不自动重开选择；明确停止并需重新发起只读分析。专项竞争与故障测试见 selection-race-after-fix.txt。只有未授权提案曾可重复，未出现自动执行金融动作。

新增 HTTP：POST `/api/workflows/subscription-budget` 只接受 text/account_id；POST `/api/workflows/{source_task_id}/select` 只接受 revision/text。随后继续原 `/api/graphs/{child_task_id}/authorize`。客户端 user_id 字段不能覆盖认证身份。

修改/新增文件精确列表见 source-changes.json。现有六 Agent、TaskGraph Validator/Compiler/Runtime、ECDSA/DID/Groth16、Merkle、银行适配器及 Solidity 源码保持本轮原始哈希。未修改任何原有测试断言。
