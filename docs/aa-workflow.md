# AA Collection Workflow

领域模型为 SplitBill、SplitParticipant、PaymentRequest、PaymentAction。账单与独立 payment request 落盘，参与者状态及任务/回执引用在账单 JSON 中。银行配置的 split_payee_routes 绑定真实本地收款账户，普通默认转账保留 settlement 行为。没有把资金变化写到业务工作流表代替银行借贷。

Organizer 只能创建/发布/取消账单，不能动参与者账户。每人以服务器配置的独立身份准备其所属账户的普通 TRANSFER 任务，再独立审核 action_hash。每人自己的 Policy、Authorization、Contract、Gateway、HardVerifier、Receipt、Audit 均执行；三个付款有三个任务/hash/审批/回执。付款 Guard 重新核对账单状态、参与者/任务/收款路由/金额/到期时间，直接绕到原任务审批接口也无法越过过期或取消限制。

Equal 用整数余数按参与者顺序分配；ExactAmount 用非负整数且总和精确；Percentage 用最多四位小数的十进制字符串、总百分比100、最大余数法，金额总和不变。1分三人可为1/0/0；零份额无需转账，不生成假的零金额回执。

生命周期：DRAFT → REQUESTED → PARTIALLY_PAID → COMPLETED；可 CANCELLED/EXPIRED，参与者可 DECLINED。只有所有正份额真实借记/贷记且 VERIFIED/Receipt 才完成；拒绝不减少应收总额。取消保留已收款，不自动退款；过期禁止未完成付款。重启只观察既有可信任务与银行 ledger，不重新授权或自动重复扣款。

本地 HTTP 身份映射由 create_server(identities=...) 配置并复制，客户端 user_id 被拒绝。参与者只能查看自身条目、准备自己的来源账户；不能读取/批准别人任务。该方式为多客户沙箱演示，不等于生产 OAuth/KYC。

接口：POST /api/splits；GET /api/splits/{id}；POST /publish、/prepare(source_account_id)、/pay(action_hash,approve,strong_secret)、/decline、/cancel。所有使用 Bearer 对应的服务器身份。

运行 `python scripts/phase6_demo.py`：100元三人3334/3333/3333分 → 逐人HTTP付款 → PARTIALLY_PAID → COMPLETED，收款账户实际增加10000分。完整输出 aa-demo.json；测试还覆盖百分比分配、零份额、部分付款重启、拒绝、过期、取消、重复、身份/来源隔离。外部 Starling AA 收款路由尚未配置，外部 collection 明确拒绝。
