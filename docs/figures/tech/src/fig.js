// Shared helpers: line icons (stroke, 24x24) and arrows drawn between element edges after layout.
const P = {
  web: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9.5 16h5"/>',
  term: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
  brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3V4zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3V4z"/>',
  check: '<path d="M4 6h10M4 12h7M4 18h7"/><path d="M14 16l2.5 2.5L21 14"/>',
  cards: '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="M19 7v12a2 2 0 0 1-2 2H8"/><path d="M6 10h7M6 14h5"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  flow: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8 7.5l3 8M16 7.5l-3 8"/>',
  transfer: '<path d="M4 8h14l-3-3M20 16H6l3 3"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  coins: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
  headset: '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="2.5" y="14" width="4" height="6" rx="1.5"/><rect x="17.5" y="14" width="4" height="6" rx="1.5"/>',
  light: '<rect x="7" y="2.5" width="10" height="19" rx="4"/><circle cx="12" cy="7.5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="16.5" r="1.6"/>',
  finger: '<path d="M7 10a5 5 0 0 1 10 0v3M12 10v5a6 6 0 0 1-1.5 4M9.5 11v2.5a9 9 0 0 1-1.6 5M14.5 12.5v1a10 10 0 0 1-1 4.5M4.5 13V10a7.5 7.5 0 0 1 13.8-4"/>',
  contract: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  gate: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5M8 11l2 2 4-4"/>',
  receipt: '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
  bank: '<path d="M3 9l9-5 9 5M4 10h16M6 10v8M10 10v8M14 10v8M18 10v8M3 20h18"/>',
  cube: '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
  plug: '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
  chain: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  sign: '<path d="M4 20l4-1 11-11-3-3L5 16z"/><path d="M14 6l3 3"/>',
  gift: '<rect x="3" y="8" width="18" height="5" rx="1"/><path d="M5 13v8h14v-8M12 8v13M12 8c-2-3-6-3-6 0h6zM12 8c2-3 6-3 6 0h-6z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  x: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  bell: '<path d="M6 9a6 6 0 0 1 12 0c0 6 2 7 2 7H4s2-1 2-7"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  wallet: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M16 14h2"/>',
};
function icon(name, color) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[name]}</svg>`;
}
document.querySelectorAll("[data-icon]").forEach((el) => {
  const color = el.dataset.color || "#0b1a2c";
  el.innerHTML = icon(el.dataset.icon, color);
  if (el.dataset.bg) el.style.background = el.dataset.bg;
});

// wire(from, to, {from:'r'|'l'|'t'|'b', to:..., color, dash, label, bend, head})
const fig = document.getElementById("fig");
const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
svg.setAttribute("class", "wires");
fig.appendChild(svg);
const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
svg.appendChild(defs);
const heads = {};
function head(color) {
  if (heads[color]) return heads[color];
  const id = "h" + Object.keys(heads).length;
  const m = document.createElementNS("http://www.w3.org/2000/svg", "marker");
  m.setAttribute("id", id); m.setAttribute("viewBox", "0 0 10 10"); m.setAttribute("refX", "8.5"); m.setAttribute("refY", "5");
  m.setAttribute("markerWidth", "7"); m.setAttribute("markerHeight", "7"); m.setAttribute("orient", "auto-start-reverse");
  m.innerHTML = `<path d="M0 0L10 5L0 10z" fill="${color}"/>`;
  defs.appendChild(m);
  return (heads[color] = id);
}
function pt(el, side, frac = 0.5) {
  const r = el.getBoundingClientRect(), f = fig.getBoundingClientRect();
  const x = r.left - f.left, y = r.top - f.top;
  return { r: [x + r.width, y + r.height * frac], l: [x, y + r.height * frac], t: [x + r.width * frac, y], b: [x + r.width * frac, y + r.height] }[side];
}
function wire(a, b, o = {}) {
  const A = document.getElementById(a), B = document.getElementById(b);
  const [x1, y1] = pt(A, o.from || "r", o.ff ?? 0.5), [x2, y2] = pt(B, o.to || "l", o.tf ?? 0.5);
  const color = o.color || "#5b6573";
  let d;
  if (o.straight) d = `M${x1} ${y1}L${x2} ${y2}`;
  else if (o.elbow === "v") { const my = o.at ?? (y1 + y2) / 2; d = `M${x1} ${y1}V${my}H${x2}V${y2}`; }
  else if (o.elbow === "h") { const mx = o.at ?? (x1 + x2) / 2; d = `M${x1} ${y1}H${mx}V${y2}H${x2}`; }
  else {
    const k = o.bend ?? 0.5, vert = (o.from === "b" || o.from === "t");
    d = vert ? `M${x1} ${y1}C${x1} ${y1 + (y2 - y1) * k},${x2} ${y2 - (y2 - y1) * k},${x2} ${y2}`
             : `M${x1} ${y1}C${x1 + (x2 - x1) * k} ${y1},${x2 - (x2 - x1) * k} ${y2},${x2} ${y2}`;
  }
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", d); p.setAttribute("fill", "none"); p.setAttribute("stroke", color);
  p.setAttribute("stroke-width", o.width || 2); p.setAttribute("stroke-linecap", "round"); p.setAttribute("stroke-linejoin", "round");
  if (o.dash) p.setAttribute("stroke-dasharray", o.dash === true ? "5 5" : o.dash);
  if (o.head !== false) p.setAttribute("marker-end", `url(#${head(color)})`);
  svg.appendChild(p);
  if (o.label) {
    const len = p.getTotalLength(), m = p.getPointAtLength(len * (o.lpos ?? 0.5));
    const l = document.createElement("div");
    l.className = "wlabel"; l.innerHTML = o.label;
    l.style.left = m.x + (o.dx || 0) + "px"; l.style.top = m.y + (o.dy || 0) + "px";
    if (o.lcolor) { l.style.color = o.lcolor; l.style.borderColor = o.lcolor; }
    fig.appendChild(l);
  }
  return p;
}
window.icon = icon; window.wire = wire;
