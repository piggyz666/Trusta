# 后端信任边界（2026-10-08）

本轮保留六个业务 Agent 与原资金执行链。模型、用户自然语言、Agent 子进程输出均不授予金融权限。可信宿主包含身份注册表、密钥 store、Blackboard、Compiler、窄 broker、原金融执行和审计组件；宿主管理员及内核不在 AppContainer 防护范围内。

```text
用户明确输入 → Conversation / Intent → Supervisor
                                      ↓
                         Gateway → Router → 可替换 Provider
                                      ↓ 未可信紧凑函数提案
原 TaskGraph Validator → 银行资源归属核验 → 确定性 Compiler
                                      ↓
Blackboard / Bus / Scheduler → 原六 Agent
                 Finance 部分节点 → 签名 REQUEST → AppContainer
                                              ↓ 窄只读请求
                       宿主 broker → Capability / Gateway → Bank Adapter
                                              ↓ 独立比对输出
                 结果持久化 → V3 回复 → 原子 nonce / audit / evidence / result

金融提案 → BankActionIR → Schema / Capability → Policy → Authorization
         → Execution Contract → Tool Gateway → Bank Adapter
         → HardVerifier → Receipt → Audit / Merkle
```

紧凑提案仅允许已登记函数、准确参数名与来源引用。展开重复元数据后继续使用原 Validator；金额、银行记录、授权、合约、执行节点均不能从模型臆造。金融审批由 Compiler 添加，WAIT_USER 选择不是付款授权。full_v1 默认兼容，compact_v2 显式配置。

Finance 子进程没有 bank token、数据库句柄、Agent 私钥或金融写代码。它通过宿主的固定 Finance READ broker 获取本次任务绑定的数据。签名由可信宿主在上下文核验后完成；没有接受任意主体、任意字节的签名 RPC。宿主独立比对权威读结果或既有确定性 Finance 算法结果，签名不把任意子进程输出变成银行事实。

证据签名证明 Runtime 的历史声明、V3 归属、节点关系与 Merkle 包含性，不证明隐藏业务载荷的真实含义、生产结算、外部链或当前授权。公钥及 capability 快照必须从独立可信渠道固定。示例和证明一起下载仅适合演示，不建立外部信任根。

SQLite 图执行竞争登记只解决共享同一 DB 的 worker 互斥，没有按时间抢占金融任务。终态登记清理由可信维护方在停止相关 worker 后进行，接口未向客户暴露；不是分布式共识或所有写路径的通用 fencing。正常重启停止 RUNNING，保留不确定金融执行的 MANUAL_REVIEW。只读交付最多两次尝试；不存在金融写自动重试。

仍在宿主内执行的其他 Agent 尚无独立 OS 边界；本轮只验证 Finance GENERAL_GRAPH 的三类读取和 summary/anomalies。不能称为全 Agent/HSM/生产密钥隔离。
