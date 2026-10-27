import FinancePanel from "../components/FinancePanel.jsx";
import { useEffect, useState } from "react";
import { api, explain } from "../api.js";
import { BankGaps, Tag, useToast } from "../components/ui.jsx";
import { dateOnly, yuan } from "../format.js";

let cache = null;
const RISK_TONE = { 1: "green", 2: "green", 3: "amber", 4: "red", 5: "red" };
const GROUP_ORDER = ["存款", "现金管理", "固收", "固收+", "黄金", "权益", "QDII"];
const CURRENCIES = [["CNY", "人民币"], ["USD", "美元"], ["EUR", "欧元"], ["JPY", "日元"], ["HKD", "港币"]];

// yuan per one unit of each currency, from the quote cards (JPY is quoted per 100)
function ratesFrom(market) {
  const r = { CNY: 1 };
  for (const q of market.quotes) if (q.symbol.endsWith("CNY")) { const c = q.symbol.slice(0, 3); r[c] = c === "JPY" ? q.price / 100 : q.price; }
  return r;
}

function Converter({ market }) {
  const rates = ratesFrom(market);
  const [amount, setAmount] = useState("1000");
  const [from, setFrom] = useState("CNY");
  const [to, setTo] = useState("USD");
  const value = Number(amount) || 0;
  const result = rates[from] && rates[to] ? (value * rates[from]) / rates[to] : null;
  const unit = rates[from] && rates[to] ? rates[from] / rates[to] : null;
  const fmt = (n) => n.toLocaleString("zh-CN", { maximumFractionDigits: n < 1 ? 6 : 2, minimumFractionDigits: 2 });
  return (
    <div className="panel converter">
      <div className="conv-row">
        <input className="input num" inputMode="decimal" aria-label="金额" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))} />
        <select className="input" aria-label="从" value={from} onChange={(e) => setFrom(e.target.value)}>
          {CURRENCIES.map(([c, n]) => <option key={c} value={c}>{n} {c}</option>)}
        </select>
        <button className="btn small ghost swap" aria-label="交换" onClick={() => { setFrom(to); setTo(from); }}>⇄</button>
        <select className="input" aria-label="换成" value={to} onChange={(e) => setTo(e.target.value)}>
          {CURRENCIES.map(([c, n]) => <option key={c} value={c}>{n} {c}</option>)}
        </select>
        <div className="conv-result num">{result == null ? "—" : fmt(result)} <small>{to}</small></div>
      </div>
      <div className="faint conv-note">
        {unit != null && <>1 {from} ≈ {fmt(unit)} {to} · </>}按上方参考汇率中间价换算，不是本行结售汇牌价；实际购汇、结汇以办理时的牌价为准。
      </div>
    </div>
  );
}

function Plan({ plan, ask }) {
  if (!plan) return null;
  return (
    <section className="section">
      <h2>为你推荐 <span className="count">按你的余额和消费计算</span></h2>
      <div className="panel plan">
        <ul className="plan-why">{plan.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <div className="plan-buckets">
          {plan.buckets.map((b) => (
            <div key={b.name} className={`bucket b-${b.name}`}>
              <div className="kv"><b>{b.name}</b><span className="num">{yuan(b.amount_minor)}</span></div>
              <small className="faint">{b.label}</small>
              {b.items.map((i) => (
                <div key={i.product_id} className="bucket-item">
                  <span className="grow"><b>{i.name}</b><small className="faint row-sub">{i.issuer} · R{i.risk_level} · {i.term_days ? `${i.term_days} 天` : "随存随取"}</small></span>
                  <span className="num">{yuan(i.amount_minor)}</span>
                  <button className="btn small" onClick={() => ask(`申购${i.amount_minor / 100}元${i.name}`)}>去申购</button>
                </div>
              ))}
            </div>
          ))}
        </div>
        <p className="faint rule-note">依据：{plan.basis.join("、")}。{plan.note}</p>
      </div>
    </section>
  );
}

function ago(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  if (m < 60 * 24) return `${Math.round(m / 60)} 小时前`;
  const d = new Date(t);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function Spark({ points }) {
  if (!points || points.length < 2) return <div className="spark empty-spark" aria-hidden="true" />;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const w = 120, h = 32;
  const path = points.map((p, i) => `${i ? "L" : "M"}${(((p[0] - x0) / (x1 - x0 || 1)) * w).toFixed(1)},${(h - 2 - ((p[1] - y0) / (y1 - y0 || 1)) * (h - 4)).toFixed(1)}`).join("");
  const up = ys[ys.length - 1] >= ys[0];
  return <svg className={`spark ${up ? "up" : "down"}`} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true"><path d={path} /></svg>;
}

export default function Wealth({ ask, goldOnly=false }) {
  const toast = useToast();
  const [data, setData] = useState(cache);
  const [error, setError] = useState(null);
  const [cat, setCat] = useState("全部");
  const [mine, setMine] = useState(false);
  const [open, setOpen] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => { if(goldOnly)return; api.wealth().then((d) => { cache = d; setData(d); }).catch((e) => setError(explain(e))); }, []);
  // quotes keep themselves current while the page is open
  useEffect(() => {
    if(goldOnly)return;
    const id = setInterval(() => api.market().then((m) => setData((d) => d && (cache = { ...d, market: m }))).catch(() => {}), 60000);
    return () => clearInterval(id);
  }, []);

  async function refresh() {
    setRefreshing(true);
    try {
      const m = await api.market(true);
      setData((d) => (cache = { ...d, market: m }));
      toast(m.live ? "行情已更新" : "暂时连不上行情源，显示的是最近一次的数据");
    } catch (e) { toast(explain(e), "error"); }
    setRefreshing(false);
  }

  if(goldOnly)return <div className="page"><div className="inner"><header className="page-head"><h1>黄金交易</h1><p>查看黄金持仓，使用虚拟资金买入或卖出。每笔交易仍需确认及身份验证。</p></header><FinancePanel gold/></div></div>;
  if (error && !data) return <div className="page"><div className="inner"><div className="empty">{error}</div></div></div>;
  if (!data) return <div className="page"><div className="inner muted">读取中…</div></div>;
  const { risk, market } = data;
  const groups = ["全部", ...GROUP_ORDER.filter((g) => data.products.some((p) => p.group === g))];
  const products = data.products.filter((p) => (cat === "全部" || p.group === cat) && (!mine || p.suitable));
  const issuers = new Set(data.products.map((p) => p.issuer)).size;

  return (
    <div className="page">
      <div className="inner">
        <header className="page-head">
          <h1>理财产品</h1>
          <p>{data.policy}</p>
        </header>
        <BankGaps items={data.unavailable} />

        <section className="section">
          <h2>行情
            <span className="count">{market.live ? "实时参考" : "离线"}</span>
            <button className="btn small ghost" style={{ marginLeft: "auto" }} disabled={refreshing} onClick={refresh}>{refreshing ? "刷新中…" : "刷新"}</button>
          </h2>
          <div className="quotes">
            {market.quotes.map((q) => (
              <div key={q.symbol} className={`panel quote ${q.symbol === "XAU" ? "gold" : ""}`}>
                <div className="kv"><b>{q.name}</b>{q.snapshot ? <Tag tone="neutral">快照</Tag> : q.stale ? <Tag tone="amber">延迟</Tag> : null}</div>
                <div className="num quote-price">{q.symbol === "XAU" ? q.price.toFixed(2) : q.price.toFixed(4)}</div>
                <div className="faint quote-unit">{q.unit}</div>
                <Spark points={q.history} />
                <div className="faint quote-src" title={q.detail || ""}>{q.source} · {ago(q.as_of)}</div>
              </div>
            ))}
          </div>
          <p className="faint rule-note">{market.note}{!market.live && " 当前未连上行情源，显示的是最近一次成功获取的数据或内置快照。"}</p>
          <h2 style={{ marginTop: 18 }}>汇率换算</h2>
          <Converter market={market} />
        </section>

        <Plan plan={data.plan} ask={ask} />

        <div className="grid-2">
          <section className="section panel">
            <h2>我的风险等级</h2>
            {risk.level ? (
              <>
                <div className="kv" style={{ alignItems: "baseline" }}>
                  <span className="stat-value">R{risk.level} <span style={{ fontSize: 18, fontWeight: 500 }}>{risk.label}</span></span>
                  <span className="faint" style={{ fontSize: 12.5 }}>有效至 {dateOnly(risk.expires_at)}</span>
                </div>
                <div className="risk-scale" aria-hidden="true">{[1, 2, 3, 4, 5].map((l) => <i key={l} className={l <= risk.level ? "on" : ""}>R{l}</i>)}</div>
                <p className="muted" style={{ fontSize: 13.5, margin: "10px 0" }}>可以申购 R1–R{risk.level} 的产品；更高风险的产品会显示，但不会推荐也不能申购。</p>
              </>
            ) : <p className="muted">还没有有效的风险测评，测评后才能申购理财。</p>}
            <div className="actions">
              <button className="btn small" onClick={() => ask("做一下风险测评")}>{risk.level ? "重新测评" : "开始测评"}</button>
              <button className="btn small ghost" onClick={() => ask("推荐适合我的理财")}>让助手推荐</button>
            </div>
          </section>
          <section className="section panel">
            <h2>我的持仓 <span className="count">{data.holdings.length}</span></h2>
            {data.holdings.length === 0 && <div className="muted">还没有持仓。在下面挑一款，或让助手推荐。</div>}
            {data.holdings.length > 0 && (
              <>
                <div className="stat-value num" style={{ marginBottom: 10 }}>{yuan(data.holdings_minor)}<small className="muted" style={{ fontSize: 13, fontWeight: 400 }}> 按申购本金计</small></div>
                <div className="list">
                  {data.holdings.map((h) => (
                    <div key={h.product_id}>
                      <span className="grow">{h.name} <Tag tone={RISK_TONE[h.risk_level]}>R{h.risk_level}</Tag> <span className="faint" style={{ fontSize: 12.5 }}>{h.issuer}</span>
                        <small className="faint row-sub">{h.redeemable ? "现在可赎回" : `${dateOnly(h.redeemable_at)} 后可赎回`}</small></span>
                      <span className="num">{yuan(h.amount_minor)}</span>
                      <button className="btn small ghost" disabled={!h.redeemable} onClick={() => ask(`赎回${h.name}`)}>赎回</button>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </div>

        <section className="section">
          <h2>在售产品 <span className="count">{data.products.length} 款 · {issuers} 家机构</span></h2>
          <div className="filters" role="group" aria-label="产品类型">
            {groups.map((c) => <button key={c} aria-pressed={cat === c} onClick={() => setCat(c)}>{c}</button>)}
            <button aria-pressed={mine} onClick={() => setMine((v) => !v)} style={{ marginLeft: "auto" }}>只看适合我的</button>
          </div>
          <div className="shelf">
            {products.length === 0 && <div className="panel muted">这一类暂时没有适合你的产品。</div>}
            {products.map((p) => <Product key={p.id} p={p} open={open === p.id} toggle={() => setOpen(open === p.id ? null : p.id)} ask={ask} />)}
          </div>
          <p className="faint rule-note">理财非存款，产品有风险，投资须谨慎。业绩比较基准不是预期收益，也不代表未来表现。以上机构与产品均为演示用虚构数据；本行代销的产品由发行机构管理，本行负责销售适当性。</p>
        </section>
      </div>
    </div>
  );
}

function Product({ p, open, toggle, ask }) {
  const [amount, setAmount] = useState(String(p.minimum_minor / 100));
  const value = Number(amount);
  const ok = p.suitable && p.purchase_enabled && value * 100 >= p.minimum_minor;
  return (
    <div className={`panel product ${p.suitable ? "" : "muted-card"}`}>
      <div className="product-head">
        <div>
          <b className="product-name">{p.name}</b>
          <div className="issuer faint">{p.issuer === "本行" ? "本行发行" : `${p.issuer} 发行 · 本行代销`}</div>
          <div className="product-tags"><Tag tone={RISK_TONE[p.risk_level]}>R{p.risk_level}</Tag><Tag tone="neutral">{p.category}</Tag>
            {!p.suitable && <span className="faint" style={{ fontSize: 12.5 }}>{p.blocked_reason}</span>}</div>
        </div>
        <div className="product-bench">{p.benchmark}</div>
      </div>
      <div className="product-facts">
        <span><small>期限</small>{p.term_days ? `${p.term_days} 天` : "随存随取"}</span>
        <span><small>起购</small>{yuan(p.minimum_minor)}</span>
        <span className="wide"><small>申赎</small>{p.liquidity}</span>
      </div>
      <button className="link-btn" onClick={toggle} aria-expanded={open}>{open ? "收起" : "详情与风险"}</button>
      {open && (
        <div className="product-more">
          <p>{p.description}</p>
          <p className="risk-line"><b>风险提示</b>{p.risk_note}</p>
          {p.suitable ? (
            <div className="buy">
              <label>金额 <input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))} /> 元</label>
              <button className="btn small primary" disabled={!ok} onClick={() => ask(`申购${value}元${p.name}`)}>去申购</button>
              <span className="faint" style={{ fontSize: 12.5 }}>会在对话里生成确认卡，验证后才执行</span>
            </div>
          ) : <p className="faint" style={{ fontSize: 13 }}>{p.blocked_reason}。如果你的情况有变化，可以重新做风险测评。</p>}
        </div>
      )}
    </div>
  );
}
