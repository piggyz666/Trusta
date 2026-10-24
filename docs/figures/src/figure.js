// Shared helpers for the TRUSTA figures: language switch and connector arrows drawn between laid-out elements.
const LANG = new URLSearchParams(location.search).get("lang") === "en" ? "en" : "zh";
document.documentElement.lang = LANG === "en" ? "en" : "zh-CN";
const T = (zh, en) => (LANG === "en" ? en : zh);

// Fill every [data-t] element from a dictionary { key: [zh, en] }.
function translate(dict) {
  document.querySelectorAll("[data-t]").forEach((el) => {
    const v = dict[el.dataset.t];
    if (!v) { console.warn("missing", el.dataset.t); return; }
    el.innerHTML = LANG === "en" ? v[1] : v[0];
  });
}

const NS = "http://www.w3.org/2000/svg";
let wires;
function initWires() {
  const fig = document.getElementById("fig");
  wires = document.createElementNS(NS, "svg");
  wires.id = "wires";
  wires.setAttribute("width", fig.offsetWidth);
  wires.setAttribute("height", fig.offsetHeight);
  const defs = document.createElementNS(NS, "defs");
  for (const [id, c] of Object.entries({ ink: "#16181d", line: "#8b929c", teal: "#1f7f75", amber: "#c98a12", red: "#c4422c", purple: "#5b4aa0", blue: "#2f64a3", slate: "#3c4a5c" })) {
    const m = document.createElementNS(NS, "marker");
    for (const [k, v] of Object.entries({ id: "m-" + id, viewBox: "0 0 10 10", refX: 8.5, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" })) m.setAttribute(k, v);
    const p = document.createElementNS(NS, "path"); p.setAttribute("d", "M0,1 L9,5 L0,9 z"); p.setAttribute("fill", c); m.append(p); defs.append(m);
  }
  wires.append(defs);
  fig.append(wires);
}
function rect(sel) {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) throw new Error("no element " + sel);
  const f = document.getElementById("fig").getBoundingClientRect(), r = el.getBoundingClientRect();
  return { x: r.left - f.left, y: r.top - f.top, w: r.width, h: r.height, cx: r.left - f.left + r.width / 2, cy: r.top - f.top + r.height / 2,
    r: r.right - f.left, b: r.bottom - f.top };
}
// side: "r" | "l" | "t" | "b"; offset along the side (0..1)
function port(sel, side, at = 0.5) {
  const q = rect(sel);
  if (side === "r") return [q.r, q.y + q.h * at];
  if (side === "l") return [q.x, q.y + q.h * at];
  if (side === "t") return [q.x + q.w * at, q.y];
  return [q.x + q.w * at, q.b];
}
function path(points, o = {}) {
  const p = document.createElementNS(NS, "path");
  let d = "";
  if (o.curve && points.length === 2) {
    const [[x1, y1], [x2, y2]] = points; const mx = (x1 + x2) / 2;
    d = o.curve === "v" ? `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}` : `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  } else d = points.map((q, i) => (i ? "L" : "M") + q[0] + "," + q[1]).join(" ");
  const color = o.color || "line";
  const hex = { ink: "#16181d", line: "#8b929c", teal: "#1f7f75", amber: "#c98a12", red: "#c4422c", purple: "#5b4aa0", blue: "#2f64a3", slate: "#3c4a5c" }[color];
  for (const [k, v] of Object.entries({ d, fill: "none", stroke: hex, "stroke-width": o.w || 1.5, "stroke-linejoin": "round" })) p.setAttribute(k, v);
  if (!o.noHead) p.setAttribute("marker-end", `url(#m-${color})`);
  if (o.both) p.setAttribute("marker-start", `url(#m-${color})`);
  if (o.dash) p.setAttribute("stroke-dasharray", o.dash);
  wires.append(p);
  if (o.label) {
    const [a, b] = o.seg != null ? [points[o.seg], points[o.seg + 1]] : [points[0], points[points.length - 1]];
    const t = document.createElementNS(NS, "text");
    for (const [k, v] of Object.entries({ x: (a[0] + b[0]) / 2 + (o.dx || 0), y: (a[1] + b[1]) / 2 + (o.dy ?? -7), "text-anchor": "middle",
      "font-size": o.ls || 11, "font-weight": o.lw || 600, fill: hex, "font-family": o.mono ? "DejaVu Sans Mono, monospace" : "InterFig, Noto Sans CJK SC, sans-serif" })) t.setAttribute(k, v);
    t.textContent = o.label;
    wires.append(t);
    if (o.halo) {
      const bb = t.getBBox(); const bg = document.createElementNS(NS, "rect");
      for (const [k, v] of Object.entries({ x: bb.x - 4, y: bb.y - 1, width: bb.width + 8, height: bb.height + 2, rx: 3, fill: "#fff" })) bg.setAttribute(k, v);
      wires.insertBefore(bg, t);
    }
  }
  return p;
}
function done() { document.body.dataset.ready = "1"; }
