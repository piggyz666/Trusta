# Finance 跨进程传输与密钥隔离

实现位置：`backend/runtime/windows_container.py`、`finance_worker.py`、`finance_transport.py`，集成于 GraphRuntime，不增加 Agent。适用 Windows + CPython 3.13 标准安装。本轮 Windows 11 build 26100 / Python 3.13.1 实测；Linux、其他解释器布局尚未提供隔离实现。

Python Config 默认兼容；启用 `TRUSTA_FINANCE_PROCESS_ENABLED=true`，或 `Config(finance_process_enabled=True, banking_version=5, communication_version=3, ...)`。超时 `TRUSTA_AGENT_TIMEOUT_SECONDS` 1–120 秒，默认20秒。显式启用但平台/配置/容器失败时阻止任务，不回退宿主 Finance。创建 Service 不代表已实测隔离，只有 OS Token 与实际攻击结果可以支持该声明。

进程运行于无 capability 的 Windows AppContainer。专用 staging 位于当前 LOCALAPPDATA 的 `TRUSTA-Agent-Runtime/finance-v1`；创建固定配置的 AppContainer profile，ACL 仅授予该 SID 对 staging 的 RX，宿主用户/SYSTEM/管理员保留管理权。宿主读取原 Finance 源码并原子更新 staging。解释器不载入 site-packages，不继承项目搜索路径。Child Job Object 限制一个进程、256 MiB 内存，父方关闭即终止；继承列表只含 stdin/stdout 与 NUL stderr。环境白名单不含 LLM、银行、API secrets。

允许：accounts.read / balances.read / transactions.read / finance.summary / finance.anomalies。固定宿主 broker 绑定运行中节点、Finance 身份、客户归属、真实参数、任务版本和 Supervisor V3 请求；纯分析仍重算核对。最大 IPC 包1 MiB，超时/非法响应关闭进程；银行读和 IPC 不跨越 SQLite 写事务。

交付登记为 HMAC 封存的 `agent_delivery_jobs`：PENDING → DISPATCHED → RESULT_COMMITTED → APPLIED。请求 nonce、分发审计与 outbox 同事务；返回 nonce、结果、handoff evidence、审计与 APPLIED 同事务。已提交结果在 ACK 前可复用；完成节点不重新进入 worker。中断的只读分发最多重试一次，不确定金融执行不进入此表。正常 Core 重启终止运行任务；恢复测试中关闭自动恢复的 Service 是显式维护环境，不是默认自动续跑功能。

实测30项专项（含模型协议与证据验证），实际 Child 拒绝读取其他 Agent key、宿主 DB、Adapter 源码、导入 Adapter、访问宿主进程内存、连接已验证可达的宿主监听端口、写 staging；没有通用签名/写 RPC。nonce 重放、签名/接收方/版本/父引用/主体篡改、存储篡改、轮换撤销、进程失败与重试耗尽均测试。详见两个通信 JSON 和完整回归日志。

密钥在可信宿主，未发送给子进程；不是 KMS/HSM，也不防已攻陷 broker、管理员或内核。其他五 Agent、Finance 旧场景及 recurring 尚为宿主路径。staging 与 AppContainer profile 是本机生成的部署资源，复制仓库不会复制可信 profile；不应把运行目录与秘密放在同一共享 ACL 下。
