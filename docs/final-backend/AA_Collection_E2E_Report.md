# AA 收款端到端
本地状态 LOCAL_VERIFIED；外部 AA BLOCKED_EXTERNAL。不是仅做分摊计划。

输入：10000 分，alice/bob/carol；原 split largest-remainder 分配 3334/3333/3333。三套本机 HTTP 身份分别准备动作和逐笔授权，独立 authorization_id=3。发起人不能代替参与者批准。

真实虚拟银行结果：参与者初始各 100000 分，终值 {'alice': 96666, 'bob': 96667, 'carol': 96667}；实际共扣 10000 分，发起人 1000000→1010000。Collection COMPLETED。aa-demo.json 包含动作哈希、任务和独立授权 ID、ledger transfers、三个 Receipt、签名检查点的 Merkle 包含证明与公开验证钥；本机固定信任来源，非外部链证明。

原 15 项 split 测试覆盖 1 分分配、舍入、各自授权、真实收款、partial restart、拒绝、取消已付保留、过期、参与者读取隔离、重复不扣；HTTP 并发/幂等与重启测试仍在最终全量中。没有将一次批准用于不同付款。未接支持 AA 的真实银行 Connector，不能把虚拟到账当外部收款。
