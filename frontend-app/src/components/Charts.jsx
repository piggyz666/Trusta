import { yuan } from "../format.js";

// Cool, ordered palette for categories (never red / amber / green: those mean permission tiers).
export const SERIES = ["#9cc9ff", "#7c83ff", "#b08cff", "#5fa8ff", "#c7b8ff", "#6b7bb8"];

export function Donut({ items, selected, onSelect }) {
  const total = items.reduce((s, i) => s + Math.max(0, i.value), 0) || 1;
  let offset = 0;
  const r = 52, c = 2 * Math.PI * r;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 132 132" width="132" height="132" role="img" aria-label="消费分类占比">
        <circle cx="66" cy="66" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="16" />
        {items.map((item, index) => {
          const share = Math.max(0, item.value) / total;
          const dash = `${share * c} ${c}`;
          const el = (
            <circle key={item.key} cx="66" cy="66" r={r} fill="none" stroke={SERIES[index % SERIES.length]}
              strokeWidth={selected === item.key ? 20 : 16} strokeDasharray={dash} strokeDashoffset={-offset * c}
              transform="rotate(-90 66 66)" style={{ cursor: "pointer", transition: "stroke-width .15s" }}
              onClick={() => onSelect?.(item.key)} />
          );
          offset += share;
          return el;
        })}
      </svg>
      <div className="legend">
        {items.map((item, index) => (
          <button key={item.key} onClick={() => onSelect?.(item.key)} aria-pressed={selected === item.key}>
            <i style={{ background: SERIES[index % SERIES.length] }} />
            <span>{item.label}</span>
            <span className="num muted">{yuan(item.value)} · {Math.round((Math.max(0, item.value) / total) * 100)}%</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function Bars({ items, selected, onSelect }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="chart-bars">
      {items.map((item) => (
        <button key={item.key} className="bar-row" onClick={() => onSelect?.(item.key)} aria-expanded={selected === item.key}>
          <span>{item.label}</span>
          <span className="track"><span className="fill" style={{ width: `${(Math.max(0, item.value) / max) * 100}%` }} /></span>
          <span className="num muted">{yuan(item.value)}</span>
        </button>
      ))}
    </div>
  );
}

export function Columns({ items }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="columns" role="img" aria-label="按月消费">
      {items.map((item) => (
        <div className="col" key={item.key}>
          <span className="num">{yuan(item.value)}</span>
          <i style={{ height: `${Math.max(2, (item.value / max) * 90)}px` }} />
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  );
}
