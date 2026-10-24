import { useEffect, useRef, useState } from "react";
import { DOCS, DOWNLOAD_WINDOWS, GITHUB, ONE_LINER_UNIX, ONE_LINER_WINDOWS, CLONE } from "../site/links.js";

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const hex = (n = 8) => Array.from(crypto.getRandomValues(new Uint8Array(n / 2)), (b) => b.toString(16).padStart(2, "0")).join("");
const href = (h) => (h === "github" ? GITHUB : h);

// ---------------------------------------------------------------- shared bits ----
export function Reveal({ as: Tag = "div", className = "", delay = 0, children, ...rest }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced() || !("IntersectionObserver" in window)) { setShown(true); return undefined; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setShown(true); io.disconnect(); } }, { rootMargin: "0px 0px -8% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <Tag ref={ref} className={`reveal ${shown ? "in" : ""} ${className}`} style={{ "--d": `${delay}ms` }} {...rest}>{children}</Tag>;
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true">
      <path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function CopyButton({ text, t }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = Object.assign(document.createElement("textarea"), { value: text }); document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
    }
    setDone(true); setTimeout(() => setDone(false), 1400);
  };
  return <button type="button" className="copy" onClick={copy} aria-label={`${t.copy}: ${text}`}>{done ? t.copied : t.copy}</button>;
}

export function Cmd({ children, t, prompt = "$" }) {
  return (
    <div className="cmd">
      <code><span className="prompt" aria-hidden="true">{prompt}</span>{children}</code>
      <CopyButton text={children} t={t} />
    </div>
  );
}

// ---------------------------------------------------------------- nav ----
export function Nav({ c, lang, setLang }) {
  const [solid, setSolid] = useState(false);
  useEffect(() => {
    const on = () => setSolid(window.scrollY > window.innerHeight * 0.6);
    on(); window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={`nav ${solid ? "solid" : ""}`}>
      <a className="brand" href="#top" aria-label="TRUSTA">
        <img src="/mark.png" alt="" width="26" height="26" /><span>TRUSTA</span>
      </a>
      <nav aria-label={lang === "zh" ? "页面导航" : "Site"}>
        <a className="wide" href="#product">{c.nav.product}</a>
        <a className="wide" href="#trust">{c.nav.trust}</a>
        <a className="wide" href="#install">{c.nav.install}</a>
        <a href={DOCS}>{c.nav.docs}</a>
        <a className="icon" href={GITHUB} aria-label="GitHub"><GitHubIcon /></a>
        <button type="button" className="lang" onClick={() => setLang(lang === "zh" ? "en" : "zh")} aria-label={c.nav.langLabel}>{c.nav.lang}</button>
      </nav>
    </header>
  );
}

// ---------------------------------------------------------------- statement ----
export function Statement({ c }) {
  return (
    <section className="statement wrap" aria-labelledby="statement-title">
      <Reveal as="p" className="kicker">{c.statement.kicker}</Reveal>
      <Reveal as="h2" id="statement-title" className="big" delay={60}>{c.statement.big.map((l) => <span key={l}>{l}</span>)}</Reveal>
      <Reveal as="p" className="lede" delay={140}>{c.statement.body}</Reveal>
    </section>
  );
}

// ---------------------------------------------------------------- demo ----
function trail(key, a, s) {
  const agent = key === "spend" ? a.fin : key === "lost" ? a.svc : a.tx;
  const read = key === "spend";
  const head = [[a.sup, a.plan, "REQUEST"], [a.plan, agent, "PLAN"], [agent, a.sup, read ? "READ_ONLY_REPORT" : "ACTION_PROPOSED"]];
  if (read) return { before: head, after: [] };
  return {
    before: head,
    after: [[a.you, a.contract, key === "lost" ? s.authOtp : s.auth], [a.gw, a.bank, s.exec], [a.verify, a.receipt, s.match], [a.audit, a.ckpt, s.written]],
  };
}
const TIER = { transfer: "yellow", lost: "red", spend: "green" };

function Demo({ d }) {
  const [key, setKey] = useState("transfer");
  const [run, setRun] = useState(0);
  const [typed, setTyped] = useState("");
  const [phase, setPhase] = useState("typing"); // typing | proposed | running | done | declined
  const [shown, setShown] = useState(0);
  const [hashes, setHashes] = useState([]);
  const s = d.samples[key];
  const read = key === "spend";
  const t = trail(key, d.agents, d.steps);
  const steps = [...t.before, ...t.after];

  useEffect(() => {
    setTyped(""); setShown(0); setPhase("typing");
    setHashes(Array.from({ length: 8 }, () => hex(8)));
    if (reduced()) { setTyped(s.say); setPhase(read ? "done" : "proposed"); setShown(t.before.length); return undefined; }
    let i = 0;
    const chars = Array.from(s.say);
    const id = setInterval(() => {
      i += 1; setTyped(chars.slice(0, i).join(""));
      if (i >= chars.length) { clearInterval(id); setTimeout(() => setPhase(read ? "running" : "proposed"), 380); }
    }, chars.length > 20 ? 42 : 85);
    return () => clearInterval(id);
  }, [key, run, s.say]);

  useEffect(() => {
    const target = phase === "proposed" ? t.before.length : phase === "running" ? steps.length : null;
    if (target == null || shown >= target) {
      if (phase === "running" && shown >= steps.length) setPhase("done");
      return undefined;
    }
    const id = setTimeout(() => setShown((n) => n + 1), reduced() ? 0 : 320);
    return () => clearTimeout(id);
  }, [phase, shown]);

  return (
    <div className="demo" aria-label={d.aria}>
      <div className="demo-tabs" role="tablist" aria-label={d.pick}>
        {Object.entries(d.samples).map(([k, v]) => (
          <button type="button" key={k} role="tab" aria-selected={k === key} onClick={() => { setKey(k); setRun((n) => n + 1); }}>{v.say}</button>
        ))}
      </div>
      <div className="demo-grid">
        <div className="demo-body">
          <div className="bubble-user">{typed}<span className={`caret ${phase === "typing" ? "on" : ""}`} /></div>
          {phase !== "typing" && (
            <>
              <p className="demo-reply">{read && phase !== "done" ? d.querying : s.reply}</p>
              {!read && (
                <div className={`proposal tier-${TIER[key]}`}>
                  <div className="proposal-head"><b>{s.card.title}</b><span className={`tier tier-${TIER[key]}`}>{s.tierLabel}</span></div>
                  {s.card.amount && <div className="proposal-amount">{s.card.amount}</div>}
                  <dl>{s.card.fields.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}</dl>
                  {key === "lost" && phase === "proposed" && <div className="otp">{d.otp} <span>4 8 2 0 1 7</span></div>}
                  {phase === "proposed" && (
                    <div className="proposal-actions">
                      <button type="button" className="go" onClick={() => setPhase("running")}>{s.action}</button>
                      <button type="button" className="no" onClick={() => setPhase("declined")}>{d.reject}</button>
                    </div>
                  )}
                  {phase === "declined" && <p className="proposal-note">{d.declined}</p>}
                  {(phase === "running" || phase === "done") && <p className="proposal-note ok">{phase === "done" ? s.done : d.running}</p>}
                </div>
              )}
              {read && phase === "done" && (
                <div className="mini-chart" aria-hidden="true"><i style={{ "--w": "95%" }} /><i style={{ "--w": "3%" }} /><i style={{ "--w": "2%" }} /></div>
              )}
            </>
          )}
        </div>
        <ol className="handoffs" aria-label={d.handoffs}>
          {steps.slice(0, shown).map(([from, to, kind], i) => (
            <li key={`${run}-${i}`} className={i >= t.before.length ? "exec" : ""}>
              <span className="who">{from}<i>→</i>{to}</span>
              <span className="kind">{kind}</span>
              <span className="sig">{d.signed} <code>{hashes[i]}</code></span>
            </li>
          ))}
        </ol>
      </div>
      <div className="demo-foot">
        <span>
          {phase === "proposed" && d.footProposed}
          {phase === "done" && (read ? d.footDoneRead : d.footDone)}
          {(phase === "done" || phase === "declined") && <button type="button" className="link" onClick={() => setRun((n) => n + 1)}>{d.replay}</button>}
        </span>
        <span className="muted">{d.note}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- product ----
export function Product({ c }) {
  const p = c.product;
  return (
    <section id="product" className="product wrap" aria-labelledby="product-title">
      <Reveal as="p" className="kicker">{p.kicker}</Reveal>
      <Reveal as="h2" id="product-title" className="h2" delay={60}>{p.title}</Reveal>
      <Reveal as="p" className="lede narrow" delay={120}>{p.lead}</Reveal>
      <Reveal delay={160}><Demo d={c.demo} /></Reveal>
      <div className="scenes">
        {p.scenes.map(([title, says, body], i) => (
          <Reveal key={title} className="scene" delay={(i % 3) * 70}>
            <h3>{title}</h3>
            <ul>{says.map((s) => <li key={s}>“{s}”</li>)}</ul>
            <p>{body}</p>
          </Reveal>
        ))}
      </div>
      <Reveal as="h3" className="tiers-title">{c.tiers.title}</Reveal>
      <div className="tiers">
        {c.tiers.items.map(([k, title, body], i) => (
          <Reveal key={k} className={`tier-card ${k}`} delay={i * 80}>
            <span className="dot" aria-hidden="true" /><h4>{title}</h4><p>{body}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- trust ----
export function Trust({ c }) {
  const tr = c.trust;
  return (
    <section id="trust" className="trust wrap" aria-labelledby="trust-title">
      <Reveal as="p" className="kicker">{tr.kicker}</Reveal>
      <Reveal as="h2" id="trust-title" className="h2" delay={60}>{tr.title}</Reveal>
      {tr.layers.map((l) => (
        <article key={l.n} className="layer">
          <Reveal className="layer-text">
            <span className="layer-n">{l.n}</span>
            <h3>{l.title}</h3>
            <p>{l.body}</p>
          </Reveal>
          {l.fig && (
            <Reveal className="figure" delay={100}>
              <img src={l.fig} alt={l.figAlt} loading="lazy" decoding="async" />
            </Reveal>
          )}
        </article>
      ))}
      <div className="extras">
        {tr.extras.map(([h, b], i) => (
          <Reveal key={h} className="extra" delay={i * 60}><h4>{h}</h4><p>{b}</p></Reveal>
        ))}
      </div>
      <Reveal as="a" className="more" href={`${DOCS}#/agent-communication`}>{tr.more} →</Reveal>
    </section>
  );
}

// ---------------------------------------------------------------- numbers ----
export function Numbers({ c }) {
  const n = c.numbers;
  return (
    <section className="numbers" aria-labelledby="numbers-title">
      <div className="wrap">
        <Reveal as="p" className="kicker" id="numbers-title">{n.kicker}</Reveal>
        <div className="num-grid">
          {n.items.map(([big, small], i) => (
            <Reveal key={small} className="num" delay={(i % 3) * 70}><b>{big}</b><span>{small}</span></Reveal>
          ))}
        </div>
        <Reveal as="p" className="num-note">{n.note}</Reveal>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- install ----
function guessOs() {
  const p = (navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "").toLowerCase();
  return p.includes("win") ? "windows" : "unix";
}

export function Install({ c }) {
  const t = c.install;
  const [tab, setTab] = useState("apps");
  const [os, setOs] = useState(guessOs);
  const tabs = ["apps", "oneliner", "pip", "hack"];
  const onKey = (e) => {
    const i = tabs.indexOf(tab);
    if (e.key === "ArrowRight") setTab(tabs[(i + 1) % tabs.length]);
    if (e.key === "ArrowLeft") setTab(tabs[(i + tabs.length - 1) % tabs.length]);
  };
  const App = ({ k, primary }) => {
    const [name, meta, action, note] = t.apps[k];
    return (
      <div className={`app-card ${primary ? "primary" : ""}`}>
        <h4>{name}</h4><p className="meta">{meta}</p>
        {primary
          ? <a className="btn solid" href={DOWNLOAD_WINDOWS}>↓ {action}</a>
          : <span className="btn soon" aria-disabled="true">{action}</span>}
        <p className="note">{note}</p>
        {!primary && <button type="button" className="link" onClick={() => { setTab("oneliner"); setOs("unix"); }}>{t.apps.useOneLiner} →</button>}
      </div>
    );
  };
  return (
    <section id="install" className="install wrap" aria-labelledby="install-title">
      <div className="install-head">
        <div>
          <Reveal as="p" className="kicker">{t.kicker}</Reveal>
          <Reveal as="h2" id="install-title" className="h2" delay={60}>{t.title}</Reveal>
        </div>
        <a className="more" href={`${DOCS}#/install`}>{t.all} →</a>
      </div>
      <Reveal className="panel" delay={100}>
        <div className="panel-tabs" role="tablist" aria-label={t.kicker} onKeyDown={onKey}>
          {tabs.map((k) => (
            <button type="button" key={k} role="tab" id={`tab-${k}`} aria-controls={`pane-${k}`} aria-selected={tab === k}
              tabIndex={tab === k ? 0 : -1} onClick={() => setTab(k)}>{t.tabs[k]}</button>
          ))}
        </div>
        <div className="panel-body" role="tabpanel" id={`pane-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "apps" && (
            <>
              <h3>{t.apps.title}</h3><p className="pane-lead">{t.apps.lead}</p>
              <div className="apps"><App k="windows" primary /><App k="mac" /><App k="linux" /></div>
            </>
          )}
          {tab === "oneliner" && (
            <>
              <h3>{t.oneliner.title}</h3><p className="pane-lead">{t.oneliner.lead}</p>
              <div className="os-switch" role="radiogroup">
                {[["windows", t.oneliner.windows], ["unix", t.oneliner.unix]].map(([k, label]) => (
                  <button type="button" key={k} role="radio" aria-checked={os === k} onClick={() => setOs(k)}>{label}</button>
                ))}
              </div>
              {os === "windows" ? <Cmd t={t} prompt="PS>">{ONE_LINER_WINDOWS}</Cmd> : <Cmd t={t}>{ONE_LINER_UNIX}</Cmd>}
              <p className="fine">{t.oneliner.req}</p>
            </>
          )}
          {tab === "pip" && (
            <>
              <h3>{t.pip.title} <span className="badge">{t.pip.soon}</span></h3><p className="pane-lead">{t.pip.lead}</p>
              <div className="cmd ghost"><code><span className="prompt">$</span>pip install trusta</code></div>
              <div className="cmd ghost"><code><span className="prompt">$</span>trusta setup &amp;&amp; trusta start</code></div>
            </>
          )}
          {tab === "hack" && (
            <>
              <h3>{t.hack.title}</h3><p className="pane-lead">{t.hack.lead}</p>
              <ol className="steps">
                <li><span>{t.hack.steps[0]}</span><Cmd t={t}>{`${CLONE} && cd Trusta`}</Cmd></li>
                <li><span>{t.hack.steps[1]}</span><Cmd t={t}>{"python -m venv .venv && source .venv/bin/activate"}</Cmd>
                  <Cmd t={t} prompt="PS>">{".venv\\Scripts\\activate"}</Cmd>
                  <Cmd t={t}>{"pip install -r requirements.txt -r requirements-channels.txt"}</Cmd></li>
                <li><span>{t.hack.steps[2]}</span><Cmd t={t}>{"python -m scripts.setup"}</Cmd></li>
                <li><span>{t.hack.steps[3]}</span><Cmd t={t}>{"./start.sh"}</Cmd><Cmd t={t} prompt="PS>">{".\\start.ps1"}</Cmd></li>
              </ol>
              <p className="fine">{t.hack.front} <code>cd frontend-app && npm install && npm run build</code></p>
            </>
          )}
        </div>
      </Reveal>
      <Reveal as="p" className="after">{t.after[0]} <code>http://127.0.0.1:8000/#token=…</code> {t.after[1]}</Reveal>
    </section>
  );
}

// ---------------------------------------------------------------- footer ----
export function Footer({ c }) {
  const f = c.footer;
  return (
    <footer className="footer">
      <div className="wrap foot-grid">
        <div className="foot-brand">
          <a className="brand" href="#top"><img src="/mark.png" alt="" width="28" height="28" /><span>TRUSTA</span></a>
          <p>{f.tagline}</p>
        </div>
        {f.cols.map(([h, links]) => (
          <div key={h} className="foot-col">
            <h4>{h}</h4>
            <ul>{links.map(([label, to]) => <li key={label}><a href={href(to)}>{label}</a></li>)}</ul>
          </div>
        ))}
      </div>
      <div className="wrap foot-legal"><p>{f.legal}</p><p>© 2026 TRUSTA</p></div>
    </footer>
  );
}
