import { createContext, useCallback, useContext, useState } from "react";

const PATHS = {
  chat: "M4 5h16v11H8l-4 4z",
  ledger: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .3-1.3-1.3.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L6 7.3 4 7 3 10l2 1v2l-2 1 1 3 2-.3 1.3 1.3-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.3-1.3 2 .3 1-3-2-1z",
  plus: "M12 5v14M5 12h14",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6 6 18",
  more: "M6 12h.01M12 12h.01M18 12h.01",
  send: "M5 12h12M13 6l6 6-6 6",
  stop: "M7 7h10v10H7z",
  panel: "M4 5h16v14H4zM15 5v14",
  transfer: "M5 9h13l-3-3M19 15H6l3 3",
  card: "M3 6h18v12H3zM3 10h18",
  wealth: "M4 18l5-6 4 3 7-9",
  wish: "M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z",
  subscription: "M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4",
  payee: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20a8 8 0 0 1 16 0",
  income: "M12 4v12M6 10l6 6 6-6M5 20h14",
  automation: "M13 3 5 14h6l-1 7 8-11h-6z",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  phone: "M8 3h8v18H8zM11 18h2",
  lock: "M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3",
  check: "M5 12l5 5 9-10",
  ticket: "M4 7h16v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4zM14 7v10",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  edit: "M4 20h4L18 10l-4-4L4 16zM13 7l4 4",
};

export function Icon({ name, ...rest }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" {...rest}>
      <path d={PATHS[name] || PATHS.spark} />
    </svg>
  );
}

const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((text, kind = "info", ms = 3600) => {
    const id = Math.random();
    setItems((list) => [...list, { id, text, kind }]);
    setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), ms);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

export function Tag({ tone = "neutral", children }) {
  return <span className={`tag ${tone}`}>{children}</span>;
}

export function Drawer({ title, onClose, children }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <header><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="关闭"><Icon name="close" /></button></header>
        <div className="content">{children}</div>
      </aside>
    </>
  );
}

// Sections the connected bank does not provide through its interface. Shown plainly instead of an error.
const MISSING = { cards: "银行卡", portfolio: "理财持仓", wish_funds: "心愿金", subscriptions: "订阅查询", direct_debits: "代扣查询",
  detection: "周期扣费识别（需要交易流水）", finance: "存款、外汇、黄金、基金、贷款与信用账户", products: "理财产品货架", holdings: "理财持仓", purchase: "在线申购" };
export function BankGaps({ items }) {
  if (!items || !items.length) return null;
  return <div className="panel muted bank-gaps" style={{ fontSize: 13.5 }}>你接入的银行没有通过接口提供：{items.map((k) => MISSING[k] || k).join("、")}。这些区块会在银行开放对应接口后自动出现。</div>;
}
