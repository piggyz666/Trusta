# TRUSTA 后端缺口审查
审查日期：2026-10-09T19:44:08.228566+00:00。Windows / Python 3.13.1。证据状态与冻结策略分别记录。

未找到同名 Engineering Spec，已按实际 architecture、phase4/5/6、backend-hardening、targeted、Sepolia 文档及源码交叉检查。工作目录没有 Git 元数据，因此交付源文件前后 SHA-256 清单，不伪造 Git diff/commit。原始 693 项测试重新通过，0 跳过，295.067 秒；不是复述历史 693。

| ID / Module | Current / Evidence | Root Cause | Severity | Fix / Acceptance | Final Status / Change Policy |
|---|---|---|---|---|---|
| G01 自然语言组合 | 旧 graph API 需要先填槽位；grounded_request、真实评测 B_GRAPH | 缺少非 Agent 的分句、参数来源、候选语义核对入口 | P1 | 新入口；六条 DeepSeek 真实任务通过，原编译器未放宽 | LIVE_TESTED / 新层 |
| G02 DAG 取消 | service.cancel_draft 原先将 GENERAL_GRAPH 送入旧业务授权路径 | 任务类型分派不完整 | P1 | 专用取消、claim/revision 检查、保留已完成回执；21 项新增测试 | LOCAL_VERIFIED / 定向修复 |
| G03 安装竞态 | 模型返回到安装之间可改变 revision | 检查与安装不是同一临界区 | P1 | create 内检查 owner、PENDING、revision；不在外层锁内运行 READ workers | LOCAL_VERIFIED |
| G04 双模型 | 46 次真实调用及完整原始 JSON | GLM 免费模型格式、语义质量弱；4.7 探测截断/429 | P1 | 可选 thinking 配置、真实 GLM 模板；未将弱模型投入可靠规划 | LIVE_TESTED 接通 / FAILED 质量 |
| G05 金额提取 | dev FC01 包含“元”而被拒绝 | 提示词未明确字段单位边界 | P1 | 补充精确格式；DS 开发样本真实复测通过；不重跑 Holdout | LIVE_TESTED 小样本 / 泛化未验证 |
| G06 DAG 多轮补槽 | ProposalDialogue 原有 SDK 已实现 | 缺 HTTP 暴露 | P2 | dialogue/continue 复用封印、身份、版本和单次消费；无授权继承 | LOCAL_VERIFIED |
| G07 第二 RPC | PublicNode、dRPC、BlockPI、1RPC 探测记录 | 当前环境返回 403/不可用；非同运营商验收未通过 | P1 | 主链检查完成；集中操作员清单，未将不同 URL 当独立运营商 | BLOCKED_EXTERNAL |
| G08 Sepolia 部署与 commit | estimateGas、代码哈希、硬保护已实现 | 缺受控本地签名器与具体 exact-plan 批准；未部署 | P1 | 仅新增本机加密 keystore 登记工具；真实签名/广播均 0 | BLOCKED_EXTERNAL / 既有安全模块 FROZEN |
| G09 银行转账历史状态 | 本轮 14/14 远端读回检查，历史 0.01 EUR 为 SETTLED | 早期 403 与待结算记录已被后续授权试验解决 | P2 | 不重复付款；区分官方沙箱模拟结算与生产结算 | LIVE_TESTED / FROZEN |
| G10 AA | 三身份 HTTP、实际虚拟账本扣增、三个回执及证明 | 外部银行 manifest 无 AA 能力，不能夸大 | P1 | 本地闭环重新验证；外部 AA 显式阻塞 | LOCAL_VERIFIED 本地 / BLOCKED_EXTERNAL 外部 |
| G11 运行和证据 | 31 攻击/恢复、80 并发 HTTP、1000 审计事件 | 无新增 Core 可靠性缺陷；仅两个 Demo 直接调用 import 失败 | P2 | 修正 Demo 根路径初始化，保留历史失败日志 | LOCAL_VERIFIED |
| G12 文档 | 旧 architecture 写 DID/ZKP 未实现 | 历史状态未同步 | P2 | 指向当前能力矩阵；did:key/Groth16 真实本地验证与链上状态分开 | LOCAL_VERIFIED |
| G13 评测记分 | 控制意图空槽位、flattened clarification | 评分器 KeyError/取错 reason 层级；不是 Provider 故障 | P2 | 单独保存评分修订，原始输出/锁定集不变、无新增调用 | LOCAL_VERIFIED |
| G14 泛化自然语言条件/自由重规划 | Core 注册 DAG 支持分支/等待；NL 新层为有限显式语法 | 业务条件需要可靠事实来源和语义设计；不能靠模型猜 | P1 | 现阶段拒绝含糊条件；列为后续能力，不宣称任意自然语言理解完成 | NOT_IMPLEMENTED 该入口扩展 |

无必须改变冻结架构的冲突。金融执行、六 Agent、ECDSA/DID/ZKP、Merkle、Solidity、银行 Adapter 没有无故重写；原测试未删除或修改。没有 P0 被隐藏为成功。GLM 质量与链上独立验收仍是发布限制。
