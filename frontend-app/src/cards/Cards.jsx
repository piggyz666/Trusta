import { useEffect, useMemo, useState } from "react";
import { api, explain } from "../api.js";
import { Bars, Columns, Donut } from "../components/Charts.jsx";
import { Icon, Tag, useToast } from "../components/ui.jsx";
import { TIER, dateOnly, dateTime, yuan } from "../format.js";

// Every card is interactive, but anything that changes money or account state is turned into a *message* (so it goes
// through understanding → proposal → confirmation card) or a decision on an existing card. No card executes by itself.

const CATEGORY = { FOOD: "餐饮", GROCERIES: "日用采购", TRANSPORT: "交通", SHOPPING: "购物", OTHER: "其他" };
const CATEGORY_CODE = Object.fromEntries(Object.entries(CATEGORY).map(([k, v]) => [v, k]));

export function Card({ card, ctx }) {
  switch (card.type) {
    case "confirm": return <ConfirmCard card={card} ctx={ctx} />;
    case "result": return <ResultCard card={card} ctx={ctx} />;
    case "report": return <ReportCard card={card} ctx={ctx} />;
    case "clarify": return <ClarifyCard card={card} ctx={ctx} />;
    case "risk_alert": return <RiskCard card={card} ctx={ctx} />;
    case "knowledge": return <KnowledgeCard card={card} ctx={ctx} />;
    case "navigate": return <NavigateCard card={card} ctx={ctx} />;
    case "questionnaire": return <QuestionnaireCard card={card} ctx={ctx} />;
    case "automation_confirm": return <RuleCard card={card} ctx={ctx} />;
    case "handoff": return <HandoffCard card={card} />;
    default: return null;
  }
}

function Sources({ sources, ctx }) {
  if (!sources?.length) return null;
  return (
    <div className="sources" aria-label="依据">
      <span style={{ border: 0, paddingLeft: 0 }}>依据</span>
      {sources.map((s, i) => s.kind === "EVIDENCE"
        ? <button key={i} className="evidence" onClick={() => ctx.openEvidence(s.ref)}>{s.label}</button>
        : <span key={i}>{s.label}</span>)}
    </div>
  );
}

function Countdown({ until }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  const left = Math.max(0, Math.round(until - now));
  if (!until) return null;
  return <span className="expiry">{left > 0 ? `授权 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} 后失效` : "已失效，请重新发起"}</span>;
}

// ---------- confirmation (yellow / red) ----------
function ConfirmCard({ card, ctx }) {
  const pending = ctx.pendingIds.has(card.action_id);
  const tier = TIER[card.risk_level] || TIER.YELLOW;
  const red = card.risk_level === "RED";
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [method, setMethod] = useState("SMS");
  const [code, setCode] = useState("");
  const [sms, setSms] = useState(null);
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState("");
  const amountField = card.fields.find((f) => "amount_minor" in f);
  const others = card.fields.filter((f) => f !== amountField);

  async function decide(approve) {
    setBusy(true);
    try {
      const verification = approve && red ? { method, code: code.trim() } : undefined;
      const result = await api.decide(ctx.sessionId, card, approve, verification);
      if (result.error) toast(result.reply.text, "error");
      ctx.reload();
    } catch (error) {
      toast(explain(error), "error");
      ctx.reload();
    } finally {
      setBusy(false);
    }
  }

  async function sendSms() {
    try {
      const result = await api.sms(ctx.sessionId, card.action_id);
      setSms(result);
      if (result.dev_code) toast(`模拟手机收到短信：验证码 ${result.dev_code}（5 分钟内有效）`, "info", 9000);
    } catch (error) { toast(explain(error), "error"); }
  }

  return (
    <article className={`card tier-${card.risk_level === "RED" ? "red" : "yellow"}`} aria-label={card.title}>
      <div className="card-head">
        <h3>{card.title}</h3>
        <Tag tone={tier.tone}>{tier.label}</Tag>
      </div>
      <div className="card-body">
        {amountField && (
          <div>
            <div className="stat-label">{amountField.label}</div>
            {editing ? (
              <form className="edit-inline" onSubmit={(e) => { e.preventDefault(); if (amount) { ctx.send(`改成${amount}元`); setEditing(false); } }}>
                <input className="input num" inputMode="decimal" autoFocus value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} placeholder="新金额" />
                <button className="btn small primary" disabled={!amount}>改好了</button>
                <button type="button" className="btn small ghost" onClick={() => setEditing(false)}>取消</button>
              </form>
            ) : (
              <div className="amount-big num">{amountField.value}
                {pending && card.action_type === "TRANSFER" && (
                  <button className="icon-btn" style={{ display: "inline-grid", verticalAlign: "middle" }} onClick={() => { setAmount(String(amountField.amount_minor / 100)); setEditing(true); }} aria-label="修改金额"><Icon name="edit" /></button>
                )}
              </div>
            )}
          </div>
        )}
        <dl className="fields">{others.map((f, i) => <FieldRow key={i} field={f} />)}</dl>
        {card.risk_reasons?.length > 0 && <ul className="reasons">{card.risk_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
        {pending && red && (
          <div className="verify">
            <div className="row">
              <span className="muted" style={{ fontSize: 13 }}>验证方式</span>
              <div className="segmented" role="group">
                <button aria-pressed={method === "SMS"} onClick={() => setMethod("SMS")}>短信验证码</button>
                <button aria-pressed={method === "TOTP"} onClick={() => setMethod("TOTP")}>认证器动态码</button>
              </div>
            </div>
            <div className="row">
              <input className="input otp num" inputMode="numeric" maxLength={6} value={code} placeholder="••••••" aria-label="6 位验证码"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              {method === "SMS" && <button className="btn small" onClick={sendSms}>{sms ? "重新发送" : "发送验证码"}</button>}
            </div>
            {sms?.dev_code && <div className="phone-toast">模拟手机 · 短信：【TRUSTA】验证码 <b className="num">{sms.dev_code}</b>，仅用于本笔操作。</div>}
          </div>
        )}
      </div>
      <div className="card-foot">
        {pending ? (
          <>
            <button className="btn primary" disabled={busy || (red && code.length !== 6)} onClick={() => decide(true)}>{red ? "验证并确认" : "确认"}</button>
            <button className="btn ghost" disabled={busy} onClick={() => decide(false)}>拒绝</button>
            <span className="spacer" />
            <Countdown until={card.expires_at} />
          </>
        ) : <span className="muted" style={{ fontSize: 13 }}>这张确认卡已处理</span>}
      </div>
    </article>
  );
}

function FieldRow({ field }) {
  return <><dt>{field.label}</dt><dd className={"amount_minor" in field ? "num" : ""}>{field.value}</dd></>;
}

function ResultCard({ card, ctx }) {
  const ok = card.status === "SUCCEEDED" || card.status === "ACTIVE" || card.status === "PROTECTION_ACTIVE";
  const bad = card.status === "FAILED";
  return (
    <article className="card">
      <div className={`result ${ok ? "ok" : bad ? "bad" : "neutral"}`}>
        <span className="mark">{ok ? "✓" : bad ? "!" : "–"}</span>
        <div style={{ display: "grid", gap: 6 }}>
          <span>{card.message}</span>
          {card.proof_url && <button className="btn small ghost" style={{ justifySelf: "start", padding: "2px 0" }} onClick={() => ctx.openProof(card.proof_url)}>查看银行回执与证明</button>}
        </div>
      </div>
    </article>
  );
}

// ---------- reports ----------
function ReportCard({ card, ctx }) {
  const body = {
    BALANCE: Balance, SPENDING_SUMMARY: Spending, ANNUAL_REPORT: Spending, SPENDING_QUERY: SpendingQuery, PRODUCT_LIST: Products,
    PRODUCT_COMPARE: Compare, SUBSCRIPTIONS: Subscriptions, RENEWAL_REMINDERS: Renewals, CARDS: Cards, SPLIT_REQUEST: Split,
    INCOME: Income, AUTOMATIONS: Rules, WISH_FUNDS: WishFunds, STANDING_PLAN: StandingPlan,
  }[card.report_type] || Generic;
  const titles = {
    BALANCE: "账户余额", SPENDING_SUMMARY: `${card.period || ""} 账单`, ANNUAL_REPORT: `${card.period || ""} 年度账单`, SPENDING_QUERY: "消费查询",
    PRODUCT_LIST: "适合你的理财", PRODUCT_COMPARE: "产品对比", SUBSCRIPTIONS: "订阅与代扣", RENEWAL_REMINDERS: "即将扣费",
    CARDS: "我的银行卡", SPLIT_REQUEST: "AA 收款", INCOME: "最近入账", AUTOMATIONS: "联动规则", WISH_FUNDS: "心愿金", STANDING_PLAN: "周期转账计划",
  };
  return (
    <article className="card tier-green">
      <div className="card-head"><h3>{titles[card.report_type] || "查询结果"}</h3><Tag tone="green">只读查询</Tag></div>
      <div className="card-body">
        {body({ card, ctx })}
        <Sources sources={card.sources} ctx={ctx} />
      </div>
    </article>
  );
}

function Balance({ card }) {
  return (
    <div className="grid-2">
      {card.items.map((a) => (
        <div key={a.account_id}><div className="stat-label">{a.display}</div><div className="amount-big num">{yuan(a.balance_minor)}</div></div>
      ))}
    </div>
  );
}

function Spending({ card, ctx }) {
  const [view, setView] = useState("donut");
  const [selected, setSelected] = useState(null);
  const [records, setRecords] = useState(null);
  const items = (card.breakdown || []).map((b) => ({ key: b.category, label: b.category, value: b.amount_minor }));
  useEffect(() => {
    if (!selected || records || !/^\d{4}-\d{2}$/.test(card.period || "")) return;
    api.get(`/api/transactions?account_id=acct-demo&month=${card.period}`).then((r) => setRecords(r.records)).catch(() => setRecords([]));
  }, [selected]);
  const detail = (records || []).filter((r) => CATEGORY[r.category] === selected || r.category === CATEGORY_CODE[selected]);
  return (
    <>
      <div className="kv">
        <div><div className="stat-label">合计消费</div><div className="amount-big num">{yuan(card.summary?.total_minor)}</div>
          <div className="faint" style={{ fontSize: 13 }}>{card.summary?.tx_count} 笔已入账交易</div></div>
        <div className="segmented" role="group" aria-label="图表">
          <button aria-pressed={view === "donut"} onClick={() => setView("donut")}>占比</button>
          <button aria-pressed={view === "bars"} onClick={() => setView("bars")}>排行</button>
        </div>
      </div>
      {view === "donut" ? <Donut items={items} selected={selected} onSelect={(k) => setSelected(k === selected ? null : k)} />
        : <Bars items={[...items].sort((a, b) => b.value - a.value)} selected={selected} onSelect={(k) => setSelected(k === selected ? null : k)} />}
      {selected && (
        <div>
          <div className="stat-label" style={{ marginBottom: 6 }}>{selected} 明细</div>
          {records === null ? <div className="faint">读取中…</div> : detail.length === 0 ? <div className="faint">没有可展示的明细</div> : (
            <table className="table"><tbody>{detail.map((r) => (
              <tr key={r.id}><td>{r.merchant}</td><td className="faint">{dateTime(r.occurred_at)}</td>
                <td className="right num">{r.kind === "REFUND" ? "+" : ""}{yuan(r.amount)}</td></tr>))}</tbody></table>
          )}
        </div>
      )}
      {card.anomalies?.length > 0 && (
        <div>
          <div className="stat-label" style={{ marginBottom: 6 }}>需要留意 {card.anomalies.length} 笔</div>
          <div className="list">{card.anomalies.map((a) => (
            <div key={a.transaction_id}><div className="grow"><b style={{ fontWeight: 560 }}>{a.merchant}</b>
              <div className="faint" style={{ fontSize: 12.5 }}>{a.reasons.join("；")}</div></div><span className="num">{yuan(a.amount_minor)}</span></div>))}
          </div>
          <div className="chips" style={{ marginTop: 10 }}>
            <button className="chip" onClick={() => ctx.send("有大额异常消费就冻结卡并转人工")}>以后出现大额消费就提醒我</button>
            <button className="chip" onClick={() => ctx.send("先把卡冻结一下")}>先冻结卡片</button>
          </div>
        </div>
      )}
    </>
  );
}

function SpendingQuery({ card }) {
  const [selected, setSelected] = useState(null);
  const months = (card.by_month || []).map((m) => ({ key: m.month, label: m.month.slice(5) + "月", value: m.net_spend }));
  const cats = (card.breakdown || []).map((b) => ({ key: b.category, label: b.label, value: b.net_spend }));
  const cmp = card.comparison;
  return (
    <>
      <div className="kv">
        <div><div className="stat-label">{card.period}{card.filters?.category_label ? ` · ${card.filters.category_label}` : ""}{card.filters?.merchant ? ` · ${card.filters.merchant}` : ""}</div>
          <div className="amount-big num">{yuan(card.summary?.total_minor)}</div></div>
        {cmp && cmp.previous_net_spend > 0 && (
          <Tag tone="neutral">{cmp.net_change > 0 ? "比上期多" : "比上期少"} {yuan(Math.abs(cmp.net_change))}</Tag>
        )}
      </div>
      {months.length > 1 && <Columns items={months} />}
      {cats.length > 1 && <Bars items={cats} selected={selected} onSelect={setSelected} />}
      {card.top_merchants?.length > 0 && (
        <table className="table"><thead><tr><th>商户</th><th>笔数</th><th className="right">金额</th></tr></thead>
          <tbody>{card.top_merchants.map((m) => <tr key={m.merchant}><td>{m.merchant}</td><td className="num">{m.count}</td><td className="right num">{yuan(m.net_spend)}</td></tr>)}</tbody></table>
      )}
    </>
  );
}

function Products({ card, ctx }) {
  const [pick, setPick] = useState(null);
  const [amount, setAmount] = useState("");
  const chosen = card.items.find((p) => p.product_id === pick);
  return (
    <>
      <div className="faint" style={{ fontSize: 13 }}>你的风险等级 R{card.customer_risk_level}。点选一款可直接发起申购（仍需确认和验证）。</div>
      <table className="table">
        <thead><tr><th>产品</th><th>风险</th><th>期限</th><th className="right">起购</th></tr></thead>
        <tbody>{card.items.map((p) => (
          <tr key={p.product_id} className="row-select" aria-selected={pick === p.product_id} onClick={() => setPick(p.product_id)}>
            <td>{p.name}<div className="faint" style={{ fontSize: 12 }}>{p.risk_note}</div></td><td>R{p.risk_level}</td>
            <td>{p.term_days ? `${p.term_days} 天` : "随存随取"}</td><td className="right num">{yuan(p.minimum_minor)}</td></tr>))}</tbody>
      </table>
      {chosen && (
        <form className="edit-inline" onSubmit={(e) => { e.preventDefault(); ctx.send(`申购${amount}元${chosen.name}`); }}>
          <input className="input num" inputMode="decimal" placeholder={`至少 ${chosen.minimum_minor / 100}`} value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} />
          <button className="btn small primary" disabled={!amount || Number(amount) * 100 < chosen.minimum_minor}>申购 {chosen.name}</button>
        </form>
      )}
      {card.disclaimer && <div className="faint" style={{ fontSize: 12.5 }}>{card.disclaimer}</div>}
    </>
  );
}

function Compare({ card, ctx }) {
  const rows = [["风险等级", (p) => `R${p.risk_level}`], ["期限", (p) => p.term_days ? `${p.term_days} 天` : "随存随取"], ["起购金额", (p) => yuan(p.minimum_minor)], ["说明", (p) => p.risk_note]];
  return (
    <>
      <table className="table">
        <thead><tr><th /> {card.items.map((p) => <th key={p.product_id}>{p.name}</th>)}</tr></thead>
        <tbody>{rows.map(([label, fn]) => <tr key={label}><td className="muted">{label}</td>{card.items.map((p) => <td key={p.product_id} className="num">{fn(p)}</td>)}</tr>)}
          <tr><td /> {card.items.map((p) => <td key={p.product_id}><button className="btn small" onClick={() => ctx.send(`我想申购${p.name}`)}>申购这款</button></td>)}</tr></tbody>
      </table>
      <div className="faint" style={{ fontSize: 12.5 }}>不预测收益；理财非存款，投资需谨慎。</div>
    </>
  );
}

function Subscriptions({ card, ctx }) {
  return (
    <>
      <div className="list">{card.items.map((s) => (
        <div key={s.id}>
          <div className="grow"><b style={{ fontWeight: 560 }}>{s.merchant}</b>
            <div className="faint" style={{ fontSize: 12.5 }}>{s.kind === "direct_debit" ? "代扣" : "订阅"} · 下次扣费 {dateOnly(s.next_charge_at)}</div></div>
          <span className="num">{yuan(s.amount_minor)}/月</span>
          {s.status === "ACTIVE" && <button className="btn small" onClick={() => ctx.send(s.kind === "direct_debit" ? `关闭${s.merchant}代扣` : `取消${s.merchant}`)}>{s.kind === "direct_debit" ? "关闭" : "取消"}</button>}
        </div>))}
      </div>
      {card.suspected?.length > 0 && <div className="faint" style={{ fontSize: 13 }}>从扣款记录发现 {card.suspected.length} 个未登记的疑似订阅（{card.suspected.map((s) => `${s.merchant} ${yuan(s.amount_minor)}`).join("、")}），需联系商户取消。</div>}
      <div className="chips"><button className="chip" onClick={() => ctx.send("把取消订阅省下的钱每月1号上午9点定投到低风险现金产品")}>把省下的钱拿去定投</button></div>
    </>
  );
}

function Renewals({ card }) {
  return <div className="list">{card.items.map((r, i) => (
    <div key={i}><div className="grow">{r.merchant}<div className="faint" style={{ fontSize: 12.5 }}>{dateOnly(r.next_charge_at)}</div></div>
      {r.status !== "UPCOMING" && <Tag tone="amber">三天内</Tag>}<span className="num">{yuan(r.amount_minor)}</span></div>))}</div>;
}

function Cards({ card, ctx }) {
  return card.items.map((c) => (
    <div key={c.id} className="panel" style={{ padding: 14, display: "grid", gap: 10 }}>
      <div className="kv"><b>{c.display}</b><Tag tone={c.status === "ACTIVE" ? "green" : c.status === "FROZEN" ? "amber" : "red"}>{{ ACTIVE: "正常", FROZEN: "已冻结", LOST: "已挂失" }[c.status] || c.status}</Tag></div>
      <div className="kv muted" style={{ fontSize: 13.5 }}><span>单日限额</span><span className="num">{yuan(c.limit_minor)}</span></div>
      {[["online", "线上支付"], ["international", "境外交易"], ["contactless", "非接触支付"]].map(([key, label]) => (
        <div key={key} className="kv" style={{ alignItems: "center" }}>
          <span>{label}</span>
          <button className="switch" role="switch" aria-checked={!!c[key]} aria-label={label} onClick={() => ctx.send(`${c[key] ? "关闭" : "开启"}${label}`)} />
        </div>))}
      <div className="chips">
        {c.status === "ACTIVE" && <button className="chip" onClick={() => ctx.send("冻结我的卡")}>冻结</button>}
        {c.status === "FROZEN" && <button className="chip" onClick={() => ctx.send("解冻我的卡")}>解冻</button>}
        {c.status !== "LOST" && <button className="chip" onClick={() => ctx.send("我的卡丢了，帮我挂失")}>挂失</button>}
        <button className="chip" onClick={() => ctx.send("把卡单日限额调到2000")}>调整限额</button>
      </div>
      <div className="faint" style={{ fontSize: 12.5 }}>开关会先生成确认卡；开启类操作需要验证码，关闭类操作只需确认。</div>
    </div>));
}

function Split({ card }) {
  return (
    <>
      <div className="kv"><span className="muted">总额</span><span className="num">{yuan(card.total_minor)}</span></div>
      {card.self_share_minor > 0 && <div className="kv"><span className="muted">你承担</span><span className="num">{yuan(card.self_share_minor)}</span></div>}
      <div className="list">{card.items.map((i) => <div key={i.payee}><span className="grow">{i.payee}</span><Tag tone="neutral">待对方付款</Tag><span className="num">{yuan(i.amount_minor)}</span></div>)}</div>
      <div className="faint" style={{ fontSize: 12.5 }}>只发出收款请求，不会从任何人的账户扣款。</div>
    </>
  );
}

function Income({ card }) {
  return (
    <>
      {card.quarantined_count > 0 && <div className="phone-toast">有 {card.quarantined_count} 笔入账的付款信息夹带了类似指令的文字，已隔离并记录审计；这些文字不会发给 AI，也不会被执行。</div>}
      <div className="list">{card.items.map((i) => (
        <div key={i.credit_id}><div className="grow">{i.payer}<div className="faint" style={{ fontSize: 12.5 }}>{i.memo} · {dateTime(i.at)}</div></div>
          {i.quarantined && <Tag tone="red">已隔离</Tag>}<span className="num" style={{ color: "var(--green)" }}>+{yuan(i.amount_minor)}</span></div>))}
        {card.items.length === 0 && <div className="faint">近 90 天没有入账记录</div>}
      </div>
    </>
  );
}

function Rules({ card, ctx }) {
  const toast = useToast();
  return (
    <div className="list">{card.items.map((r) => (
      <div key={r.rule_id}><div className="grow"><b style={{ fontWeight: 560 }}>{r.name}</b>
        <div className="faint" style={{ fontSize: 12.5 }}>{r.trigger}：{r.actions.join("、")} · 已触发 {r.runs}/{r.max_runs}</div></div>
        <button className="btn small ghost danger" onClick={async () => { try { await api.post(`/api/chat/automations/${r.rule_id}/cancel`); toast("规则已停用"); ctx.reload(); } catch (e) { toast(explain(e), "error"); } }}>停用</button>
      </div>))}
      {card.items.length === 0 && <div className="faint">还没有联动规则。试试说“外卖每月超过800就提醒我”。</div>}
    </div>
  );
}

function WishFunds({ card }) {
  return <div className="list">{(card.items || []).map((f) => (
    <div key={f.id} style={{ display: "grid", gap: 6 }}>
      <div className="kv"><b style={{ fontWeight: 560 }}>{f.name}</b><span className="num">{yuan(f.balance_minor)}</span></div>
      {f.release_at && <span className="faint" style={{ fontSize: 12.5 }}>{dateOnly(f.release_at)} 释放回活期</span>}
    </div>))}</div>;
}

function StandingPlan({ card }) {
  return <div className="list">{card.items.map((i) => (
    <div key={i.event_id}><div className="grow">{i.frequency}给 {i.payee} 转账 · 共 {i.count} 次<div className="faint" style={{ fontSize: 12.5 }}>首次 {i.first_run}{i.memo ? ` · 备注 ${i.memo}` : ""}</div></div>
      <span className="num">{i.amount}</span></div>))}</div>;
}

function Generic({ card }) {
  const items = card.items || [];
  return items.length ? <div className="list">{items.map((i, n) => <div key={n} className="mono">{JSON.stringify(i)}</div>)}</div> : <div className="faint">已完成查询。</div>;
}

// ---------- other cards ----------
function ClarifyCard({ card, ctx }) {
  if (!card.options?.length) return null; // the question itself is the message text; free text goes in the composer
  return <div className="chips">{card.options.map((o) => <button key={o.value} className="chip" onClick={() => ctx.send(o.value)}>{o.label}</button>)}</div>;
}

function RiskCard({ card, ctx }) {
  const toast = useToast();
  return (
    <article className="card tier-red">
      <div className="card-head"><h3>{{ FRAUD_SUSPECTED: "疑似诈骗，已拦截", PROMPT_INJECTION: "拒绝越权请求", INFO_PROBING: "拒绝查询他人信息", ACCOUNT_LOCKED: "会话已锁定" }[card.category] || "风险提示"}</h3>
        <Tag tone="red">已拦截</Tag></div>
      <div className="card-body">
        <span>{card.message}</span>
        {card.offer?.type === "ENABLE_PROTECTION" && (
          <div className="verify" style={{ background: "var(--surface-2)", borderColor: "var(--line-strong)" }}>
            <span>担心自己正被人诱导转账？开启后 {card.offer.hours} 小时内对外转账全部暂停，只能由银行人员核实后解除。</span>
            <div><button className="btn primary" onClick={async () => { try { await api.protect(ctx.sessionId, card.offer.hours); toast("已开启转账保护"); ctx.reload(); } catch (e) { toast(explain(e), "error"); } }}>{card.offer.label}</button></div>
          </div>
        )}
      </div>
    </article>
  );
}

function KnowledgeCard({ card, ctx }) {
  return (
    <article className="card">
      <div className="card-head"><h3>{card.title}</h3><Tag tone="neutral">规则说明</Tag></div>
      <div className="card-body"><span>{card.text}</span>
        <div className="sources"><span style={{ border: 0, paddingLeft: 0 }}>依据</span>{card.sources.map((s, i) => <span key={i} title={s.ref}>{s.label}</span>)}</div></div>
    </article>
  );
}

const PAGE_ROUTES = { transfer: "chat", payees: "overview", bills: "overview", wealth: "overview", cards: "overview", subscriptions: "overview",
  wish_funds: "overview", security: "settings", audit: "overview" };

function NavigateCard({ card, ctx }) {
  return <div><button className="btn" onClick={() => ctx.navigate(PAGE_ROUTES[card.page] || "overview")}>打开{card.title}</button> <span className="faint" style={{ fontSize: 13 }}>{card.note}</span></div>;
}

function QuestionnaireCard({ card, ctx }) {
  const [answers, setAnswers] = useState({});
  const [done, setDone] = useState(false);
  const toast = useToast();
  const complete = card.questions.every((q) => answers[q.id]);
  return (
    <article className="card">
      <div className="card-head"><h3>{card.title}</h3><Tag tone="neutral">{Object.keys(answers).length}/{card.questions.length}</Tag></div>
      <div className="card-body">
        {card.questions.map((q) => (
          <div className="question" key={q.id}><span>{q.text}</span>
            <div className="options">{q.options.map((o) => <button key={o.value} aria-pressed={answers[q.id] === o.value} disabled={done}
              onClick={() => setAnswers({ ...answers, [q.id]: o.value })}>{o.label}</button>)}</div></div>))}
      </div>
      <div className="card-foot">
        <button className="btn primary" disabled={!complete || done} onClick={async () => {
          try { await api.assessment(ctx.sessionId, answers); setDone(true); ctx.reload(); } catch (e) { toast(explain(e), "error"); }
        }}>提交测评</button>
        <span className="faint" style={{ fontSize: 13 }}>提交后会生成“更新风险等级”的确认卡</span>
      </div>
    </article>
  );
}

function RuleCard({ card, ctx }) {
  const pending = ctx.pendingRuleIds.has(card.bundle_id);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  async function decide(approve) {
    setBusy(true);
    try { await api.decideRule(ctx.sessionId, card, approve); ctx.reload(); } catch (e) { toast(explain(e), "error"); ctx.reload(); } finally { setBusy(false); }
  }
  return (
    <article className="card tier-yellow">
      <div className="card-head"><h3>{card.title}</h3><Tag tone="amber">需你确认</Tag></div>
      <div className="card-body">
        {card.rules.map((r) => (
          <div key={r.rule_id} className="plan-step" style={{ gridTemplateColumns: "22px 1fr" }}>
            <span className="node"><Icon name="automation" style={{ width: 12, height: 12 }} /></span>
            <div><b style={{ fontWeight: 560 }}>{r.name}</b><div>{r.trigger}</div>
              <ul className="reasons" style={{ marginTop: 4 }}>{r.actions.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
          </div>))}
        <ul className="reasons">{card.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </div>
      <div className="card-foot">
        {pending ? <>
          <button className="btn primary" disabled={busy} onClick={() => decide(true)}>启用规则</button>
          <button className="btn ghost" disabled={busy} onClick={() => decide(false)}>不用了</button>
        </> : <span className="muted" style={{ fontSize: 13 }}>已处理</span>}
      </div>
    </article>
  );
}

function HandoffCard({ card }) {
  return (
    <article className="card">
      <div className="result neutral"><span className="mark"><Icon name="payee" style={{ width: 16, height: 16 }} /></span>
        <div>已转接人工客服（工单 {card.handoff_id}）。人工期间我不会执行任何操作；说“回到智能助手”可以切回。</div></div>
    </article>
  );
}

export function PlanCard({ plan, ctx }) {
  if (!plan?.nodes?.length) return null;
  const label = { SUCCEEDED: "已完成", AWAITING_CONFIRMATION: "等你确认", QUEUED: "等上一步完成", SCHEDULED: "已排期", FAILED: "未完成",
    CANCELLED: "已取消", SKIPPED: "已跳过", NEEDS_INPUT: "等你补充" };
  const icon = { SUCCEEDED: "✓", FAILED: "!", CANCELLED: "–", SKIPPED: "–" };
  return (
    <article className="card">
      <div className="card-head"><h3>执行计划</h3><Tag tone="neutral">{plan.nodes.filter((n) => n.status === "SUCCEEDED").length}/{plan.nodes.length}</Tag></div>
      <div className="card-body">
        {plan.goal && <span className="muted" style={{ fontSize: 13.5 }}>{plan.goal}</span>}
        <div className="plan">{plan.nodes.map((n, i) => (
          <div key={n.id} className={`plan-step ${n.status}`}>
            <span className="node">{icon[n.status] || i + 1}</span>
            <div>{n.title}{n.scheduled_at && <small>{dateTime(n.scheduled_at)} 到期后提醒你确认</small>}</div>
            <span className="faint" style={{ fontSize: 12.5 }}>{label[n.status] || n.status}</span>
          </div>))}</div>
      </div>
    </article>
  );
}
