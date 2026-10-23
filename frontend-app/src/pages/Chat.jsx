import { useEffect, useMemo, useRef, useState } from "react";
import { api, explain } from "../api.js";
import { Card, PlanCard } from "../cards/Cards.jsx";
import { stirInk } from "../components/Backdrop.jsx";
import { Icon, useToast } from "../components/ui.jsx";
import { uid } from "../format.js";

const SUGGESTIONS = ["查一下余额", "这个月花了多少钱", "给张三转500元", "我的卡丢了", "推荐适合我的理财", "外卖每月超过800就提醒我"];
const STAGES = { "turn.started": "收到", "guard.result": "安全检查", "intent.recognized": "理解意图", "tool.started": "调用银行", "plan.created": "生成计划", card: "生成卡片" };

function Composer({ value, setValue, onSend, busy, onStop, autoFocus, placeholder }) {
  const ref = useRef(null);
  const formRef = useRef(null);
  const lastWisp = useRef(0);
  const submit = (text) => { stirInk("send", formRef.current); onSend(text); };
  const change = (next) => {
    const now = performance.now();
    if (next.length > value.length && now - lastWisp.current > 70) { lastWisp.current = now; stirInk("type", formRef.current); }
    setValue(next);
  };
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(160, el.scrollHeight) + "px";
  }, [value]);
  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus]);
  return (
    <form ref={formRef} className="composer" onSubmit={(e) => { e.preventDefault(); if (value.trim() && !busy) submit(value.trim()); }}>
      <label className="visually-hidden" htmlFor="composer">输入要办理的事</label>
      <textarea id="composer" ref={ref} rows={1} value={value} maxLength={500} placeholder={placeholder || "例如：给张三转500元"}
        onChange={(e) => change(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (value.trim() && !busy) submit(value.trim()); } }} />
      {busy && onStop
        ? <button type="button" className="icon-btn stop" onClick={onStop} aria-label="中断"><Icon name="stop" /></button>
        : <button className="btn primary send" disabled={!value.trim() || busy}>发送</button>}
    </form>
  );
}

export default function Chat({ sessionId, onSessionCreated, onChanged, navigate, openTrace, openEvidence, openProof, capabilities, prefill, clearPrefill }) {
  const toast = useToast();
  const [view, setView] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [optimistic, setOptimistic] = useState(null); // the message being sent
  const [stages, setStages] = useState([]);
  const [entered, setEntered] = useState(true);
  const threadRef = useRef(null);
  const currentId = useRef(sessionId);

  async function load(id = sessionId) {
    if (!id) { setView(null); return; }
    try { setView(await api.session(id)); } catch (error) { toast(explain(error), "error"); }
  }
  useEffect(() => { currentId.current = sessionId; setOptimistic(null); setStages([]); load(sessionId); }, [sessionId]);
  useEffect(() => { if (prefill) { send(prefill); clearPrefill?.(); } }, [prefill]);
  useEffect(() => { const el = threadRef.current; if (el) el.scrollTop = el.scrollHeight; }, [view, optimistic, stages]);

  const pendingIds = useMemo(() => new Set((view?.pending || []).filter((c) => c.type === "confirm").map((c) => c.action_id)), [view]);
  const pendingRuleIds = useMemo(() => new Set((view?.pending || []).filter((c) => c.type === "automation_confirm").map((c) => c.bundle_id)), [view]);

  async function send(text) {
    if (busy) return;
    let id = currentId.current;
    let revision = view?.revision;
    setBusy(true);
    setInput("");
    setOptimistic(text);
    setStages([]);
    try {
      if (!id || (view && !view.active)) {
        const created = await api.createSession();
        id = created.session_id;
        revision = created.revision;
        currentId.current = id;
        onSessionCreated?.(id);
      }
      const messageId = "w" + uid();
      await api.send(id, { message_id: messageId, revision, text }, (name, data) => {
        if (STAGES[name]) setStages((list) => list.includes(STAGES[name]) ? list : [...list, STAGES[name]]);
      });
    } catch (error) {
      toast(explain(error), "error");
      if (error.code === "CHAT_STALE") await load(id);
    } finally {
      setBusy(false);
      setOptimistic(null);
      setStages([]);
      await load(id);
      onChanged?.();
    }
  }

  async function interrupt() {
    if (!view) return;
    try { await api.interrupt(view.session_id, view.revision); await load(); } catch (error) { toast(explain(error), "error"); }
  }

  const ctx = { sessionId: view?.session_id, pendingIds, pendingRuleIds, send, reload: () => { load(currentId.current); onChanged?.(); },
    navigate, openEvidence: (taskId) => openEvidence(view?.session_id, taskId), openProof };

  const messages = view?.messages || [];
  const empty = !messages.some((m) => m.role === "user") && !optimistic;
  const nlu = capabilities?.features?.find((f) => f.id === "nlu");
  const plan = view?.plan;
  const planActive = plan?.nodes?.some((n) => !["SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED"].includes(n.status));

  if (empty) {
    return (
      <div className="hero-wrap">
        <section className={`hero ${entered ? "enter" : ""}`} onAnimationEnd={(e) => e.target === e.currentTarget.querySelector(".chips") && setEntered(false)}>
          <div className="hero-inner">
            <img className="hero-mark" src="/mark.png" alt="" width="64" height="64" />
            <h2>今天想办点什么？</h2>
            <p>一句话转账、查账单、管卡片、安排理财。资金操作都会先给你一张确认卡，由你决定。</p>
            <Composer value={input} setValue={setInput} onSend={send} busy={busy} autoFocus />
            <div className="chips">{SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        </section>
        <div className="hero-foot">
          <span><Icon name="shield" style={{ width: 14, height: 14 }} />AI 只能提议，不能替你授权</span>
          <span><Icon name="lock" style={{ width: 14, height: 14 }} />大额与高风险操作需要验证码</span>
          {nlu && nlu.status !== "AVAILABLE" && <span style={{ color: "var(--amber)" }}>未接入大模型：只能识别标准说法</span>}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="thread" ref={threadRef}>
        <div className="inner">
          {messages.map((m, i) => m.role === "user" ? (
            <div key={"u" + i + m.message_id} className="msg-user">{m.text}</div>
          ) : (
            <div key={"a" + i + m.message_id} className="msg-ai">
              <img className="avatar" src="/mark.png" alt="" width="28" height="28" />
              <div className="body">
                {m.text && <div className="text">{m.text}</div>}
                {(m.cards || []).map((card) => <Card key={card.card_id || card.action_id} card={card} ctx={ctx} />)}
                {openTrace && <MessageMeta message={m} messages={messages} sessionId={view.session_id} openTrace={openTrace} />}
              </div>
            </div>
          ))}
          {optimistic && (
            <>
              <div className="msg-user">{optimistic}</div>
              <div className="msg-ai"><img className="avatar" src="/mark.png" alt="" width="28" height="28" />
                <div className="body"><div className="pipeline" aria-live="polite">
                  {(stages.length ? stages : ["收到"]).map((s, i, all) => <span key={s} className={i === all.length - 1 ? "live" : "done"}>{s}</span>)}
                </div></div></div>
            </>
          )}
          {plan && planActive && !optimistic && <PlanCard plan={plan} ctx={ctx} />}
        </div>
      </div>
      <div className="dock">
        <div className="inner">
          {view && !view.active ? (
            <div className="readonly-bar"><span>这段对话已结束（超过 24 小时或已关闭），只能查看。</span>
              <button className="btn small primary" onClick={() => send(input || "你好")}>开始新对话</button></div>
          ) : (
            <>
              <Composer value={input} setValue={setInput} onSend={send} busy={busy} onStop={view?.pending?.length ? interrupt : null}
                placeholder={view?.state === "HANDED_OFF" ? "已转人工，人工客服会在这里回复你" : "继续说，如“改成800”“算了不转了”"} />
              <div className="hint">确认卡上的按钮才是授权，聊天里说“确认”不会执行任何操作。</div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function MessageMeta({ message, messages, sessionId, openTrace }) {
  // The decision trace is keyed by the user message that started the turn.
  const index = messages.indexOf(message);
  const user = [...messages.slice(0, index)].reverse().find((m) => m.role === "user");
  // Only a direct reply to a user message has a trace (reminders, decisions and rule firings do not).
  if (!user || message.message_id !== user.message_id) return null;
  return (
    <div className="msg-meta">
      <button onClick={() => openTrace(sessionId, user.message_id)}>查看决策过程</button>
    </div>
  );
}
