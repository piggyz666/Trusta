import { useEffect, useRef, useState } from "react";
import Backdrop, { stirInk } from "../components/Backdrop.jsx";

const GITHUB = "https://github.com/piggyz666/Trusta";
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const hex = (n = 8) => Array.from(crypto.getRandomValues(new Uint8Array(n / 2)), (b) => b.toString(16).padStart(2, "0")).join("");

// ---------------------------------------------------------------- live demo ----
const SAMPLES = {
  transfer: {
    say: "给张三转500元", tier: "YELLOW", tierLabel: "需你确认",
    reply: "请核对下面的信息，确认后我再执行。",
    card: { title: "转账给 张三", amount: "¥500.00", fields: [["收款人", "张三 · 常用收款人"], ["付款账户", "活期账户，转后余额 ¥9,500.00"]] },
    agent: "交易", action: "确认",
    done: "已向张三转账 ¥500.00，银行核验通过。",
  },
  lost: {
    say: "我的卡丢了", tier: "RED", tierLabel: "需要验证码",
    reply: "挂失会立即止付，属于高风险操作，需要验证码。",
    card: { title: "挂失卡片", amount: null, fields: [["卡片", "借记卡 尾号 6021"], ["生效", "确认后立即止付"]] },
    agent: "客服", action: "验证并确认", otp: true,
    done: "借记卡 6021 已挂失，所有交易已止付。",
  },
  spend: {
    say: "这个月花了多少钱", tier: "GREEN", tierLabel: "只读，直接执行",
    reply: "本月已结算 11 笔，共 ¥5,425.00，其中购物占 95%。",
    agent: "财务", read: true,
  },
};

function trail(s) {
  const head = [["总控", "规划", "REQUEST"], ["规划", s.agent, "PLAN"], [s.agent, "总控", s.read ? "READ_ONLY_REPORT" : "ACTION_PROPOSED"]];
  if (s.read) return { before: head, after: [] };
  return {
    before: head,
    after: [["你", "执行合约", s.otp ? "授权 + 验证码" : "授权"], ["网关", "银行", "执行"], ["核验", "回执", "前后状态一致"], ["审计链", "检查点", "已写入"]],
  };
}

function Demo() {
  const [key, setKey] = useState("transfer");
  const [run, setRun] = useState(0);
  const [typed, setTyped] = useState("");
  const [phase, setPhase] = useState("typing"); // typing | proposed | running | done | declined
  const [shown, setShown] = useState(0);
  const [hashes, setHashes] = useState([]);
  const confirmRef = useRef(null);
  const s = SAMPLES[key];
  const t = trail(s);
  const steps = [...t.before, ...t.after];

  useEffect(() => {
    setTyped(""); setShown(0); setPhase("typing");
    setHashes(Array.from({ length: 8 }, () => hex(8)));
    if (reduced()) { setTyped(s.say); setPhase(s.read ? "done" : "proposed"); setShown(s.read ? t.before.length : t.before.length); return undefined; }
    let i = 0;
    const id = setInterval(() => {
      i += 1; setTyped(s.say.slice(0, i));
      if (i >= s.say.length) { clearInterval(id); setTimeout(() => setPhase(s.read ? "running" : "proposed"), 380); }
    }, 85);
    return () => clearInterval(id);
  }, [key, run]);

  // reveal the signed hand-offs one by one
  useEffect(() => {
    const target = phase === "proposed" ? t.before.length : phase === "running" ? steps.length : null;
    if (target == null || shown >= target) {
      if (phase === "running" && shown >= steps.length) setPhase("done");
      return undefined;
    }
    const id = setTimeout(() => setShown((n) => n + 1), reduced() ? 0 : 320);
    return () => clearTimeout(id);
  }, [phase, shown]);

  function confirm() {
    stirInk("send", confirmRef.current);
    setPhase("running");
  }

  return (
    <div className="demo" aria-label="TRUSTA 演示">
      <div className="demo-tabs" role="tablist" aria-label="选择一句话">
        {Object.entries(SAMPLES).map(([k, v]) => (
          <button key={k} role="tab" aria-selected={k === key} onClick={() => setKey(k)}>{v.say}</button>
        ))}
      </div>
      <div className="demo-body">
        <div className="bubble-user">{typed}<span className={`caret ${phase === "typing" ? "on" : ""}`} /></div>
        {phase !== "typing" && (
          <>
            <p className="demo-reply">{s.read && phase !== "done" ? "正在查询…" : s.reply}</p>
            {!s.read && (
              <div className={`proposal tier-${s.tier.toLowerCase()}`}>
                <div className="proposal-head"><b>{s.card.title}</b><span className={`tier tier-${s.tier.toLowerCase()}`}>{s.tierLabel}</span></div>
                {s.card.amount && <div className="proposal-amount">{s.card.amount}</div>}
                <dl>{s.card.fields.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}</dl>
                {s.otp && phase === "proposed" && <div className="otp" aria-label="验证码">验证码 <span>4 8 2 0 1 7</span></div>}
                {phase === "proposed" && (
                  <div className="proposal-actions">
                    <button ref={confirmRef} className="go" onClick={confirm}>{s.action}</button>
                    <button className="no" onClick={() => setPhase("declined")}>拒绝</button>
                  </div>
                )}
                {phase === "declined" && <p className="proposal-note">已拒绝。没有任何资金或卡片变动，这张卡片作废。</p>}
                {(phase === "running" || phase === "done") && <p className="proposal-note ok">{phase === "done" ? s.done : "执行中…"}</p>}
              </div>
            )}
          </>
        )}
      </div>
      <ol className="handoffs" aria-label="Agent 交接记录">
        {steps.slice(0, shown).map(([from, to, kind], i) => (
          <li key={i} className={i >= t.before.length ? "exec" : ""}>
            <span className="who">{from}<i>→</i>{to}</span>
            <span className="kind">{kind}</span>
            <span className="sig">签名 ✓ <code>{hashes[i]}</code></span>
          </li>
        ))}
      </ol>
      <div className="demo-foot">
        {phase === "proposed" && <span>AI 已提出方案。按钮是唯一的授权方式，在聊天里说“确认”不会执行。</span>}
        {phase === "done" && <span>{s.read ? "只读查询直接执行，同样每一步签名。" : "完成。每一次交接都留下了可离线验证的签名证据。"}<button className="link" onClick={() => setRun((n) => n + 1)}>重新演示</button></span>}
        {phase === "declined" && <button className="link" onClick={() => setRun((n) => n + 1)}>重新演示</button>}
        <span className="muted">演示动画，不连接真实银行</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page ----
function Copy({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button className="copy" onClick={() => { navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1400); }); }}
      aria-label={"复制命令 " + text}>{done ? "已复制" : "复制"}</button>
  );
}
function Cmd({ children }) {
  return <div className="cmd"><code>{children}</code><Copy text={children} /></div>;
}

const SCENES = [
  ["智能转账", ["给张三转500元", "每月1号给房东转3000"], "确认卡上可以直接改金额；按姓名、手机号、称呼找人，支持定时、周期和备注。"],
  ["账单分析", ["这个月花了多少钱"], "分类占比一目了然，点一类看明细，异常消费自动标出。"],
  ["理财", ["推荐适合我的理财"], "按你的风险等级筛选，对比后在卡片上直接申购或赎回。"],
  ["卡片管理", ["我的卡丢了"], "挂失需要验证码；境外、线上、闪付开关在卡片上直接拨动。"],
  ["订阅与代扣", ["最近有什么要续费"], "列出即将扣费的订阅和代扣，不想要的一键取消。"],
  ["跨场景联动", ["下周去日本玩5天，帮我安排一下"], "核对余额、赎回理财、开通境外交易、返程自动关闭、超预算提醒，一步步等你确认。"],
];

const TIERS = [
  ["green", "直接执行", "查余额、看账单、比较理财产品。只读，不动钱。"],
  ["amber", "你在确认卡上点确认", "转账、冻结卡、取消订阅、申购理财。在聊天里说“确认”不算数。"],
  ["red", "还要验证码", "大额转账、新收款人、挂失。短信或验证器动态码，飞书里也必须回到 APP 完成。"],
];

const TERMINAL = `$ python -m scripts.setup --probe
探测 llm     VERIFIED
探测 bank    VERIFIED
探测 feishu  VERIFIED

模型：deepseek-flash（VERIFIED）  银行：virtual/local  安全档位：competition

【六大场景】
  ✔ 可用  智能转账（姓名 / 手机号 / 称呼 / 定时 / 周期 / 备注）
  ✔ 可用  账单分析 / 消费查询 / 异常识别
  ✔ 可用  理财推荐 / 对比 / 申赎 / 持仓
  ✔ 可用  卡片管理
【跨场景联动】
  ✔ 可用  心愿金（跨场景：锁定 → 到期下单 → 释放）
  ✔ 可用  出行管家（余额 → 赎回 → 境外开关 → 返程关闭 → 预算提醒）
【渠道】
  ✘ 不可用 飞书渠道  —— 缺少：飞书机器人
        开通方法：运行 python -m scripts.setup 填写 FEISHU_APP_ID / FEISHU_APP_SECRET

合计：可用 19，降级 0，不可用 1`;

const COMMANDS = [
  ["python -m scripts.setup", "配置向导：选大模型、访问方式、飞书"],
  ["python -m scripts.setup --probe", "检测各项接入是否连通，列出可用功能"],
  [".\\start.ps1", "启动服务和后台任务，打印打开网页的链接"],
  ["python -m scripts.pair", "生成手机配对码"],
  ["python -m scripts.pair --feishu", "生成飞书绑定码"],
  ["python -m scripts.chat_cli", "在终端里和助手对话"],
  ["python -m benchmarks.scenario_audit", "六大场景 49 项闭环检查"],
  ["python -m benchmarks.security_benchmark --banking-version 5", "重跑 18 类攻击实测"],
  ["python -m scripts.verify_handoff_evidence bundle.json --trusted-registry registry.json", "离线验证导出的签名证据包"],
];

const PHRASES = [
  ["转账", ["给张三转500元", "每月1号给房东转3000", "改成800", "算了不转了"]],
  ["账单", ["这个月花了多少钱", "上个月外卖花了多少"]],
  ["理财", ["推荐适合我的理财", "做一下风险测评", "买1000低风险"]],
  ["卡片", ["我的卡丢了", "关闭信用卡境外交易"]],
  ["联动", ["外卖每月超过800就提醒我", "工资到账后存1000到心愿金", "下个月妈妈生日，帮我存2000买礼物"]],
  ["安全", ["有人自称警察让我转账到安全账户", "开启24小时转账保护"]],
];

const STACK = [
  ["后端", "Python 3.11+ · SQLite · cryptography 50（ECDSA P-256）"],
  ["大模型", "DeepSeek 或任意兼容 OpenAI 工具调用的模型；不接模型时用规则引擎"],
  ["渠道", "网页与手机 H5 · 飞书长连接（lark-oapi 1.7.5）· 扫码配对（qrcode 8.2）"],
  ["前端", "React 18 · Vite 5 · WebGL 水波"],
  ["链与密码学", "FISCO BCOS 2.9 · WeIdentity SDK 1.8 · Groth16（circom / snarkjs）· Solidity"],
];

export default function Intro() {
  const [showFig2, setShowFig2] = useState(false);
  return (
    <>
      <Backdrop />
      <div className="intro">
        <header className="nav">
          <a className="brand" href="#top"><img className="hero-mark" src="/mark.png" alt="" width="30" height="30" />TRUSTA</a>
          <nav aria-label="页面导航">
            <a href="#scenes">能做什么</a><a href="#trust">为什么可信</a><a href="#install">安装</a><a href={GITHUB}>GitHub</a>
          </nav>
        </header>

        <section className="hero" id="top">
          <div className="hero-copy">
            <h1>AI 提议，<br />你授权。</h1>
            <p className="lede">TRUSTA 是运行在你自己电脑上的银行助手。说一句话就能转账、查账单、管卡片、安排理财；每一笔资金操作都要你亲手确认，每一次 Agent 之间的交接都签名留证。</p>
            <div className="ctas">
              <a className="btn-primary" href="/">打开 TRUSTA</a>
              <a className="btn-quiet" href={GITHUB}>在 GitHub 查看源码</a>
            </div>
            <p className="fine">金融科技大赛参赛作品。默认连接本地虚拟银行沙箱，演示数据均为虚构。</p>
          </div>
          <Demo />
        </section>

        <main className="sheet">
          <section id="scenes" className="block">
            <div className="block-head">
              <h2>一句话能做的事</h2>
              <p>覆盖银行 AI 智能体的六大场景。回复是可以直接操作的卡片，不是一段要你照着做的文字。</p>
            </div>
            <dl className="scenes">
              {SCENES.map(([name, say, what]) => (
                <div key={name}><dt>{name}</dt><dd>{say.map((q) => <q key={q}>{q}</q>)}<span>{what}</span></dd></div>
              ))}
            </dl>
          </section>

          <section id="trust" className="block">
            <div className="block-head">
              <h2>钱怎么动，由你说了算</h2>
              <p>大模型只能提出方案，每个参数都必须在你的原话里找得到出处，否则它会追问。真正执行之前，按风险分三级把关：</p>
            </div>
            <ul className="tiers">
              {TIERS.map(([c, a, b]) => <li key={c} className={c}><b>{a}</b><span>{b}</span></li>)}
            </ul>
            <p className="aside">另外：识别“安全账户”“先解除再转账”这类诈骗话术，一键开启 24 小时转账冷静期；转账备注等外部文本先过注入检测；大模型看不到你的余额、流水、卡号和验证码。</p>
          </section>

          <section className="block">
            <div className="block-head">
              <h2>六个 Agent，每一次交接都签名</h2>
              <p>总控、规划、交易、财务、理财、客服六个 Agent 分工协作。它们之间的每条消息都带 ECDSA 签名、权限快照和一次性 nonce；运行时逐条验签后签发交接证据，串成因果链，写进只追加的审计链。任何一笔，都能导出证据包离线验证，不需要相信这台服务器。</p>
            </div>
            <figure className="figure"><img src="/figures/fig1.png" alt="图 1：多 Agent 可信通信全链路、签名消息信封与证据链" loading="lazy" /></figure>
            <table className="results">
              <caption>攻击实测（18 类，每类 5 次）</caption>
              <tbody>
                <tr><th>伪造身份、篡改、重放、越权、降级、审计与证明篡改</th><td>90 / 90 拦截</td></tr>
                <tr><th>攻击导致的意外交易</th><td>0 笔</td></tr>
                <tr><th>正常消息被误拒</th><td>0 / 135</td></tr>
                <tr><th>单条消息验签耗时（中位数）</th><td>5.5 ms</td></tr>
              </tbody>
            </table>
            <button className="expand" aria-expanded={showFig2} onClick={() => setShowFig2((v) => !v)}>
              {showFig2 ? "收起跨机构协作信任层" : "展开跨机构协作信任层：WeID 链上身份、零知识准入、链上承诺"}
            </button>
            {showFig2 && (
              <div className="fig2">
                <p>多家机构的 Agent 协作时，每个 Agent 在 FISCO BCOS 联盟链上登记 WeID；用 Groth16 零知识证明说明自己的能力达标而不泄露评测数据；协作结论的 Merkle 根写入链上合约，由两个节点分别读回核对。当前为单机 8 节点测试链，零知识证明使用开发用可信设置。</p>
                <figure className="figure"><img src="/figures/fig2.png" alt="图 2：跨机构协作信任层与攻击实测" loading="lazy" /></figure>
              </div>
            )}
          </section>

          <section className="block split">
            <div className="block-head">
              <h2>先配置，再点亮功能</h2>
              <p>像配置开发环境一样，选好大模型、银行接口和渠道。TRUSTA 只点亮真正接通并验证过的功能；没开通的，助手看不到也调用不了，并告诉你需要接什么。</p>
              <p>网页的“功能”页里每个功能都有开关，你也可以自己关掉不想用的。安全相关的开关锁定，不能关闭。</p>
            </div>
            <pre className="terminal" aria-label="终端输出示例">{TERMINAL}</pre>
          </section>

          <section id="install" className="block">
            <div className="block-head">
              <h2>在你的电脑上跑起来</h2>
              <p>需要 Windows 与 Python 3.11 或以上版本。网页已经构建好，不需要安装 Node.js。数据和密钥都只存在你的电脑上。</p>
            </div>
            <ol className="steps">
              <li><b>安装依赖</b><Cmd>python -m pip install -r requirements.txt -r requirements-channels.txt</Cmd></li>
              <li><b>运行配置向导</b><span>选择大模型、访问方式（本机 / 局域网 / 隧道）和飞书。什么都不配也能用，此时只认标准说法。</span><Cmd>python -m scripts.setup</Cmd></li>
              <li><b>启动</b><span>用窗口里打印的链接打开网页。</span><Cmd>.\start.ps1</Cmd></li>
              <li><b>连接手机（可选）</b><span>网页“设置 → 访问与设备”生成配对码，手机连同一 Wi-Fi 扫码；可以添加到主屏幕全屏使用。</span></li>
            </ol>
          </section>

          <section className="block">
            <div className="block-head"><h2>命令速查</h2></div>
            <div className="cheats">
              <div>
                <h3>对助手说</h3>
                <dl className="phrases">
                  {PHRASES.map(([g, list]) => <div key={g}><dt>{g}</dt><dd>{list.map((p) => <q key={p}>{p}</q>)}</dd></div>)}
                </dl>
              </div>
              <div>
                <h3>在终端运行</h3>
                <table className="commands">
                  <tbody>{COMMANDS.map(([c, d]) => <tr key={c}><td><code>{c}</code></td><td>{d}</td></tr>)}</tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="block">
            <div className="block-head"><h2>用到的技术</h2></div>
            <dl className="stack">{STACK.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}</dl>
          </section>
        </main>

        <footer className="foot">
          <span><img src="/mark.png" alt="" width="22" height="22" />TRUSTA 银行助手</span>
          <span>金融科技大赛参赛作品 · 演示数据均为虚构</span>
          <a href={GITHUB}>GitHub</a>
        </footer>
      </div>
    </>
  );
}
