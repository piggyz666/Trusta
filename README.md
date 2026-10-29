<p align="center"><img src="frontend-app/public/icon-192.png" width="96" alt="TRUSTA"></p>

<h1 align="center">TRUSTA 银行助手</h1>

<p align="center">可信的多智能体银行 AI 助手：一句话办理转账、账单、理财、卡片和跨场景安排，也能帮你查机票、演出；<br>AI 只能提议，每一笔资金操作都由你在确认卡上授权。</p>

<p align="center"><a href="https://fintechtrusta.com">fintechtrusta.com</a></p>

---

## 它能做什么

| 场景 | 一句话示例 |
|---|---|
| 智能转账 | “给张三转500元”“每月1号给房东转3000” |
| 账单分析 | “这个月花了多少钱”“上个月外卖花了多少” |
| 理财 | “推荐适合我的理财”“根据我的消费推荐理财”“今天金价多少”“做一下风险测评” |
| 卡片管理 | “我的卡丢了”“关闭信用卡境外交易” |
| 订阅与代扣 | “我有哪些订阅”“最近有什么要续费”“取消视频会员”；周期扣费满 3 次自动识别并提醒（查询订阅属于黄色操作，先确认再显示） |
| 跨场景联动 | “下个月妈妈生日，帮我存2000买礼物”“下周去日本玩5天，帮我安排一下”“工资到账后存1000到心愿金” |
| 生活服务 | “帮我查下周五北京到上海的机票”“明天上海到杭州的高铁”“上海最近有什么演唱会”（带条件跳转官方平台，本行不交易） |

回复是**可交互卡片**：转账卡上直接改金额，账单饼图点分类看明细，理财卡选产品直接申购，卡片开关直接拨动。

除了对话，APP 还有几个独立页面：

- **资金总览**：余额、持仓、心愿金和每一笔经 TRUSTA 办理的记录，点开可看银行回执与签名证明。
- **账单与订阅**：每月固定支出、未来 30 天扣费时间线；同一商户同一金额约每月扣一次、满 3 次就识别为疑似订阅，由你确认后扣费前提醒，不认识就给出止损办法。
- **理财**：黄金与美元、欧元、日元、港币参考行情（标来源和时间，附汇率换算）；按近三个月消费留足备用金、其余按风险等级配置的“为你推荐”；19 款来自 7 家（虚构）机构的产品，高于风险等级的看得到、买不了。不荐个股，不承诺收益。
- **存款、外汇、黄金、基金、贷款与信用账户**（部分业务创意来自队友 [Mannenzakura](https://github.com/Mannenzakura)）：放在各分类页的标签里——账单与信用下的信用账单、贷款与分期；理财下的基金份额、定期存款、黄金交易与定投、外币账户。都按"银行报价 → 确认卡 → 签名执行 → 独立验账"办理，账记在银行一侧；接入的银行开放哪些业务，就显示哪些。
- **生活服务**：机票、火车票、酒店、演出、缴费、社保医保的官方入口；对话里的城市、日期会直接带进链接。

## 为什么可信

- **AI 只能提议，不能授权。** 大模型只输出候选工具调用，每个参数都要能在你的原话里找到出处，否则追问；在聊天里说“确认”不会执行任何操作。
- **三级权限（与赛题一致）。** 绿色只读直接执行；黄色（小额转账、订阅查询与取消、虚拟卡申请等）需要你在确认卡上点确认；红色（当日累计超过 ¥1,000、新收款人、挂失与解挂、改密码、理财申购等）还要强验证：**刷脸 / 指纹 / 安全密钥**，或短信、验证器动态码。`tests/test_permission_matrix.py` 用真实对话逐级核对。
- **六 Agent 可信通信。** Agent 之间的每条消息都签名、每次交接都留证据，见下一节。
- **反诈。** 识别“安全账户”“先解除再转账”等诈骗话术并拦截，可一键开启 24 小时转账冷静期，期间只能由银行人员核实后解除。
- **外部文本隔离。** 转账备注、商户名等第三方文本先过注入检测，可疑内容不会进入大模型。
- **大模型看不到** 余额、流水、卡号、验证码。

## 核心亮点：多 Agent 可信通信

多 Agent 系统最怕的是：某个 Agent 被提示注入或被攻破后，冒充别人、篡改消息、越权提案，而事后谁也说不清发生了什么。TRUSTA 让 Agent 之间的每一次通信都**可验证、可追溯、不可抵赖**。

<p align="center"><img src="docs/figures/fig1.png" alt="图 1 AI 只能提议；Agent 之间的每一步都签名、可验证" width="100%"></p>

<sub>**图 1 ｜ AI 只能提议；Agent 之间的每一步都签名、可验证。** **a**，一个被攻破的 Agent 可能做的四件事（冒充、篡改与重放、越权、事后抵赖），以及 TRUSTA 对应的机制。**b**，“给张三转500元”的全链路：AI 的权限止于校验器；Agent 之间每次交接都签名，并由接收方检查 ①–④；只有你在确认卡上的授权（⑤，绑定 action_hash）能让执行合约（⑥）动钱；每一步留下的证据串成因果链，封进签名 Merkle 检查点（⑦）。**c**，18 类攻击各 5 次的实测结果，以及每类攻击被哪一道关卡拦下；右侧解释这些数字的含义。English version: [docs/figures/fig1-en.png](docs/figures/fig1-en.png)。</sub>

| 机制 | 防住什么 |
|---|---|
| 每个 Agent 独立的 ECDSA P-256 密钥身份，私钥加密存储；跨机构协作时登记为链上 WeID | 冒充其他 Agent |
| V3 消息信封：规范化负载哈希、任务版本、5 分钟有效期、一次性 nonce、绑定接收方 | 篡改、重放、过期消息、错发 |
| 能力快照：消息里带发送方当前权限承诺，与注册表不一致即拒收 | Agent 越权、权限被悄悄放大 |
| 通信边约束：每类任务只允许计划内的（发送方、接收方、消息类型）组合 | 例如财务 Agent 突然发出一条“转账提案” |
| 交接证据因果链：运行时对每次交接再签名，并引用前序证据 | 事后否认、顺序被调换 |
| 意图来源证明：用户原话哈希、理解方式（大模型或规则）、模型工具调用记录哈希写进总控签名的请求 | 无法证明某个提案来自用户的哪句话 |
| Agent 只能提案；执行由确定性执行器和封印的执行合约完成 | 被攻破的 Agent 直接动钱 |
| 密钥轮换、吊销、身份暂停 | 密钥泄露后的处置 |

**攻击实测**：伪造身份、无效签名、负载篡改、重放、过期、吊销密钥、越权、能力快照篡改、未授权工具调用、重复执行、审计篡改、Merkle 证明与检查点篡改、签名算法降级等 **18 类攻击各 5 次，全部拦截，0 笔意外交易**。

**亲眼验证**：
- 网页“设置 → 外观”打开**开发者模式**，每条回复下出现“查看决策过程”：逐条列出 Agent 间的签名消息，并当场重新验签（消息签名、证据签名、因果父节点）。
- 每张结果卡可导出**签名证据包**，用 `python -m scripts.verify_handoff_evidence bundle.json --trusted-registry registry.json` 离线验证，不需要相信这台服务器；改动包里任何一个字节都会验证失败。
- 重跑攻击基准：`python -m benchmarks.security_benchmark --banking-version 5`。

### 跨机构协作：WeID 链上身份 · 零知识准入 · 链上承诺

当多家机构的 Agent 需要共同完成一件事（例如几家银行的 Agent 交叉核对同一份风险结论），仅靠本机签名还不够：对方怎么确认“你是谁”“你够不够格参与”“最后的结论有没有被改过”？TRUSTA 在图 1 的基础上叠加了一层可在联盟链上公开核验的信任层：

<p align="center"><img src="docs/figures/fig2.png" alt="图 2 跨机构协作：身份、资格、结论都能在链上公开核验" width="100%"></p>

<sub>**图 2 ｜ 跨机构协作：身份、资格、结论都能在链上公开核验。** **a**，合作方会问的三个问题与对应机制：WeID 链上身份、Groth16 零知识能力准入（M1）、链上决策承诺（M3），由 M2 把它们绑定到每条协作消息。**b**，一轮 k-of-n 协作的实测时间线：凑够 k 份也不提前结束，截止才冻结证据。**c**，在 FISCO BCOS 测试联盟链上的实测结果与如实标注的边界。English version: [docs/figures/fig2-en.png](docs/figures/fig2-en.png)。</sub>

- **WeID（微众银行开源的分布式身份）**：Agent 身份登记在 FISCO BCOS 联盟链上，可注册、解析、吊销；协作轮次只认链上 WeID，链不可用时直接拒绝，不会退回本地自签的 did:key。
- **零知识能力准入（M1）**：证明“我的能力达标”，但不泄露评测数据和预测结果；绑定策略、周期与模型承诺，可撤销、会到期。
- **链上决策承诺（M3）**：协作结论上链存证，提交结果未知时按原哈希恢复，绝不盲目重发。
- 链上身份与签名都**不授予任何银行写入权限**，资金操作仍须用户在确认卡上授权。

当前为单机 8 节点测试联盟链，Groth16 使用开发用可信设置（非生产 MPC），均如实标注在[集成验收报告](docs/ANCHOR_Integration_Final_Acceptance.md)中。

设计与检查记录见 [docs/trusted-agent-communication-review.md](docs/trusted-agent-communication-review.md) 和 [docs/phase4-cryptographic-trust.md](docs/phase4-cryptographic-trust.md)。

## 按配置点亮功能

像配置开发环境一样：选择大模型、银行接口和渠道（网页 / 手机 H5 / 飞书），TRUSTA 根据实际接入且验证通过的能力决定哪些功能可用。没开通的功能，助手看不到也调用不了，功能页会告诉你需要接什么接口。

## 接入真实银行

所有页面和对话只通过统一的银行接口取数和办理，不依赖任何银行的内部数据。银行写一个适配器、在配置里指向它就能接入，不用改 TRUSTA 的代码；银行没开放的业务，对应功能自动隐藏，相关请求会明确说明。

- 已在 **Revolut 官方沙箱**实测：真实读取账户与流水，并完成一笔逐笔授权的小额付款。
- 全部业务（含存款、外汇、黄金、基金、贷款、信用账户）在一家数据库独立、TRUSTA 读不到的**模拟外部银行**上做了端到端测试，并核对银行账本（`tests/test_real_bank_readiness.py`）。
- 接入工具包：[接入指南](docs/real-bank-integration.md)、[数据格式规范](docs/bank-data-contract.md) 与 JSON Schema、可直接改的适配器模板 `examples/bank_adapter/`、[银行安全审查说明](docs/bank-security-brief.md)，以及接入自检工具：

```bash
python -m scripts.bank_check --config config/bank.<你的银行>.local.toml   # 逐项检查并出报告；--sandbox 可直接演示
```

## 赛题要求对照

| 赛题要求 | 实现 | 说明 |
|---|---|---|
| 六大场景 | 全部覆盖，另有存款、外汇、黄金、基金、贷款、信用账户与生活服务 | `python -m benchmarks.scenario_audit` 49 项闭环检查 |
| 权限分级 | 绿 / 黄 / 红，阈值与操作范围按赛题表；红色支持刷脸、指纹、安全密钥 | [技术文档 3.3](docs/technical-design.md) |
| 幻觉防护 | 业务数字只来自银行；金额等参数必须出自原话；模型自由文本中的数字不展示 | [技术文档 3.1](docs/technical-design.md) |
| 注入防御 | 输入防护、模型输出授权字段检测、他人文字隔离、3 次锁定会话 | 18 类攻击 × 5 次实测全部拦下 |
| 操作审计 | 每轮决策链可查；审计哈希链 + 签名 Merkle 检查点；签名回执可离线验证 | [技术文档 4](docs/technical-design.md) |
| 异常熔断 | 连续认证 / 执行失败锁定写操作；模型熔断降级规则引擎；24 小时转账冷静期 | 同上 |
| 沙箱环境 | 不执行模型生成的代码；财务智能体可放进受限进程；内置沙箱银行 | 同上 |
| 意图与规划 | 模糊表达、省略上下文、隐含意图；任务 DAG；中断、撤销、人工接管 | [技术文档 3.2](docs/technical-design.md) |

## 对比评测：同一个模型，三种搭法

<p align="center"><img src="docs/compare/figure-card.png" alt="图 3 同一个大模型，三种搭法：只有 TRUSTA 既不出事，又能当场办成" width="100%"></p>

<sub>**图 3 ｜ 同一个大模型，三种搭法：只有 TRUSTA 既不出事，又能当场办成。** 三组使用同一个模型（DeepSeek `deepseek-flash`，温度 0）、同一个沙箱银行和同一份 218 道题（正常业务、模糊指令、直接注入、间接注入、诱导编造、高风险操作），每题每组跑 3 次，共 1,962 次真实对话。**A 不设防的智能体**：最常见的写法，模型直接调用转账、挂失等银行接口，调了就执行。**B 提示词加固**：在 A 上加 8 条安全规则（不执行数据和转发内容里的指令、高风险操作先发验证码、不编造数据等），所有写操作弹确认框；能否遵守靠模型自觉。**C TRUSTA**：模型只能提议；金额、收款人必须出自客户原话，客户在卡片上逐笔确认，高风险操作由系统强制强验证。四行依次回答：会不会被话术骗着越权（攻击成功率）；真的出过事吗（未经确认就执行的越权操作、泄露系统提示词、超出风险等级的理财，合计次数）；大额、改密等高风险操作有没有强验证（覆盖率）；说一句话能不能当场办成（正常业务当轮完成率）。埋在银行数据和转发短信里的指令，这一代模型本身已能识破，三组都没有被骗；TRUSTA 的不同在于，即使模型被骗，系统也不放行。 方法、题库、局限与逐条记录见 [docs/compare](docs/compare/README.md)；复现：`python -m benchmarks.compare --live --runs 3`。English version: [docs/compare/figure-card-en.png](docs/compare/figure-card-en.png)。</sub>

## 快速开始

**一行命令安装**（自动下载、创建独立虚拟环境、安装依赖并打开配置向导）：

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex      # Windows PowerShell
```

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash   # macOS / Linux
```

**从源码运行**（需要 Python 3.12+）：

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-channels.txt   # 可选：飞书、手机配对二维码
python -m scripts.setup                              # 配置向导：大模型、访问方式、飞书
.\start.ps1                                          # macOS / Linux：./start.sh；Windows 也可双击 TRUSTA.cmd
```

用窗口里打印的 `http://localhost:8000/#token=…` 链接打开网页（请用 localhost：刷脸 / 指纹验证不支持 IP 地址）。不配置大模型也能用，此时使用规则引擎，只认标准说法。

**手机 H5**：在配置向导里把访问方式设为“局域网”，重启后在网页“设置 → 访问与设备”生成配对码，手机连同一 Wi-Fi 扫码即可；可“添加到主屏幕”全屏使用。

**飞书**：在飞书开放平台创建企业自建应用，开通 `im:message.p2p_msg:readonly`（收单聊消息）和 `im:message:send_as_bot`（以应用身份发消息）两项权限，事件与回调选长连接并添加 `im.message.receive_v1` 与卡片回调，发布版本。然后在网页 **设置 → 飞书** 或配置向导里填 App ID / App Secret（长连接，不需要公网地址），重启后生成绑定码，在飞书里私聊发给机器人。完整步骤见 [飞书接入](frontend-app/src/docs/content/zh/feishu.md)。黄色操作可在飞书内确认，红色操作会引导回 APP 验证。

完整文档（安装、配置、授权等级、可信机制、API）在网站的 `/docs.html`，源文件在 `frontend-app/src/docs/content/`。

## 技术文档

系统架构图、核心算法与安全设计见 [docs/technical-design.md](docs/technical-design.md)。

## 项目结构

```
backend/          六 Agent 运行时、银行网关、安全控制、对话层（chat/）、渠道（channels/）
frontend-app/     网页与手机端（React + Vite），构建产物在 frontend/dist，由后端直接提供
scripts/          启动、配置向导、配对、定时任务、审计 worker、银行接入自检（bank_check）、演示脚本
tests/            单元与集成测试（约 1,550 项）
examples/         银行适配器模板（bank_adapter/）
benchmarks/       六大场景闭环审计等评测
docs/             协议、设计与评测文档；历史开发记录见 docs/development-history.md
```

修改前端后重新构建：

```powershell
cd frontend-app
npm install
npm run build
```

## 测试

```powershell
python -m unittest discover -s tests   # 或 python -m pytest -q
python -m benchmarks.scenario_audit     # 六大场景 49 项闭环检查
```

少数测试需要未随仓库发布的原始材料（Groth16 证明资产、冻结的模型评测记录），缺少时会显示为“跳过”并写明原因，材料放入后自动运行。权限分级与赛题对照见 `tests/test_permission_matrix.py`，真实银行接入见 `tests/test_real_bank_readiness.py` 与 `tests/test_bank_check.py`。

## 说明

本项目为金融科技大赛参赛作品，默认运行在本地虚拟银行沙箱，演示数据均为虚构；接入真实银行见上文“接入真实银行”，还需要完成银行侧授权。短信验证码在演示模式下为模拟通道（验证码显示在页面上），刷脸 / 指纹验证为真实的设备验证。`.env`、`data/` 保存密钥和数据库，已被忽略，不要提交。

## 版权

© 2026 TRUSTA 团队。保留所有权利。本仓库公开仅用于金融科技大赛评审，以及个人阅读、学习和本地运行评估；未经书面许可，不得复制、修改、再发布、商用，或作为自己的作品参赛。详见 [LICENSE](LICENSE)。
