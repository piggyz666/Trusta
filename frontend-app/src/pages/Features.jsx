import { useState } from "react";
import { api, explain } from "../api.js";
import { Drawer, Tag, useToast } from "../components/ui.jsx";
import { bankName } from "../format.js";

const EXAMPLES = {
  transfer: ["给张三转500元", "每月1号给房东转3000"], payee_add: ["新增收款人李四，尾号6688"], split: ["昨晚聚餐600，我和张三李四AA"],
  bills: ["这个月花了多少钱", "上个月外卖花了多少"], balance: ["查一下余额"], wealth: ["推荐适合我的理财", "根据我的消费推荐理财", "今天金价多少"],
  risk_assessment: ["做一下风险测评"], cards: ["我的卡丢了", "关闭信用卡境外交易"], subscriptions: ["我有哪些订阅", "最近有什么要续费"], life: ["帮我查下周五北京到上海的机票", "上海最近有什么演唱会"],
  wish_fund: ["下个月妈妈生日，帮我存2000买礼物"], automation: ["外卖每月超过800就提醒我", "余额低于1000提醒我"],
  automation_income: ["工资到账后存1000到心愿金"], automation_tighten: ["单笔超过5000自动冻结信用卡"],
  trip: ["下周去日本玩5天，帮我安排一下"], income: ["最近有哪些入账"], protection: ["有人自称警察让我转账到安全账户"],
  knowledge: ["转账限额是多少", "怎么挂失"], nlu: ["帮我把上次给房东那笔再转一次"],
};
const STATUS = { AVAILABLE: ["green", "可用"], DEGRADED: ["amber", "降级可用"], UNAVAILABLE: ["neutral", "未开通"], OFF: ["neutral", "已关闭"] };

export default function Features({ capabilities, refresh, ask, navigate }) {
  const toast = useToast();
  const [busy, setBusy] = useState(null);
  const [blocked, setBlocked] = useState(null);
  if (!capabilities) return <div className="page"><div className="inner muted">读取中…</div></div>;

  const groups = [];
  for (const f of capabilities.features) {
    const g = groups.find((x) => x.name === f.group) || (groups.push({ name: f.group, list: [] }), groups[groups.length - 1]);
    g.list.push(f);
  }
  const sum = capabilities.summary;

  async function flip(f) {
    if (f.locked) { toast(f.lock_reason); return; }
    if (f.status === "UNAVAILABLE") { setBlocked(f); return; }
    setBusy(f.id);
    try { await api.toggle(f.id, !f.enabled); await refresh(); toast(f.enabled ? `已关闭：${short(f.title)}，助手不会再使用它` : `已开启：${short(f.title)}`); }
    catch (e) { toast(explain(e), "error"); } finally { setBusy(null); }
  }

  return (
    <div className="page">
      <div className="inner">
        <header className="page-head">
          <h1>功能</h1>
          <p>能用哪些功能，取决于你接入的大模型、银行接口和渠道。关掉的功能，助手看不到也调用不了；灰色的开关点一下会告诉你需要接什么。</p>
        </header>
        <div className="grid-3 stats">
          <div className="panel"><div className="stat-label">可用</div><div className="stat-value num">{sum.AVAILABLE + sum.DEGRADED}</div></div>
          <div className="panel"><div className="stat-label">已关闭</div><div className="stat-value num">{sum.OFF}</div></div>
          <div className="panel"><div className="stat-label">未开通</div><div className="stat-value num">{sum.UNAVAILABLE}</div></div>
        </div>
        <div className="panel" style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center", fontSize: 14 }}>
          <span><span className="muted">大模型</span> {capabilities.llm.model || "未接入（规则模式）"}</span>
          <span><span className="muted">银行</span> {bankName(capabilities.bank)}</span>
          <span><span className="muted">渠道</span> {["网页 / H5", ...(capabilities.channels?.feishu ? ["飞书"] : [])].join("、")}</span>
          <span className="spacer" style={{ flex: 1 }} />
          <button className="btn small" onClick={() => navigate("settings", { tab: "model" })}>更改接入</button>
        </div>
        {groups.map((g) => (
          <section key={g.name} className="section">
            <h2>{g.name} <span className="count">{g.list.length}</span></h2>
            <div className="panel" style={{ padding: 0 }}>
              {g.list.map((f) => {
                const [tone, label] = STATUS[f.status];
                return (
                  <div key={f.id} className="feature">
                    <div className="name">{f.title}<Tag tone={tone}>{label}</Tag>{f.locked && <Tag tone="neutral">安全底线</Tag>}</div>
                    <button role="switch" aria-checked={f.enabled} aria-label={f.title} disabled={busy === f.id}
                      className={`switch ${f.locked ? "locked" : ""} ${f.status === "UNAVAILABLE" ? "unavailable" : ""}`} onClick={() => flip(f)} />
                    <div className="desc">{f.locked ? f.lock_reason : f.reason || "已就绪"}</div>
                    {f.enabled && EXAMPLES[f.id] && (
                      <div className="try">{EXAMPLES[f.id].map((e) => <button key={e} className="chip" style={{ fontSize: 12.5, padding: "3px 10px" }} onClick={() => ask(e)}>{e}</button>)}</div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      {blocked && (
        <Drawer title="这个功能还没开通" onClose={() => setBlocked(null)}>
          <b style={{ fontSize: 16 }}>{blocked.title}</b>
          <span className="muted">{blocked.reason}</span>
          <div>
            <div className="stat-label" style={{ marginBottom: 6 }}>开通方法</div>
            <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 6 }}>
              {blocked.how_to_enable.map((h) => <li key={h}>{h}</li>)}
            </ol>
          </div>
          <span className="faint" style={{ fontSize: 13 }}>接入后在“设置 → 模型与接口”里点“重新检测”，功能会自动亮起。没开通时，助手不会假装能做。</span>
          <button className="btn primary" onClick={() => { setBlocked(null); navigate("settings", { tab: blocked.id === "feishu" ? "feishu" : "model" }); }}>去设置</button>
        </Drawer>
      )}
    </div>
  );
}

const short = (title) => title.split("（")[0];
