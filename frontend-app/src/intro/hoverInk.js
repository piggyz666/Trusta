// Hover Ink: a black balloon wordmark that stays put; where the pointer passes, the letters
// smoothly take on a balloon material, hold for a moment, then ease back to black.
// Every material image is pre-aligned to the black base (same crop, same letter positions),
// so a reveal is just a soft per-material mask over the word — no tearing, no seams.

const HOLD = 0.9;        // seconds a painted area stays fully revealed after the last touch
const FADE = 1.7;        // fade-back speed (1/s, exponential)
const MASK_SCALE = 0.3;  // masks are low-res; bilinear upscaling is what makes the edge soft
const NEW_STROKE = 0.5;  // pointer idle this long → the next touch starts a new material

function load(src) {
  return new Promise((ok, bad) => { const im = new Image(); im.decoding = "async"; im.onload = () => ok(im); im.onerror = bad; im.src = src; });
}

export async function createHoverInk(canvas, { ink, materials }) {
  const [base, ...mats] = await Promise.all([ink, ...materials].map(load));
  const ctx = canvas.getContext("2d");
  const aspect = base.width / base.height;
  const layer = document.createElement("canvas");
  const lctx = layer.getContext("2d");
  const masks = mats.map(() => { const c = document.createElement("canvas"); return { c, x: c.getContext("2d"), last: -1e9, born: -1e9, live: false }; });

  let dpr = 1, rect = { x: 0, y: 0, w: 1, h: 1 }, cssW = 0, cssH = 0;
  function layout() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(r.width * dpr), H = Math.round(r.height * dpr);
    if (W === canvas.width && H === canvas.height && r.width === cssW) return false;
    cssW = r.width; cssH = r.height;
    canvas.width = W; canvas.height = H;
    // the word fills most of the width, but never gets taller than ~42% of the viewport
    const w = Math.min(W * (W < 720 * dpr ? 0.96 : 0.9), H * 0.42 * aspect);
    const h = w / aspect;
    rect = { x: (W - w) / 2, y: (H - h) / 2 + H * 0.02, w, h };
    layer.width = Math.ceil(w); layer.height = Math.ceil(h);
    for (const m of masks) {
      const old = m.live ? m.c : null; let copy = null;
      if (old) { copy = document.createElement("canvas"); copy.width = old.width; copy.height = old.height; copy.getContext("2d").drawImage(old, 0, 0); }
      m.c.width = Math.max(8, Math.round(w * MASK_SCALE)); m.c.height = Math.max(4, Math.round(h * MASK_SCALE));
      if (copy) m.x.drawImage(copy, 0, 0, m.c.width, m.c.height);
    }
    return true;
  }
  layout();

  // pointer with inertia: the brush glides after the real pointer, which is what makes it feel fluid
  let target = null, pos = null, current = -1, lastTouch = -1e9;
  const radiusPx = () => rect.h * 0.34;   // in canvas px

  function point(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    return [(clientX - r.left) * dpr, (clientY - r.top) * dpr];
  }
  function touch(clientX, clientY, now) {
    if (now - lastTouch > NEW_STROKE || current < 0) { current = (current + 1) % masks.length; pos = null; masks[current].born = now; }
    lastTouch = now;
    target = point(clientX, clientY);
    if (!pos) pos = target.slice();
  }
  function release() { target = null; }

  function stamp(m, x, y, r, strength) {
    // map canvas px → mask px
    const s = m.c.width / rect.w;
    const mx = (x - rect.x) * s, my = (y - rect.y) * s, mr = r * s;
    if (mx < -mr || my < -mr || mx > m.c.width + mr || my > m.c.height + mr) return;
    const g = m.x.createRadialGradient(mx, my, 0, mx, my, mr);
    g.addColorStop(0, `rgba(255,255,255,${strength})`);
    g.addColorStop(0.45, `rgba(255,255,255,${strength * 0.8})`);
    g.addColorStop(1, "rgba(255,255,255,0)");
    m.x.globalCompositeOperation = "source-over";
    m.x.fillStyle = g; m.x.fillRect(mx - mr, my - mr, mr * 2, mr * 2);
  }

  function step(dt, now) {
    if (target && pos && current >= 0) {
      const k = 1 - Math.exp(-dt * 16);
      const nx = pos[0] + (target[0] - pos[0]) * k, ny = pos[1] + (target[1] - pos[1]) * k;
      const r = radiusPx(), m = masks[current];
      const d = Math.hypot(nx - pos[0], ny - pos[1]), n = Math.max(1, Math.ceil(d / (r * 0.18)));
      for (let i = 1; i <= n; i++) stamp(m, pos[0] + (nx - pos[0]) * i / n, pos[1] + (ny - pos[1]) * i / n, r, 0.22);
      pos = [nx, ny];
      m.last = now; m.live = true;
      // a resting pointer settles into place rather than painting forever
      if (Math.hypot(target[0] - nx, target[1] - ny) < 0.5) target = null;
    }
    for (const m of masks) {
      if (!m.live) continue;
      const idle = now - m.last;
      if (idle > HOLD) {
        m.x.globalCompositeOperation = "destination-out";
        m.x.fillStyle = `rgba(0,0,0,${1 - Math.exp(-dt * FADE)})`;
        m.x.fillRect(0, 0, m.c.width, m.c.height);
      }
      if (idle > HOLD + 4.2 / FADE) { m.x.clearRect(0, 0, m.c.width, m.c.height); m.live = false; }   // 8-bit fades never quite reach 0
    }
  }

  function draw() {
    const { x, y, w, h } = rect;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(base, x, y, w, h);
    // oldest stroke first, so the newest material sits on top where strokes cross
    const live = masks.map((m, i) => [m, i]).filter(([m]) => m.live).sort((a, b) => a[0].born - b[0].born);
    for (const [m, i] of live) {
      lctx.globalCompositeOperation = "source-over";
      lctx.clearRect(0, 0, layer.width, layer.height);
      lctx.imageSmoothingQuality = "high";
      lctx.drawImage(mats[i], 0, 0, layer.width, layer.height);
      lctx.globalCompositeOperation = "destination-in";
      lctx.drawImage(m.c, 0, 0, layer.width, layer.height);
      ctx.drawImage(layer, x, y);
    }
  }

  const busy = () => !!target || masks.some((m) => m.live);
  return {
    layout, step, draw, touch, release, busy,
    get rect() { return rect; }, get dpr() { return dpr; },
    materials: mats.length,
  };
}
