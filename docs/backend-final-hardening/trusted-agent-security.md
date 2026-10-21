# Agent 可信通信与证据复核
状态 LOCAL_VERIFIED。实际 Windows Finance AppContainer、Broker 权限攻击与恢复重新运行 31/31、0 skip。输出见 agent-communication-security-benchmark.json 和 agent-communication-recovery-report.json。

仅 GENERAL_GRAPH Finance read/summary/anomalies 走隔离子进程。broker 锁定 launch identity、request/step/user/inputs，独立比对银行读取/Finance 计算结果；提交后复用有封印结果。宿主保管签名器，子进程不持银行 token、私钥或写权限。recurring 路径和其他 Agent 仍宿主执行，不宣称六 Agent/HSM 物理隔离。

原 ECDSA V3 覆盖 payload hash、sender/receiver、capability snapshot、task/version、nonce、撤销与重启重放。重规划产生新任务或取消旧待批动作，旧 authorization 不迁移；消息签名不等于金融授权。DID 为已有 did:key 本地公钥绑定，Groth16 为实际开发 setup，不是外部身份网络/生产 ceremony。

新增选择上下文同时检查独立 HMAC、owner、revision、source graph seal、期限和最新银行资源；篡改、过期、重复、竞争、跨用户、币种改变均拒绝。未弱化既有消息验证。

新 workflow-handoff.json 可用 workflow-trusted-registry.json 中经带外信任的公钥/capability 验证；workflow-record-proof.json、workflow-checkpoint.json 与 public.pem 验证同一次真实本地取消的审计包含证明。workflow-checkpoint 的 Merkle Root 非随机摘要，可作为随后逐笔批准 Commit 的业务来源。当前 root **尚未写入 Sepolia**。源读取图到子图的关联由真实审计事件携带 source_task_id/state hash；没有虚构跨进程网络协作或已完成链锚。

第三方必须带外固定 trust root，不能把随包附带的 registry 自动当信任。证明谁签了什么与因果引用，不证明隐藏 payload 全语义、生产银行清算、历史 registry 当前有效，或抵御全部 Core/issuer 密钥失陷。
