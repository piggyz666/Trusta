# TaskGraph 泛化与证据
原受约束 Compiler/Runtime 为 LOCAL_VERIFIED；明确 NL 顺序组合为 LIVE_TESTED；开放式条件 NL 仍 NOT_IMPLEMENTED。

新 POST /api/graphs/from-text 接收 text/account_id，通过非 Agent 确定性分句及中文金额/资源归一化后，给 Supervisor/Planner 的 LLM 只有目标、输入 reference 与类型/分句元数据。候选必须匹配完整操作数、Agent/capability、输入来源、结果来源和先后依赖，才能装入 Blackboard。

新增 /api/graphs/dialogue 与 /api/graphs/{id}/continue 复用原 ProposalDialogue：closed fields、实际 HTTP 身份、revision、seal、允许缺失字段白名单、补槽单次消费，新提案不继承 authorization。一般图修改采用取消未执行部分并产生新提案；已完成不可逆动作的 Receipt 保留。不是随意修改运行中金融图。

原注册图支持并行 READ/JOIN、分支、WAITING_USER、WAITING_TIME、失败传播、重启等待恢复、跨 Agent handoff、多银行动作独立审批。原 19 图测试及新 21 测试证明真实并行重叠、来源/输出/图封印、防假 capability、取消和实际两笔付款审批。18 条顺序 READ + report 的新测试说明不限两个目标，但仍遵守原 32 节点/20 深度。

phase6-taskgraph-demo.json 展示分析 → 待确认退订 → verified receipt → wealth budget，重启后批准才继续。六场景 Demo、banking-task-benchmark.json 包含复杂业务依赖。agent-communication-demo.json 与可信 registry 导出提供实际签名 handoff 证据。LLM 新六条显式多目标：DS 6/6，GLM 0/6，GLM 有合法格式却错误顺序的失败样本。

限制：NL 层拒绝条件、否定、含糊金额、未知组合；当前不能将任意人类条件准确编译到受信事实分支。年报和 AA 自身已有业务 API，但其任意 NL 混合组合不在新有限语法中。多轮图补槽是本地/HTTP 验证，真实模型多轮样本未成功，不冒充通用自然语言完成。RUNNING 异常任务默认不自动恢复资金写操作；等待/只读结果恢复有明确范围。
