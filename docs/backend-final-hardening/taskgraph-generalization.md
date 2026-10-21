# TaskGraph 能力与边界

新增 LOCAL_VERIFIED：自然语言最近三个月读取、显式用户选择条件、跨 Agent 订阅→预算的多轮选择；在已有 32 semantic nodes、20 depth、16 dependencies、64 explicit input refs 内运行。条件引用必须指向祖先 WAIT_USER/BRANCH/DECIDE；金融节点仍只接受本地显式 input，Compiler 注入全部八个安全阶段。

`subscription-budget-demo.json` 来自当前代码实际运行：原始长句 → 三月读取与汇总、历史候选和银行 subscription.list → 唯一 Service 匹配 → 显式“取消演示视频，下个月基础预算一百元” → 新 Cancel 图停在 WAITING_USER → 重启 → 独立批准 → verified receipt → 119.99 元预算。19.99 元节省来自核验过的取消对象；预算规划不是购买、收益承诺或实际增加余额。CNY 月度订阅为当前支持范围。

条件例：“如果我选择继续，则给张三转一元”。第一次停在真正 WAIT_USER；signal=true 后仍需独立金融授权；false 完整跳过金融展开，没有动作、授权或资金写入。模型等价节点名通过，条件反转拒绝。条件请求使用既有 full_v1；compact_v2 不被伪造为支持条件。

原 ProposalDialogue 补槽、Conversation 补充金额/修改/撤销、定时执行到期再验、图取消与部分完成回执保留继续由旧模块处理。60 项变更面回归含这些原始断言；跨 Service 实例竞争提交只生成一个待批动作。持久化一次消费发生在子图创建前；中断后不自动重建金融提案。

局限：订阅组合是可审计的有限规范化工作流，不是任意语言规划器；“没必要”由人选择。通用自然语言 AA 混合组合、天气/自由银行事实比较与随意改单条已执行计划仍不实现；已完成 AA API 不受影响。未运行新实时模型或 holdout，不宣称无界泛化准确率。
