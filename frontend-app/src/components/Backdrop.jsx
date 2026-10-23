import { useEffect, useRef, useState } from "react";
import { createInk } from "./ink/fluid.js";

// Interactive ink-in-water background. The ink only moves when you do:
//  • moving the pointer (or a finger) draws a stroke of ink that swirls behind your hand;
//  • pressing drops a bloom of ink;
//  • typing in the composer lets small wisps rise from the input, sending releases a plume toward the reply.
// When nothing happens the ink settles and fades into the dark, and the simulation stops to save power.
// Other components can stir it with: window.dispatchEvent(new CustomEvent("trusta-ink", { detail: { kind, rect } })).

function motionAllowed() {
  let off = false;
  try { off = localStorage.getItem("trusta-motion") === "off"; } catch { /* ignore */ }
  return !off && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
const isLight = () => document.documentElement.dataset.theme === "light";

export default function Backdrop({ dim = false }) {
  const canvasRef = useRef(null);
  const [moving, setMoving] = useState(motionAllowed);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const update = () => setMoving(motionAllowed());
    window.addEventListener("trusta-motion", update);
    return () => window.removeEventListener("trusta-motion", update);
  }, []);

  useEffect(() => {
    if (!moving) return undefined;
    const canvas = canvasRef.current;
    const mobile = matchMedia("(pointer: coarse)").matches || Math.min(innerWidth, innerHeight) < 600;
    let ink;
    try { ink = createInk(canvas, mobile ? { simRes: 96, dyeRes: 512, splatForce: 4200 } : {}); } catch { ink = null; }
    if (!ink) { setFailed(true); return undefined; }
    ink.setTheme(isLight());

    let raf = 0, last = performance.now(), lastInput = performance.now(), running = false;
    const queue = [];
    const pointer = { x: 0, y: 0, has: false };

    function frame(now) {
      const dt = Math.min((now - last) / 1000, 1 / 60);
      last = now;
      ink.resize();
      while (queue.length) queue.shift()();
      ink.step(dt);
      ink.render(now / 1000);
      if (now - lastInput > 14000 || document.hidden) { running = false; return; } // settled: stop until the next touch
      raf = requestAnimationFrame(frame);
    }
    function wake() {
      lastInput = performance.now();
      if (!running && !document.hidden) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    }
    const toUv = (cx, cy) => [cx / canvas.clientWidth, 1 - cy / canvas.clientHeight];

    const force = mobile ? 4200 : 5200;
    function stroke(cx, cy) {
      const [x, y] = toUv(cx, cy);
      if (!pointer.has) { pointer.x = x; pointer.y = y; pointer.has = true; return; }
      const px0 = pointer.x, py0 = pointer.y, ux = x - px0, uy = y - py0;
      pointer.x = x; pointer.y = y;
      const aspect = canvas.clientWidth / canvas.clientHeight;
      const dx = aspect < 1 ? ux * aspect : ux, dy = aspect < 1 ? uy : uy / aspect;
      const speed = Math.hypot(dx, dy);
      if (speed < 0.0004) return;
      const strength = Math.min(1.6, 0.35 + speed * 40);
      const steps = Math.min(6, Math.ceil(speed / 0.02)); // a fast flick still leaves one continuous ribbon of ink
      for (let i = 1; i <= steps; i++) {
        const t = i / steps, color = ink.nextColor((strength / steps) * 1.4);
        queue.push(() => ink.splat(px0 + ux * t, py0 + uy * t, dx * force, dy * force, color, 1));
      }
      wake();
    }

    function bloom(cx, cy, power = 1) {
      const [x, y] = toUv(cx, cy);
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * Math.PI * 2 + Math.random() * 0.5;
        const color = ink.nextColor(0.6 * power);
        queue.push(() => ink.splat(x, y, Math.cos(a) * 220 * power, Math.sin(a) * 220 * power, color, 1.2 * power));
      }
      wake();
    }

    const onMove = (e) => stroke(e.clientX, e.clientY);
    const onLeave = () => { pointer.has = false; };
    const onDown = (e) => { if (e.button === 0 || e.pointerType !== "mouse") bloom(e.clientX, e.clientY, 0.7); };
    const onInk = (e) => {
      const { kind, rect } = e.detail || {};
      if (!rect) return;
      if (kind === "type") {
        // a wisp rises from a random point on the input's top edge
        const cx = rect.left + 24 + Math.random() * Math.max(10, rect.width - 48), cy = rect.top + 4;
        const [x, y] = toUv(cx, cy);
        const color = ink.nextColor(0.55);
        queue.push(() => ink.splat(x, y, (Math.random() - 0.5) * 120, 420 + Math.random() * 240, color, 0.7));
        wake();
      } else if (kind === "send") {
        const cx = rect.left + rect.width / 2, cy = rect.top;
        const [x, y] = toUv(cx, cy);
        for (let i = -3; i <= 3; i++) {
          const color = ink.nextColor(1.1);
          queue.push(() => ink.splat(x + i * 0.025, y, i * 90, 1500 + Math.random() * 400, color, 1.4));
        }
        wake();
      } else if (kind === "bloom") {
        bloom(rect.left + rect.width / 2, rect.top + rect.height / 2, 1);
      }
    };
    const onVisible = () => { if (!document.hidden) wake(); };
    const themeObserver = new MutationObserver(() => { ink.setTheme(isLight()); wake(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("blur", onLeave);
    window.addEventListener("trusta-ink", onInk);
    document.addEventListener("visibilitychange", onVisible);

    // opening: one drop of ink blooms behind the headline, then everything waits for you
    const intro = setTimeout(() => {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      bloom(w * 0.7, h * 0.78, 0.9);
      setTimeout(() => bloom(w * 0.3, h * 0.85, 0.6), 450);
    }, 250);
    wake();

    return () => {
      clearTimeout(intro);
      cancelAnimationFrame(raf);
      themeObserver.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
      window.removeEventListener("trusta-ink", onInk);
      document.removeEventListener("visibilitychange", onVisible);
      ink.destroy();
    };
  }, [moving]);

  return (
    <div className={`backdrop ${dim ? "dim" : ""} ${!moving || failed ? "still" : ""}`} aria-hidden="true">
      {moving && !failed && <canvas ref={canvasRef} />}
    </div>
  );
}

// Helper for components: stir the ink from an element (kind: "type" | "send" | "bloom").
export function stirInk(kind, element) {
  if (!element) return;
  window.dispatchEvent(new CustomEvent("trusta-ink", { detail: { kind, rect: element.getBoundingClientRect() } }));
}
