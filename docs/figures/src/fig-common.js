// Minimal SVG drawing helpers for the README figures (rendered to PNG with scripts/render_figures.py).
const NS = "http://www.w3.org/2000/svg";
const C = {
  ink: "#1f2328", sub: "#57606a", line: "#8c959f", faint: "#d0d7de",
  blue: "#3B6EA5", blueF: "#EAF1FA", teal: "#23867C", tealF: "#E5F3F1", orange: "#C8711F", orangeF: "#FCEFE2",
  purple: "#6A55A3", purpleF: "#EFEBF8", slate: "#3A4A5C", slateF: "#EEF1F4",
  green: "#2E9E5B", amber: "#D79A00", red: "#C8442E",
};
let svg;
function el(name, attrs = {}, parent = svg) {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent.appendChild(e);
  return e;
}
function start(width, height) {
  svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` }, document.getElementById("fig"));
  const defs = el("defs");
  for (const [id, color] of [["arr", C.line], ["arrInk", C.ink], ["arrTeal", C.teal], ["arrOrange", C.orange], ["arrPurple", C.purple], ["arrBlue", C.blue], ["arrSlate", C.slate]]) {
    const m = el("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0,1 L9,5 L0,9 z", fill: color }, m);
  }
  el("rect", { x: 0, y: 0, width, height, fill: "#fff" });
  return svg;
}
function text(x, y, s, o = {}) {
  const t = el("text", { x, y, fill: o.color || C.ink, "font-size": o.size || 13, "font-weight": o.weight || 400,
    "text-anchor": o.anchor || "start", "font-family": o.mono ? "'DejaVu Sans Mono', monospace" : "Inter, 'Noto Sans CJK SC', sans-serif",
    "dominant-baseline": "middle", ...(o.italic ? { "font-style": "italic" } : {}) }, o.parent || svg);
  t.textContent = s;
  return t;
}
function lines(x, y, arr, o = {}) { arr.forEach((s, i) => text(x, y + i * (o.lh || 17), s, o)); }
function box(x, y, w, h, o = {}) {
  el("rect", { x, y, width: w, height: h, rx: o.r ?? 6, fill: o.fill || "#fff", stroke: o.stroke || C.line, "stroke-width": o.sw || 1,
    ...(o.dash ? { "stroke-dasharray": o.dash } : {}) });
  const cx = x + w / 2;
  if (o.title) text(o.align === "left" ? x + 10 : cx, y + (o.sub ? h / 2 - 9 : h / 2), o.title, { anchor: o.align === "left" ? "start" : "middle", weight: 600, size: o.ts || 13.5, color: o.tc || C.ink });
  if (o.sub) (Array.isArray(o.sub) ? o.sub : [o.sub]).forEach((s, i) =>
    text(o.align === "left" ? x + 10 : cx, y + h / 2 + 9 + i * 15, s, { anchor: o.align === "left" ? "start" : "middle", size: o.ss || 11.5, color: C.sub }));
}
function arrow(pts, o = {}) {
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0] + "," + p[1]).join(" ");
  el("path", { d, fill: "none", stroke: o.color || C.line, "stroke-width": o.sw || 1.3, "marker-end": `url(#${o.marker || "arr"})`,
    ...(o.dash ? { "stroke-dasharray": o.dash } : {}), ...(o.both ? { "marker-start": `url(#${o.marker || "arr"})` } : {}) });
  if (o.label) {
    const [a, b] = o.at != null ? [pts[o.at], pts[o.at + 1]] : [pts[0], pts[1]];
    const lx = (a[0] + b[0]) / 2 + (o.dx || 0), ly = (a[1] + b[1]) / 2 + (o.dy ?? -9);
    const t = text(lx, ly, o.label, { anchor: "middle", size: o.ls || 10.5, color: o.lc || C.sub, mono: o.mono, weight: o.lw || 400 });
    if (o.halo) { const bb = t.getBBox(); const r = el("rect", { x: bb.x - 3, y: bb.y - 1, width: bb.width + 6, height: bb.height + 2, fill: "#fff" }); svg.insertBefore(r, t); }
  }
}
function panel(x, y, letter, title) {
  text(x, y, letter, { size: 20, weight: 700 });
  if (title) text(x + 22, y + 1, title, { size: 15, weight: 600 });
}
function chip(x, y, s, color, o = {}) {
  const w = o.w || (s.length * (o.cw || 7.2) + 14);
  el("rect", { x, y: y - 9, width: w, height: 18, rx: 9, fill: color, opacity: o.op ?? 1 });
  text(x + w / 2, y, s, { anchor: "middle", size: 10.5, weight: 600, color: o.tc || "#fff" });
  return w;
}
function sig(x, y, color = C.teal) {  // small "signed" badge
  el("rect", { x: x - 15, y: y - 8, width: 30, height: 16, rx: 3, fill: "#fff", stroke: color, "stroke-width": 1 });
  text(x, y + 0.5, "sig", { anchor: "middle", size: 9.5, weight: 700, color, mono: true });
}
