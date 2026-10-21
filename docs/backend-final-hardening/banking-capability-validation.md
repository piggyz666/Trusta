# 银行与六业务能力
当前本轮 LOCAL_VERIFIED：Banking Task 34/34；真实虚拟银行任务完成 23/23；Security 45/45，未经授权执行 0/45。six-scenes-http.json 六场景均通过，11 个真实回执/Merkle 证明；定时任务重启后再验，重复 tick 写入 0。aa-demo.json 三位参与者独立审批，收款 100.00 元，3 个回执及证明。AA 部分付款/拒绝/过期/重复等旧生命周期断言在本轮全量测试中重新通过。

官方 Revolut 历史证据保持原样：docs/final-backend/bank-live-read.json 的 5 账户与 GBP 账单；bank-payment-readback.json 唯一 0.01 EUR Acme Corp. 官方 Sandbox SETTLED 读回 14/14。本轮按指令未重复账户读取、历史支付或模拟结算。历史证据 LIVE_TESTED，不能当作本轮新远端联调。生产银行真实资金 NOT_IMPLEMENTED/本轮禁止。

源码/离线回归覆盖 Provider PENDING/UNKNOWN/SETTLED 映射、401 refresh 只重试 GET、限流/超时、币种精度与消费排除、外部未知写去重/对账、Manifest 支持判定、Sandbox/Production 隔离。原 Adapter/SDK/Agent 代码没有改。无授权财务状态变化的攻击检测持续通过。

Revolut 当前 manifest 不提供 AA、理财买赎、卡/订阅资金写能力；它们外部能力 BLOCKED_EXTERNAL/UNSUPPORTED，接口前拒绝，不能模拟成功。对应 Virtual Bank 已有六场景实际状态闭环。新增跨场景工作流仅 Virtual Bank CNY 月度订阅；预算不执行理财购买。

语言基准 HN11 POLICY_BLOCKED 是产品最低申购额规则的正确保护，不能为了预设成绩放宽。泛化模型质量与银行安全得分分开。
