import { useEffect, useState } from "react";
import { api, explain } from "../api.js";
import { TIER, dateTime } from "../format.js";
import { Drawer, Icon, Tag } from "./ui.jsx";

const AGENT = { supervisor: "总控", planner: "规划", transaction: "交易", finance: "财务", wealth: "理财", service: "客服", runtime: "运行时" };
const OPS = { TRANSFER: "转账", CARD_FREEZE: "冻结卡片", CARD_UNFREEZE: "解冻卡片", CARD_REPORT_LOST: "挂失卡片", CARD_LIMIT_CHANGE: "调整卡片限额",
  CARD_TRANSACTION_CONTROL: "设置交易开关", SUBSCRIPTION_CANCEL: "取消订阅", DIRECT_DEBIT_DISABLE: "关闭代扣", INVESTMENT_PURCHASE: "申购理财",
  INVESTMENT_REDEEM: "赎回理财", FUND_LOCK: "锁定心愿金", FUND_RELEASE: "释放心愿金", WISH_ORDER: "心愿金下单", RISK_PROFILE_UPDATE: "更新风险等级",
  ADD_PAYEE: "添加收款人", CARD_MANAGEMENT: "卡片管理", PAYEE_MANAGEMENT: "收款人管理", WISH_FUND_MANAGEMENT: "心愿金", RISK_ASSESSMENT: "风险测评",
  BALANCE_QUERY: "余额查询", BILL_ANALYSIS: "账单分析", WEALTH_RECOMMENDATION: "理财推荐" };
const VERDICT = (v) => v === "ACCEPTED" ? ["ok", "通过校验"] : v === "RULES" ? ["ok", "规则引擎"] : v?.startsWith("INJECTION") ? ["bad", "判定为注入，整轮作废"]
  : v?.startsWith("UNAVAILABLE") ? ["warn", "功能未开通"] : ["warn", "需要追问：" + (v || "").replace("CLARIFY:", "")];

// 决策过程: what happened to one sentence — input guard, model tool calls and how the validator judged them, the
// six-agent tasks with their permission tier, authorization, bank verification, signed inter-agent messages and audit.
export function TraceDrawer({ target, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!target) return;
    setData(null); setError(null);
    api.trace(target.sessionId, target.messageId).then(setData).catch((e) => setError(explain(e)));
  }, [target?.sessionId, target?.messageId, target?.nonce]);

  return (
    <Drawer title="决策过程（开发者模式）" onClose={onClose}>
      {!data && !error && !waiting && <div className="muted">读取中…</div>}
      {error && <div className="muted">{error}</div>}
      {data && <div className="trace-body"><TraceBody data={data} /></div>}
    </Drawer>
  );
}

function Step({ tone = "ok", title, children }) {
  return <div className={`trace-step ${tone}`}><span className="pin" /><div><b>{title}</b>{children && <div style={{ marginTop: 4, display: "grid", gap: 4 }}>{children}</div>}</div></div>;
}

function TraceBody({ data }) {
  const t = data.trace;
  const guard = t.guard;
  return (
    <>
      <Step tone={guard ? "bad" : "ok"} title="1 · 输入安全检查">
        <span className="muted">{guard ? `拦截：${guard.category}` : "未发现注入、诈骗话术或越权查询"}</span>
      </Step>
      <Step tone={t.understanding === "deepseek" ? "ok" : "warn"} title="2 · 理解">
        <span className="muted">{t.understanding === "deepseek" ? "大模型提出候选工具调用（只能提议）" : t.understanding === "guard" ? "未调用模型" : "规则引擎"}
          {t.fallback_reason ? `（模型不可用：${t.fallback_reason}）` : ""}</span>
        {(t.calls || []).map((c, i) => {
          const [tone, label] = VERDICT(c.verdict);
          return <div key={i} className="mono"><span style={{ color: tone === "ok" ? "var(--green)" : tone === "bad" ? "var(--red)" : "var(--amber)" }}>●</span> {c.name} — {label}</div>;
        })}
      </Step>
      {data.tasks.map((task) => (
        <Step key={task.task_id} title={`3 · 六 Agent 执行：${OPS[task.task_type] || task.task_type}`} tone={["COMPLETED", "WAITING_USER", "SCHEDULED"].includes(task.status) ? "ok" : "warn"}>
          <span className="muted">任务状态 {task.status}</span>
          {task.actions.map((a) => {
            const tier = TIER[a.policy?.risk_level];
            return (
              <div key={a.action_id} className="panel" style={{ padding: 10, display: "grid", gap: 4 }}>
                <div className="kv"><b style={{ fontWeight: 560 }}>{OPS[a.action_type] || a.action_type} <span className="mono">{a.action_type}</span></b>{tier && <Tag tone={tier.tone}>{tier.label}</Tag>}</div>
                <div className="mono">策略 {a.policy?.decision} · 授权 {a.authorization?.status || "—"}{a.authorization?.auth_method ? `（${a.authorization.auth_method}）` : ""}</div>
                <div className="mono">银行核验 {a.verification?.status || "—"} · 回执 {a.receipt_id ? a.receipt_id.slice(0, 8) : "—"}</div>
                <div className="mono">动作哈希 {a.action_hash.slice(0, 16)}…</div>
              </div>
            );
          })}
          {task.agent_messages?.length > 0 && (
            <div style={{ display: "grid", gap: 3 }}>
              <span className="muted">Agent 间签名消息 {task.agent_messages.length} 条</span>
              {task.agent_messages.map((m, i) => (
                <div key={i} className="mono">
                  {m.message_signature_valid && m.evidence_signature_valid && m.causal_parents_known ? "✓" : "✗"} {AGENT[m.sender] || m.sender} → {AGENT[m.receiver] || m.receiver} · {m.type}
                </div>
              ))}
            </div>
          )}
        </Step>
      ))}
      <Step tone={data.audit_chain_valid && data.trace_anchored_in_audit ? "ok" : "bad"} title="4 · 审计">
        <span className="muted">{data.audit_chain_valid ? "审计哈希链完整" : "审计链校验失败"}；{data.trace_anchored_in_audit ? "本条决策记录已锚定在审计链中，无法事后改写" : "决策记录未锚定"}</span>
      </Step>
    </>
  );
}

export function EvidenceDrawer({ target, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.get(`/api/chat/sessions/${target.sessionId}/evidence/${target.taskId}`).then(setData).catch((e) => setError(explain(e)));
  }, [target.sessionId, target.taskId]);
  function download() {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trusta-evidence-${target.taskId.slice(0, 8)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  return (
    <Drawer title="签名证据包" onClose={onClose}>
      {error && <div className="muted">{error}</div>}
      {!data && !error && !waiting && <div className="muted">读取中…</div>}
      {data && <>
        <span>这笔查询或操作由六个 Agent 协作完成，每条 Agent 间消息都带发送方签名，并由运行时按因果顺序加签。证据包可以离线独立验证，不需要相信这台服务器。</span>
        <div className="panel" style={{ display: "grid", gap: 6 }}>
          <div className="kv"><span className="muted">消息与交接记录</span><span className="num">{(data.bundle?.evidence || data.bundle?.records || []).length || "—"}</span></div>
          <div className="kv"><span className="muted">公钥登记</span><span>{data.public_registry ? "已附带" : "—"}</span></div>
        </div>
        <div><div className="stat-label">离线验证命令</div><div className="mono" style={{ padding: 10, background: "var(--surface-2)", borderRadius: 8 }}>{data.verify_with}</div></div>
        <button className="btn primary" onClick={download}>下载证据包</button>
      </>}
    </Drawer>
  );
}

export function ProofDrawer({ url, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    // A receipt is sealed into the next audit checkpoint a few seconds after execution; until then, wait and retry.
    let alive = true, tries = 0, timer = 0;
    const load = () => api.get(url).then((d) => { if (alive) { setWaiting(false); setData(d); } }).catch((e) => {
      if (!alive) return;
      if (e.code === "AUDIT_CHECKPOINT_PENDING" && tries++ < 20) { setWaiting(true); timer = setTimeout(load, 2000); }
      else { setWaiting(false); setError(explain(e)); }
    });
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [url]);
  const receipt = data?.receipt || data?.record?.receipt;
  return (
    <Drawer title="银行回执与证明" onClose={onClose}>
      {waiting && <div className="muted">回执已生成，正在等待写入下一个签名检查点（通常几秒钟）…</div>}
      {error && <div className="muted">{error}</div>}
      {!data && !error && !waiting && <div className="muted">读取中…</div>}
      {data && <>
        <div className="result ok" style={{ padding: 0 }}><span className="mark">✓</span>
          <div>{data.anchor_verified ? "回执已纳入签名检查点，验证通过" : "回执已记录，检查点等待后台锚定"}</div></div>
        <dl className="fields">
          {receipt && <><dt>回执号</dt><dd className="mono">{receipt.receipt_id}</dd><dt>执行时间</dt><dd>{dateTime(receipt.executed_at)}</dd>
            <dt>核验</dt><dd>{receipt.verification_status}</dd></>}
          {data.checkpoint && <><dt>检查点</dt><dd className="mono">{data.checkpoint.checkpoint_id}</dd></>}
          {data.merkle_proof && <><dt>Merkle 证明</dt><dd>{data.merkle_proof.length || 0} 层</dd></>}
        </dl>
        <span className="faint" style={{ fontSize: 13 }}>回执记录了授权编号、动作哈希、执行前后状态哈希；任何一处被改动，证明都会校验失败。</span>
      </>}
    </Drawer>
  );
}
