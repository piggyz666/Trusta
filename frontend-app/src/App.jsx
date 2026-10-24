import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, auth, bootstrapAuth, explain } from "./api.js";
import { bankName } from "./format.js";
import Sidebar from "./components/Sidebar.jsx";
import Backdrop from "./components/Backdrop.jsx";
import { EvidenceDrawer, ProofDrawer, TraceDrawer } from "./components/Trace.jsx";
import { Icon, useToast } from "./components/ui.jsx";
import Chat from "./pages/Chat.jsx";

const loaders = [() => import("./pages/Overview.jsx"), () => import("./pages/Features.jsx"), () => import("./pages/Settings.jsx")];
const Overview = lazy(loaders[0]);
const Features = lazy(loaders[1]);
const Settings = lazy(loaders[2]);
// fetch the other pages' code while idle so switching never waits on a download
(window.requestIdleCallback || ((f) => setTimeout(f, 1200)))(() => loaders.forEach((l) => l()));

// "?s=<session>" opens that conversation (used by the Feishu "去 APP 验证" button for red actions)
const linkedSession = (() => { const v = new URLSearchParams(location.search).get("s"); return v && /^[A-Za-z0-9_-]{8,64}$/.test(v) ? v : null; })();
if (linkedSession) history.replaceState(null, "", location.pathname + location.hash);
const boot = bootstrapAuth();
const TITLES = { overview: "资金总览", features: "功能", settings: "设置" };

function readRoute() {
  const path = location.pathname.replace(/\/+$/, "");
  const page = path.split("/")[1] || "chat";
  return ["chat", "overview", "features", "settings"].includes(page) ? page : "chat";
}

export default function App() {
  const toast = useToast();
  const [authed, setAuthed] = useState(!!auth.token());
  const [route, setRoute] = useState(readRoute);
  const [sessionId, setSessionId] = useState(linkedSession);
  const [sessions, setSessions] = useState([]);
  const [capabilities, setCapabilities] = useState(null);
  const [railOpen, setRailOpen] = useState(false);
  const [trace, setTrace] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [proof, setProof] = useState(null);
  const [prefill, setPrefill] = useState(null);
  const [settingsTab, setSettingsTab] = useState(null);

  const refreshSessions = useCallback(async () => {
    try { setSessions((await api.sessions()).sessions); } catch (e) { if (e.status === 401) setAuthed(false); }
  }, []);
  const refreshCapabilities = useCallback(async () => {
    try { setCapabilities(await api.capabilities()); } catch (e) { if (e.status === 401) setAuthed(false); }
  }, []);

  useEffect(() => { if (authed) { refreshSessions(); refreshCapabilities(); } }, [authed]);
  useEffect(() => {
    const onPop = () => setRoute(readRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function navigate(page, options = {}) {
    if (page !== route) history.pushState(null, "", page === "chat" ? "/" : "/" + page);
    setRoute(page);
    setRailOpen(false);
    if (options.tab) setSettingsTab(options.tab);
    if (page === "features" || page === "settings") refreshCapabilities();
  }
  function selectSession(id) { setSessionId(id); setTrace(null); navigate("chat"); }
  function newChat() { setSessionId(null); setTrace(null); navigate("chat"); }
  function ask(text) { setSessionId(null); navigate("chat"); setPrefill(text); }
  // Customers see results, not reasoning: the decision trace is only reachable with developer mode on (Settings → 外观).
  const [devMode, setDevMode] = useState(() => { try { return localStorage.getItem("trusta-dev") === "1"; } catch { return false; } });
  function openTrace(sid, messageId) { setTrace({ sessionId: sid, messageId, nonce: Math.random() }); }

  if (!authed) return <><Backdrop /><Connect pairCode={boot.pairCode} onConnected={() => setAuthed(true)} /></>;

  const llm = capabilities?.llm;
  const status = !capabilities ? { text: "连接中…" }
    : llm?.state === "NOT_CONFIGURED" ? { text: "规则模式 · 未接入大模型", warn: true }
    : { text: `${bankName(capabilities.bank)} · ${llm?.model || "大模型"}` };
  const current = sessions.find((s) => s.session_id === sessionId);
  const title = route === "chat" ? (current?.title || "") : TITLES[route];

  return (
    <>
    <Backdrop dim={!(route === "chat" && !sessionId)} />
    <div className="shell">
      <Sidebar open={railOpen} onClose={() => setRailOpen(false)} route={route} sessionId={sessionId} sessions={sessions}
        onSelect={selectSession} onNew={newChat} onNavigate={navigate} onChanged={refreshSessions} status={status} />
      <main className="main">
        <div className="topbar">
          <button className="icon-btn menu-button" onClick={() => setRailOpen(true)} aria-label="打开菜单"><Icon name="menu" /></button>
          <h1>{title}</h1>
          <span className="spacer" />
          {route === "chat" && sessionId && (
            <button className="icon-btn" onClick={newChat} aria-label="新对话" title="新对话"><Icon name="plus" /></button>
          )}
        </div>
        {route === "chat" && (
          <Chat sessionId={sessionId} onSessionCreated={(id) => { setSessionId(id); refreshSessions(); }} onChanged={refreshSessions}
            navigate={navigate} openTrace={devMode ? openTrace : null} capabilities={capabilities}
            openEvidence={(sid, taskId) => setEvidence({ sessionId: sid, taskId })} openProof={(url) => setProof(url)}
            prefill={prefill} clearPrefill={() => setPrefill(null)} />
        )}
        <Suspense fallback={<div className="page"><div className="inner muted">读取中…</div></div>}>
          {route === "overview" && <Overview openSession={selectSession} ask={ask} openProof={(url) => setProof(url)} />}
          {route === "features" && <Features capabilities={capabilities} refresh={refreshCapabilities} ask={ask} navigate={navigate} />}
          {route === "settings" && <Settings capabilities={capabilities} refresh={refreshCapabilities} tab={settingsTab} setTab={setSettingsTab} devMode={devMode} setDevMode={setDevMode}
            onSignOut={() => { auth.clear(); setAuthed(false); }} />}
        </Suspense>
      </main>
      {trace && <TraceDrawer target={trace} onClose={() => setTrace(null)} />}
      {evidence && <EvidenceDrawer target={evidence} onClose={() => setEvidence(null)} />}
      {proof && <ProofDrawer url={proof} onClose={() => setProof(null)} />}
    </div>
    </>
  );
}

function Connect({ pairCode, onConnected }) {
  const toast = useToast();
  const [code, setCode] = useState(pairCode || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function claim(value = code) {
    if (!/^\d{6}$/.test(value)) { setError("配对码是 6 位数字"); return; }
    setBusy(true); setError(null);
    try {
      const device = /iPhone|Android|Mobile/i.test(navigator.userAgent) ? "手机浏览器" : "浏览器";
      const result = await api.claimPairing(value, device);
      auth.setDevice(result.device_token);
      toast("已连接，这台设备可以随时在电脑的设置里撤销");
      onConnected();
    } catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  useEffect(() => { if (pairCode) claim(pairCode); }, []);

  return (
    <div className="connect">
      <div className="panel">
        <div className="brand" style={{ padding: 0 }}><img className="brand-mark" src="/mark.png" alt="" width="28" height="28" />TRUSTA<small>银行助手</small></div>
        <h2 style={{ margin: 0, fontSize: 20 }}>连接到你的 TRUSTA</h2>
        <span className="muted">TRUSTA 运行在你自己的电脑上。在电脑上打开“设置 → 访问与设备”，扫码或输入屏幕上的 6 位配对码。</span>
        <form onSubmit={(e) => { e.preventDefault(); claim(); }} style={{ display: "grid", gap: 10 }}>
          <label className="stat-label" htmlFor="pair">配对码</label>
          <input id="pair" className="input code-big" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="000000" style={{ textAlign: "center" }} />
          {error && <span style={{ color: "var(--red)", fontSize: 14 }}>{error}</span>}
          <button className="btn primary" disabled={busy || code.length !== 6}>{busy ? "连接中…" : "连接"}</button>
        </form>
        <span className="faint" style={{ fontSize: 13 }}>在电脑上：直接用启动时打印的链接打开即可，不需要配对码。</span>
      </div>
    </div>
  );
}
