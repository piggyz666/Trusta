import {BankCard,CardDesigner} from "../components/BankCard.jsx";
import { useEffect, useMemo, useState } from "react";
import { api, explain } from "../api.js";
import { Donut } from "../components/Charts.jsx";
import { Drawer, Icon, Tag } from "../components/ui.jsx";
import { TIER, dateOnly, dateTime, dayBucket, yuan } from "../format.js";

const KINDS = [["", "全部"], ["transfer", "转账"], ["card", "卡片"], ["wish", "心愿金"], ["wealth", "理财"], ["subscription", "订阅代扣"], ["retail", "生活订单"],
  ["credit", "贷款分期"], ["income", "入账"], ["automation", "联动规则"], ["payee", "收款人"]];
const GLYPH = { transfer: "transfer", card: "card", wish: "wish", wealth: "wealth", subscription: "subscription", retail: "card", income: "income",
  automation: "automation", payee: "payee", other: "spark" };
const CARD_STATUS = { ACTIVE: ["green", "正常"], FROZEN: ["amber", "已冻结"], LOST: ["red", "已挂失"], CANCELLED: ["neutral", "已注销"] };

// last result kept for the session: revisiting the page shows it instantly and refreshes in the background
const cache = { overview: null, items: {} };

export default function Overview({ openSession, ask, openProof, navigate }) {
  const [designCard,setDesignCard]=useState(null);
  const [data, setData] = useState(cache.overview);
  const [error, setError] = useState(null);
  const [kind, setKind] = useState("");
  const [items, setItems] = useState(cache.items[""] || null);
  const [detail, setDetail] = useState(null);

  useEffect(() => { api.overview().then((d) => { cache.overview = d; setData(d); }).catch((e) => setError(explain(e))); }, []);
  useEffect(() => { setItems(cache.items[kind] || null); api.activity(kind).then((r) => { cache.items[kind] = r.items; setItems(r.items); }).catch((e) => setError(explain(e))); }, [kind]);

  const groups = useMemo(() => {
    const out = [];
    for (const item of items || []) {
      const label = dayBucket(item.at);
      const g = out.find((x) => x.label === label) || (out.push({ label, list: [] }), out[out.length - 1]);
      g.list.push(item);
    }
    return out;
  }, [items]);

  if (error && !data) return <div className="page"><div className="inner"><div className="empty">{error}</div></div></div>;
  if (!data) return <div className="page"><div className="inner muted">读取中…</div></div>;
  const spend = data.spending;
  const change = spend.previous_minor ? Math.round(((spend.total_minor - spend.previous_minor) / spend.previous_minor) * 100) : null;

  return (
    <div className="page">
      <div className="inner">
        <header className="page-head">
          <h1>资金总览</h1>
          <p>所有余额、持仓、心愿金和每一笔经 TRUSTA 办理的记录都在这里，点任意一条可以看回执，或回到当时的对话。</p>
        </header>

        {data.protection && (
          <div className="panel" style={{ borderColor: "rgba(245,180,69,.45)", background: "var(--amber-bg)" }}>
            <b>转账保护已开启</b>，到 {dateTime(data.protection.until)} 前转账、新增收款人、心愿金下单和申购理财都会被拦截。
          </div>
        )}

        <div className="balance-hero">
          <div className="balance-total">
            <div>
              <div className="stat-label">资产合计（成本口径）</div>
              <div className="stat-value num">{yuan(data.finance?.assets_at_cost_minor ?? (data.total_minor + data.portfolio_minor + data.wish_funds.reduce((s, f) => s + f.balance_minor, 0)))}</div>
            </div>
            <div className="accounts">
              {data.accounts.map((a) => <span key={a.id}>{a.name} <b className="num">{yuan(a.balance_minor)}</b></span>)}
              <span>理财 <b className="num">{yuan(data.portfolio_minor)}</b></span>
            </div>
          </div>
          <div className="panel" style={{ display: "grid", gap: 8 }}>
            <div className="stat-label">{spend.current ? "本月消费" : "上月消费"}（{spend.month.replace("-", " 年 ")} 月）</div>
            <div className="stat-value num">{yuan(spend.total_minor)}</div>
            <span className="muted" style={{ fontSize: 13.5 }}>{spend.count} 笔{change !== null && spend.total_minor > 0 && <>，比上月{change >= 0 ? "多" : "少"} {Math.abs(change)}%</>}</span>
            <button className="btn small" style={{ justifySelf: "start" }} onClick={() => ask(spend.current ? "这个月花了多少钱" : "上个月花了多少钱")}>分析账单</button>
          </div>
        </div>

        {data.finance&&<section className="section panel"><h2>资产与还款</h2><div className="grid-2">
          <button className="btn" onClick={()=>navigate('wealth')}>黄金 {(data.finance.gold_units/100).toFixed(2)} 克 · 成本 {yuan(data.finance.gold_cost_minor)}</button>
          <button className="btn" onClick={()=>navigate('deposits')}>定期存款 {yuan(data.finance.deposit_principal_minor||0)}</button><button className="btn" onClick={()=>navigate('exchange')}>外币成本 {yuan(data.finance.fx_cost_minor||0)}</button><button className="btn" onClick={()=>navigate('credit')}>贷款与分期待还本金 {yuan(data.finance.debt_minor)}</button>
          <div>净资产（成本口径）<b className="num"> {yuan(data.finance.net_at_cost_minor)}</b></div>
          <div>{data.finance.next_due?`最近还款 ${data.finance.next_due} · ${yuan(data.finance.next_due_minor)}`:'暂无待还款'}</div>
        </div><p className="faint">黄金和外币按持仓成本计，定期存款按未支取本金计，理财按申购本金计；不等于当前市场价值。还款不会自动扣款。</p></section>}
        <div className="grid-2">
          <section className="section panel">
            <h2>消费构成</h2>
            {spend.categories.length
              ? <Donut items={spend.categories.map((c) => ({ key: c.category, label: c.label || c.category, value: c.net_spend ?? c.amount ?? 0 }))}
                  onSelect={(key) => ask(`${spend.current ? "这个月" : "上个月"}${spend.categories.find((c) => c.category === key)?.label || key}花了多少`)} />
              : <div className="muted">本月还没有消费。</div>}
          </section>
          <section className="section panel">
            <h2>理财持仓 <span className="count">{data.portfolio.length}</span></h2>
            {data.portfolio.length === 0 && <div className="muted">还没有持仓。<button className="btn small ghost" onClick={() => ask("推荐适合我的理财")}>看看推荐</button></div>}
            {data.portfolio.length > 0 && <div className="list">
              {data.portfolio.map((p) => (
                <div key={p.product_id} className="kv">
                  <span className="grow">{p.name} <span className="faint" style={{ fontSize: 12.5 }}>R{p.risk_level}</span></span>
                  <span className="num">{yuan(p.amount_minor)}</span>
                </div>
              ))}
            </div>}
          </section>
        </div>

        <section className="section">
          <h2>心愿金 <span className="count">{data.wish_funds.length}</span></h2>
          {data.wish_funds.length === 0 && <div className="panel muted">还没有心愿金。可以说“下个月妈妈生日，帮我存 2000 买礼物”。</div>}
          <div className="grid-2">
            {data.wish_funds.map((f) => {
              const pct = f.target_minor ? Math.min(100, Math.round((f.balance_minor / f.target_minor) * 100)) : 0;
              return (
                <div key={f.id} className="panel" style={{ display: "grid", gap: 10 }}>
                  <div className="kv"><b style={{ fontWeight: 600 }}><Icon name="wish" style={{ width: 16, height: 16, verticalAlign: -3, marginRight: 6 }} />{f.name}</b>
                    <Tag tone={f.status === "LOCKED" ? "neutral" : "green"}>{f.status === "LOCKED" ? `锁定至 ${dateOnly(f.release_at)}` : f.status}</Tag></div>
                  <div className="kv"><span className="num" style={{ fontSize: 22, fontWeight: 650 }}>{yuan(f.balance_minor)}</span>
                    <span className="muted num">目标 {yuan(f.target_minor)}</span></div>
                  <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><i style={{ width: pct + "%" }} /></div>
                  {f.events.length > 0 && (
                    <div style={{ display: "grid", gap: 2 }}>
                      {f.events.slice(0, 4).map((e) => (
                        <button key={e.id} className="event" style={{ padding: "6px 4px", gridTemplateColumns: "1fr auto" }} onClick={() => setDetail(e)}>
                          <span className="what"><b style={{ fontSize: 13.5 }}>{e.title}</b><small>{dateTime(e.at)} · {e.status_label}</small></span>
                          <span className="amount num">{e.amount_minor != null ? yuan(e.amount_minor) : ""}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <div className="grid-2">
          <section className="section panel">
            <h2>银行卡 <span className="count">{data.cards.length}</span></h2>
            <div className="list">
              {data.cards.map((c) => {
                const [tone, label] = CARD_STATUS[c.status] || ["neutral", c.status];
                return (
                  <div key={c.id} style={{ display: "grid", gap: 10 }}><BankCard card={c}/><button className="btn small" onClick={()=>setDesignCard(c)}>定制卡面</button>
                    <div className="kv"><span><Icon name="card" style={{ width: 16, height: 16, verticalAlign: -3, marginRight: 6 }} />
                      {c.type === "credit" ? "信用卡" : "借记卡"} ·{c.id.slice(-4)}</span><Tag tone={tone}>{label}</Tag></div>
                    <span className="faint" style={{ fontSize: 12.5 }}>
                      线上 {c.online ? "开" : "关"} · 境外 {c.international ? "开" : "关"} · 闪付 {c.contactless ? "开" : "关"} · 限额 {yuan(c.limit_minor)}
                    </span>
                  </div>
                );
              })}
            </div>
            <button className="btn small" style={{ marginTop: 12 }} onClick={() => setKind("card")}>卡片操作记录</button>
          </section>
          <section className="section panel">
            <h2>联动规则 <span className="count">{data.rules.length}</span></h2>
            {data.rules.length === 0 && <div className="muted">没有生效的规则。可以说“每月 10 号工资到账后给房东转 3000”。</div>}
            {data.rules.length > 0 && <div className="list">
              {data.rules.map((r) => (
                <div key={r.id} style={{ display: "grid" }}><b style={{ fontWeight: 560 }}>{r.name}</b>
                  <span className="faint" style={{ fontSize: 12.5 }}>{r.trigger} · 已触发 {r.runs} 次</span></div>
              ))}
            </div>}
          </section>
        </div>

        <section className="section">
          <h2>记录</h2>
          <div className="filters" role="group" aria-label="记录类型">
            {KINDS.map(([key, label]) => <button key={key} aria-pressed={kind === key} onClick={() => setKind(key)}>{label}</button>)}
          </div>
          <div className="timeline panel" style={{ padding: "4px 8px 8px", marginTop: 12 }}>
            {!items && <div className="muted" style={{ padding: 14 }}>读取中…</div>}
            {items && items.length === 0 && <div className="empty">还没有这类记录。</div>}
            {groups.map((g) => (
              <div key={g.label}>
                <h3>{g.label}</h3>
                {g.list.map((item) => <EventRow key={item.id} item={item} onOpen={() => setDetail(item)} />)}
              </div>
            ))}
          </div>
        </section>
      </div>
      {designCard && <CardDesigner card={designCard} onClose={()=>setDesignCard(null)} onSaved={()=>api.overview().then(setData)}/>}
      {detail && <RecordDrawer item={detail} onClose={() => setDetail(null)} openSession={openSession} openProof={openProof} />}
    </div>
  );
}

function headline(item) {
  const who = item.fields?.find((f) => ["收款人", "付款方", "卡片", "产品", "心愿金", "订阅"].includes(f.label));
  return who ? `${item.title} · ${who.value}` : item.title;
}

function EventRow({ item, onOpen }) {
  const tier = TIER[item.risk_level];
  const bad = ["FAILED", "BLOCKED", "DECLINED", "EXPIRED"].includes(item.status);
  return (
    <button className="event" onClick={onOpen}>
      <span className="glyph"><Icon name={GLYPH[item.kind] || "spark"} /></span>
      <span className="what">
        <b>{headline(item)}</b>
        <small>{dateTime(item.at)} · <span style={{ color: bad ? "var(--red)" : undefined }}>{item.status_label}</span>
          {tier && <> · {tier.past}</>}{item.source?.rule_id && " · 规则触发"}{item.quarantined && " · 备注已隔离"}</small>
      </span>
      <span className={`amount num ${item.direction === "in" ? "in" : ""}`}>
        {item.amount_minor != null ? (item.direction === "in" ? "+" : item.direction === "out" ? "−" : "") + yuan(item.amount_minor) : ""}
      </span>
    </button>
  );
}

function RecordDrawer({ item, onClose, openSession, openProof }) {
  const tier = TIER[item.risk_level];
  return (
    <Drawer title={item.title} onClose={onClose}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Tag tone={item.status === "SUCCEEDED" || item.status === "RECEIVED" ? "green" : ["FAILED", "BLOCKED", "DECLINED"].includes(item.status) ? "red" : "neutral"}>{item.status_label}</Tag>
        {tier && <Tag tone={tier.tone}>{tier.past}</Tag>}
        {item.verified && <Tag tone="green">银行已核验</Tag>}
      </div>
      {item.amount_minor != null && <div className="amount-big num">{yuan(item.amount_minor)}</div>}
      <dl className="fields">
        {(item.fields || []).filter((f) => f.label !== "金额").flatMap((f, i) => [<dt key={"t" + i}>{f.label}</dt>, <dd key={"d" + i}>{f.value}</dd>])}
        <dt>时间</dt><dd>{dateTime(item.at)}</dd>
        {item.receipt_id && <><dt>回执号</dt><dd className="mono">{item.receipt_id}</dd></>}
        {item.source?.channel && <><dt>发起渠道</dt><dd>{{ app: "网页 / H5", feishu: "飞书", scheduler: "定时任务" }[item.source.channel] || item.source.channel}</dd></>}
      </dl>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {item.source?.session_id && <button className="btn" onClick={() => { onClose(); openSession(item.source.session_id); }}>回到当时的对话</button>}
        {item.receipt_id && <button className="btn" onClick={() => openProof(`/api/audit/proof?receipt_id=${item.receipt_id}`)}>查看回执证明</button>}
      </div>
    </Drawer>
  );
}
