<p align="center"><img src="frontend-app/public/icon-192.png" width="96" alt="TRUSTA"></p>

<h1 align="center">TRUSTA 银行助手</h1>

<p align="center">可信的多智能体银行 AI 助手：一句话办理转账、账单、理财、卡片和跨场景安排；<br>AI 只能提议，每一笔资金操作都由你在确认卡上授权。</p>

---

## 它能做什么

| 场景 | 一句话示例 |
|---|---|
| 智能转账 | “给张三转500元”“每月1号给房东转3000” |
| 账单分析 | “这个月花了多少钱”“上个月外卖花了多少” |
| 理财 | “推荐适合我的理财”“买1000低风险”“做一下风险测评” |
| 卡片管理 | “我的卡丢了”“关闭信用卡境外交易” |
| 订阅与代扣 | “我有哪些订阅”“最近有什么要续费” |
| 跨场景联动 | “下个月妈妈生日，帮我存2000买礼物”“下周去日本玩5天，帮我安排一下”“工资到账后存1000到心愿金” |

回复是**可交互卡片**：转账卡上直接改金额，账单饼图点分类看明细，理财卡选产品直接申购，卡片开关直接拨动。

## 为什么可信

- **AI 只能提议，不能授权。** 大模型只输出候选工具调用，每个参数都要能在你的原话里找到出处，否则追问；在聊天里说“确认”不会执行任何操作。
- **三级权限。** 绿色只读直接执行；黄色涉及资金或卡片，需要你在确认卡上点确认；红色（大额、新收款人、挂失等）还要短信或验证器动态码。
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

## 快速开始

**一行命令安装**（自动下载、创建独立虚拟环境、安装依赖并打开配置向导）：

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex      # Windows PowerShell
```

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash   # macOS / Linux
```

**从源码运行**（需要 Python 3.11+）：

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-channels.txt   # 可选：飞书、手机配对二维码
python -m scripts.setup                              # 配置向导：大模型、访问方式、飞书
.\start.ps1                                          # macOS / Linux：./start.sh；Windows 也可双击 TRUSTA.cmd
```

用窗口里打印的 `http://127.0.0.1:8000/#token=…` 链接打开网页。不配置大模型也能用，此时使用规则引擎，只认标准说法。

**手机 H5**：在配置向导里把访问方式设为“局域网”，重启后在网页“设置 → 访问与设备”生成配对码，手机连同一 Wi-Fi 扫码即可；可“添加到主屏幕”全屏使用。

**飞书**：向导里填飞书应用凭据（长连接，不需要公网地址），然后 `python -m scripts.pair --feishu` 生成绑定码，在飞书里发给机器人。黄色操作可在飞书内确认，红色操作会引导回 APP 验证。

完整文档（安装、配置、授权等级、可信机制、API）在网站的 `/docs.html`，源文件在 `frontend-app/src/docs/content/`。

## 项目结构

```
backend/          六 Agent 运行时、银行网关、安全控制、对话层（chat/）、渠道（channels/）
frontend-app/     网页与手机端（React + Vite），构建产物在 frontend/dist，由后端直接提供
scripts/          启动、配置向导、配对、定时任务、审计 worker、演示脚本
tests/            单元与集成测试（约 1400 项）
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

## 说明

本项目为金融科技大赛参赛作品，默认运行在本地虚拟银行沙箱，演示数据均为虚构；接入真实银行需按 `config/` 中的适配器配置并完成银行侧授权。`.env`、`data/` 保存密钥和数据库，已被忽略，不要提交。
