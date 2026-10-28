import { useEffect, useState } from "react";
import { api, explain } from "../api.js";
import { BankGaps, Icon, Tag, useToast } from "../components/ui.jsx";
import { dateOnly, yuan } from "../format.js";

let cache = null;
const DAY = 86400;

function inDays(at) {
  const d = Math.round((at - Date.now() / 1000) / DAY);
  return d <= 0 ? "今天" : d === 1 ? "明天" : `${d} 天后`;
}

export default function Bills({ ask }) {
  const toast = useToast();
  const [data, setData] = useState(cache);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = (confirm) => api.bills(confirm).then((d) => { cache = d.requires_confirmation ? null : d; setData(d); }).catch((e) => setError(explain(e)));
  useEffect(() => { load(); }, []);

  async function mark(key, state) {
    setBusy(key);
    try { const d = await api.markRecurring(key, state); cache = d; setData(d); toast(state === "CONFIRMED" ? "已计入你的订阅，扣费前会提醒你" : state === "DISMISSED" ? "已忽略，不再提醒" : "已恢复"); }
    catch (e) { toast(explain(e), "error"); }
    setBusy(null);
  }

  async function removeMine(id) {
    setBusy(id);
    try { const d = await api.removeUserSub(id); cache = d; setData(d); toast("已从你的订阅里移除，这不会影响对方的自动续费"); }
    catch (e) { toast(explain(e), "error"); }
    setBusy(null);
  }

  if (error && !data) return <div className="page"><div className="inner"><div className="empty">{error}</div></div></div>;
  if (!data) return <div className="page"><div className="inner muted">读取中…</div></div>;
  if (data.requires_confirmation) return (
    <div className="page"><div className="inner">
      <header className="page-head"><h1>账单与订阅</h1><p>订阅与代扣涉及扣费信息，按权限规则需要你确认后再显示。</p></header>
      <section className="section panel">
        <h2>{data.confirm.title} <Tag tone="amber">需你确认</Tag></h2>
        <div style={{ display: "grid", gap: 8, margin: "10px 0 16px" }}>
          {data.confirm.fields.map((f, i) => <div key={i} style={{ display: "grid", gridTemplateColumns: "88px 1fr", gap: 12 }}>
            <span className="muted">{f.label}</span><span>{f.value}</span></div>)}
        </div>
        <div className="actions"><button className="btn primary" onClick={() => load(true)}>确认查询</button></div>
      </section>
    </div></div>);
  const subs = data.items.filter((i) => i.kind === "subscription" || i.kind === "user");
  const debits = data.items.filter((i) => i.kind === "direct_debit");
  const fresh = data.detected.filter((d) => d.state === "NEW");
  const confirmed = data.detected.filter((d) => d.state === "CONFIRMED");
  const dismissed = data.detected.filter((d) => d.state === "DISMISSED");

  return (
    <div className="page">
      <div className="inner">
        <header className="page-head">
          <h1>账单与订阅</h1>
          <p>所有会自动扣钱的项目：登记在银行的订阅和代扣，以及 TRUSTA 从流水里识别出来的周期扣费。</p>
        </header>
        <BankGaps items={data.unavailable} />

        <div className="bills-hero">
          <div className="panel">
            <div className="stat-label">每月固定支出</div>
            <div className="stat-value num">{yuan(data.monthly_minor)}</div>
            <span className="muted" style={{ fontSize: 13.5 }}>约合每年 {yuan(data.yearly_minor)} · {subs.length + debits.length + confirmed.length} 项</span>
          </div>
          <div className="panel">
            <div className="stat-label">未来 30 天将扣费</div>
            <div className="stat-value num">{yuan(data.upcoming.reduce((s, u) => s + u.amount_minor, 0))}</div>
            <span className="muted" style={{ fontSize: 13.5 }}>{data.upcoming.length} 笔{data.upcoming[0] && <>，最近一笔 {inDays(data.upcoming[0].at)}</>}</span>
          </div>
          <div className={`panel ${fresh.length ? "attention" : ""}`}>
            <div className="stat-label">待你确认的疑似订阅</div>
            <div className="stat-value num">{fresh.length}</div>
            <span className="muted" style={{ fontSize: 13.5 }}>{fresh.length ? "不认识的扣费要尽快处理" : "没有新的周期扣费"}</span>
          </div>
        </div>

        {fresh.length > 0 && (
          <section className="section">
            <h2><Icon name="spark" style={{ width: 16, height: 16 }} />识别到的新订阅 <span className="count">{fresh.length}</span></h2>
            <div className="grid-2">
              {fresh.map((d) => <Detected key={d.key} d={d} busy={busy === d.key} mark={mark} ask={ask} />)}
            </div>
            <p className="faint rule-note">{data.rule}</p>
          </section>
        )}

        <section className="section">
          <h2>即将扣费 <span className="count">{data.upcoming.length}</span></h2>
          {data.upcoming.length === 0 && <div className="panel muted">未来 30 天没有自动扣费。</div>}
          {data.upcoming.length > 0 && (
            <div className="upcoming panel">
              {data.upcoming.map((u, i) => (
                <div key={i} className="up-row">
                  <span className="up-date"><b className="num">{dateOnly(u.at).slice(5)}</b><small>{inDays(u.at)}</small></span>
                  <span className="up-what"><b>{u.merchant}</b><small>{u.kind_label}{u.estimated && " · 按历史间隔估算"}</small></span>
                  <span className="num up-amount">{yuan(u.amount_minor)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <div className="grid-2">
          <section className="section panel">
            <h2>订阅 <span className="count">{subs.length + confirmed.length}</span></h2>
            <div className="list">
              {subs.map((s) => <Row key={s.id} item={s} ask={ask} remove={removeMine} busy={busy === s.id} />)}
              {confirmed.map((d) => (
                <div key={d.key}>
                  <span className="grow"><b style={{ fontWeight: 560 }}>{d.merchant}</b>
                    <small className="faint row-sub">每月 · 已扣 {d.count} 次 · 你确认的订阅，银行未登记</small></span>
                  <span className="num">{yuan(d.amount_minor)}</span>
                  <button className="btn small ghost" disabled={busy === d.key} onClick={() => mark(d.key, "NEW")}>撤销</button>
                </div>
              ))}
              {subs.length + confirmed.length === 0 && <div className="muted">没有订阅。</div>}
            </div>
          </section>
          <section className="section panel">
            <h2>代扣 <span className="count">{debits.length}</span></h2>
            <div className="list">
              {debits.map((s) => <Row key={s.id} item={s} ask={ask} />)}
              {debits.length === 0 && <div className="muted">没有代扣协议。</div>}
            </div>
          </section>
        </div>

        {data.history.length > 0 && (
          <section className="section panel">
            <h2>取消与关闭记录</h2>
            <div className="list">
              {data.history.map((h) => (
                <div key={h.id}><span className="grow">{h.title}{h.fields?.[0] && <> · {h.fields[0].value}</>}<small className="faint row-sub">{dateOnly(h.at)}</small></span>
                  <Tag tone={h.status === "SUCCEEDED" ? "green" : "neutral"}>{h.status_label}</Tag></div>
              ))}
            </div>
          </section>
        )}

        {dismissed.length > 0 && (
          <section className="section">
            <h2>已忽略 <span className="count">{dismissed.length}</span></h2>
            <div className="list">
              {dismissed.map((d) => (
                <div key={d.key}><span className="grow">{d.merchant} <span className="faint">每月 {yuan(d.amount_minor)}</span></span>
                  <button className="btn small ghost" disabled={busy === d.key} onClick={() => mark(d.key, "NEW")}>恢复</button></div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function Detected({ d, busy, mark, ask }) {
  return (
    <div className="panel detected">
      <div className="kv"><b>{d.merchant}</b><Tag tone="amber">疑似订阅</Tag></div>
      <div className="kv"><span className="num" style={{ fontSize: 22, fontWeight: 650 }}>{yuan(d.amount_minor)}<small className="muted" style={{ fontSize: 13, fontWeight: 400 }}> / 约 {d.interval_days} 天</small></span></div>
      <span className="muted" style={{ fontSize: 13 }}>
        {dateOnly(d.first_at)} 起已扣 {d.count} 次，下次约 {dateOnly(d.next_estimate)}。这个商户没有登记在你的订阅或代扣里。
      </span>
      <div className="actions">
        <button className="btn small primary" disabled={busy} onClick={() => mark(d.key, "CONFIRMED")}>是我的订阅，扣费前提醒我</button>
        <button className="btn small" disabled={busy} onClick={() => ask(`我不认识${d.merchant}每月扣的${(d.amount_minor / 100).toFixed(2)}元，怎么办`)}>不认识这笔</button>
        <button className="btn small ghost" disabled={busy} onClick={() => mark(d.key, "DISMISSED")}>忽略</button>
      </div>
    </div>
  );
}

function Row({ item, ask, remove, busy }) {
  const active = item.status === "ACTIVE";
  if (item.kind === "user") return (
    <div>
      <span className="grow"><b style={{ fontWeight: 560 }}>{item.merchant}</b>
        <small className="faint row-sub">{item.frequency_label} · 你在对话里登记的，银行未登记</small></span>
      <span className="num">{item.amount_minor != null ? yuan(item.amount_minor) : <span className="faint">金额未知</span>}</span>
      <button className="btn small ghost" disabled={busy} onClick={() => remove(item.id)}>移除</button>
    </div>
  );
  return (
    <div>
      <span className="grow"><b style={{ fontWeight: 560 }}>{item.merchant}</b>
        <small className="faint row-sub">{item.frequency_label}{active && item.next_charge_at ? ` · 下次 ${dateOnly(item.next_charge_at)}` : ""}</small></span>
      <span className="num">{yuan(item.amount_minor)}</span>
      {active ? (item.cancelable &&
        <button className="btn small ghost" onClick={() => ask(item.kind === "subscription" ? `取消${item.merchant}订阅` : `关闭${item.merchant}代扣`)}>
          {item.kind === "subscription" ? "取消" : "关闭"}</button>)
        : <Tag tone="neutral">{item.status === "CANCELLED" ? "已取消" : "已关闭"}</Tag>}
    </div>
  );
}
