import { useEffect, useRef, useState } from "react";
import { api, explain } from "../api.js";
import { chatBucket } from "../format.js";
import { Icon, useToast } from "./ui.jsx";

const NAV = [["chat", "对话", "chat"], ["overview", "资金总览", "ledger"], ["features", "功能", "spark"], ["settings", "设置", "gear"]];

export default function Sidebar({ open, onClose, route, sessionId, sessions, onSelect, onNew, onNavigate, onChanged, status }) {
  const toast = useToast();
  const [menu, setMenu] = useState(null); // { id, top }
  const [renaming, setRenaming] = useState(null);
  const [title, setTitle] = useState("");
  const listRef = useRef(null);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menu]);

  const groups = [];
  for (const s of sessions) {
    const label = chatBucket(s.updated_at);
    const group = groups.find((g) => g.label === label) || (groups.push({ label, items: [] }), groups[groups.length - 1]);
    group.items.push(s);
  }

  async function rename(id) {
    try { await api.rename(id, title); setRenaming(null); onChanged(); } catch (e) { toast(explain(e), "error"); }
  }
  async function remove(id) {
    try { await api.remove(id); if (id === sessionId) onNew(); onChanged(); toast("已删除这段对话，银行记录与审计不受影响"); } catch (e) { toast(explain(e), "error"); }
  }

  return (
    <>
      {open && <div className="rail-backdrop" onClick={onClose} />}
      <nav className={`rail ${open ? "open" : ""}`} aria-label="导航">
        <div className="brand"><img className="brand-mark" src="/mark.png" alt="" width="28" height="28" />TRUSTA<small>银行助手</small></div>
        <button className="new-chat" onClick={onNew}><Icon name="plus" style={{ width: 16, height: 16 }} />新对话</button>
        <div className="history" ref={listRef}>
          {groups.length === 0 && <div className="history-empty">还没有对话。说一句话就会出现在这里。</div>}
          {groups.map((g) => (
            <div key={g.label}>
              <h2>{g.label}</h2>
              {g.items.map((s) => renaming === s.session_id ? (
                <form key={s.session_id} onSubmit={(e) => { e.preventDefault(); rename(s.session_id); }} style={{ padding: "2px 4px" }}>
                  <input className="input" autoFocus value={title} maxLength={40} onChange={(e) => setTitle(e.target.value)} onBlur={() => setRenaming(null)} />
                </form>
              ) : (
                <div key={s.session_id} className={`history-item ${route === "chat" && s.session_id === sessionId ? "active" : ""}`}
                  role="button" tabIndex={0} onClick={() => onSelect(s.session_id)} onKeyDown={(e) => e.key === "Enter" && onSelect(s.session_id)}>
                  {s.pending > 0 && <span className="dot" title="有待确认的操作" />}
                  <span className="title">{s.title}</span>
                  <button className="more" aria-label="更多操作" onClick={(e) => {
                    e.stopPropagation();
                    const rect = e.currentTarget.getBoundingClientRect();
                    setMenu({ id: s.session_id, top: rect.bottom + 4, left: rect.left - 110, title: s.title });
                  }}><Icon name="more" style={{ width: 16, height: 16 }} /></button>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="nav">
          {NAV.map(([key, label, icon]) => (
            <button key={key} aria-current={route === key ? "page" : undefined} onClick={() => onNavigate(key)}>
              <Icon name={icon} />{label}
            </button>
          ))}
        </div>
        <div className="rail-foot">
          <span className={`status-dot ${status.warn ? "warn" : ""}`} />
          <span>{status.text}</span>
        </div>
      </nav>
      {menu && (
        <div className="menu" style={{ top: menu.top, left: Math.max(8, menu.left), position: "fixed" }} onClick={(e) => e.stopPropagation()}>
          <button onClick={() => { setTitle(menu.title); setRenaming(menu.id); setMenu(null); }}>重命名</button>
          <button className="danger" onClick={() => { remove(menu.id); setMenu(null); }}>删除</button>
        </div>
      )}
    </>
  );
}
