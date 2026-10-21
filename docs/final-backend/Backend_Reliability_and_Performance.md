# 运行可靠性与性能
当前本机受控结果 LOCAL_VERIFIED，非生产 SLA。

最新全量 714/714，0 skip，315.963 秒；基线 693/693，295.067 秒。模块隔离进程、2 worker 保持同一断言和 Windows 资源约束；未为提分移除测试。

现有硬化 benchmark 再执行：8 次含冷启动 Finance 进程图，P50 2640.72 ms；8 HTTP clients / 80 次身份隔离请求，80/80，P50 51.75 ms / P95 223.59 ms。SQLite 写锁真实等待后成功，审计链一致；1000 次 append + checkpoint 耗时 8778.32 ms；20 次 Receipt Proof 查询验证 P50 23.73 ms。原始样本保留，测试并行运行会带来资源竞争，不声称吞吐已优化。

31 项进程攻击/恢复：持久化 read delivery、结果落库再 ACK、限定只读重试；不自动重试金融写、不给 claim TTL 自动接管。Anchor outbox 将已知 tx/raw hash 在广播前持久化；真实不确定交易只查原 tx，不自动换 nonce。waiting/time 与 AA partial restart 测试通过；Core 默认对 RUNNING crash 保守停止，需可信人工维护，未覆盖断电文件系统损坏和分布式 failover。

实际修复为取消分派、图安装 revision 原子核对、避免外层锁跨 READ worker，以及 Demo 直接入口 import。没有新性能优化收益宣称，因此不编造优化前后加速比。原异常/失败尝试保留；最新成功证据单独存档。配置隔离、外部出站及 API 超时/限流未扩大。未来生产还需迁移/备份、负载容量、可用性策略和密钥管理评审。
