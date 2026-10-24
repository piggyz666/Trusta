import { useEffect, useRef, useState } from "react";
import { createWater } from "./water/water.js";

// Interactive water background (works in light and dark themes).
//  • moving the pointer / a finger draws a wake through the water;
//  • pressing drops a stone: rings spread out and refract the pool floor;
//  • each keystroke in the composer sends a small ripple from the input, sending sends a big ring;
// The water is still otherwise, and the render loop stops completely once it has calmed down.
// Other components can disturb it: window.dispatchEvent(new CustomEvent("trusta-ink", { detail: { kind, rect } })).

function motionAllowed() {
  let off = false;
  try { off = localStorage.getItem("trusta-motion") === "off"; } catch { /* ignore */ }
  return !off && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
export const isLightTheme = () => {
  const forced = document.documentElement.dataset.theme;
  return forced ? forced === "light" : false;
};

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
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const mobile = matchMedia("(pointer: coarse)").matches || Math.min(innerWidth, innerHeight) < 600;
    let water;
    try { water = createWater(canvas, { mobile }); } catch { water = null; }
    if (!water) { setFailed(true); return undefined; }
    water.setTheme(isLightTheme());

    let raf = 0, lastInput = performance.now(), running = false, lastDrop = 0;
    const queue = [];
    const pointer = { x: 0, y: 0, has: false };

    function frame(now) {
      if (water.resize()) water.setTheme(isLightTheme());
      while (queue.length) queue.shift()();
      water.step(2);
      water.render(now / 1000);
      if (now - lastInput > 7000 || document.hidden) { running = false; return; } // calm again: stop until the next touch
      raf = requestAnimationFrame(frame);
    }
    function wake() {
      lastInput = performance.now();
      if (!moving) { queue.length = 0; water.render(0); return; }
      if (!running && !document.hidden) { running = true; raf = requestAnimationFrame(frame); }
    }
    const toUv = (cx, cy) => [cx / canvas.clientWidth, 1 - cy / canvas.clientHeight];

    function wakeTrail(cx, cy) {
      const [x, y] = toUv(cx, cy);
      if (!pointer.has) { pointer.x = x; pointer.y = y; pointer.has = true; return; }
      const dx = x - pointer.x, dy = y - pointer.y, dist = Math.hypot(dx, dy);
      const now = performance.now();
      if (dist < 0.004 || now - lastDrop < 16) return;
      lastDrop = now;
      const steps = Math.min(5, Math.ceil(dist / 0.015));
      const strength = Math.min(0.04, 0.01 + dist * 0.8);
      for (let i = 1; i <= steps; i++) {
        const px = pointer.x + dx * (i / steps), py = pointer.y + dy * (i / steps);
        queue.push(() => water.drop(px, py, 0.014, -strength / steps * 2));
      }
      pointer.x = x; pointer.y = y;
      wake();
    }
    function stone(cx, cy, radius, strength) {
      const [x, y] = toUv(cx, cy);
      queue.push(() => water.drop(x, y, radius, strength));
      wake();
    }

    const onMove = (e) => wakeTrail(e.clientX, e.clientY);
    const onLeave = () => { pointer.has = false; };
    const onDown = (e) => { if (e.button === 0 || e.pointerType !== "mouse") stone(e.clientX, e.clientY, 0.022, -0.12); };
    const onEvent = (e) => {
      const { kind, rect } = e.detail || {};
      if (!rect) return;
      if (kind === "type") stone(rect.left + 30 + Math.random() * Math.max(10, rect.width - 60), rect.top - 6, 0.01, -0.05);
      else if (kind === "send") { stone(rect.left + rect.width / 2, rect.top + rect.height / 2, 0.04, -0.22); }
      else if (kind === "bloom") stone(rect.left + rect.width / 2, rect.top + rect.height / 2, 0.03, -0.15);
    };
    const onResize = () => { water.resize(); water.setTheme(isLightTheme()); water.render(0); };
    const onVisible = () => { if (!document.hidden && running === false) water.render(0); };
    const themeObserver = new MutationObserver(() => { water.setTheme(isLightTheme()); water.render(0); wake(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("blur", onLeave);
    window.addEventListener("trusta-ink", onEvent);
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisible);

    water.render(0);
    // opening: one drop falls into the middle of the pool, behind the mark
    const intro = setTimeout(() => {
      const mark = document.querySelector(".hero-mark");
      const r = mark ? mark.getBoundingClientRect() : { left: innerWidth / 2, top: innerHeight / 2, width: 0, height: 0 };
      stone(r.left + r.width / 2, r.top + r.height / 2, 0.03, -0.18);
    }, 350);

    return () => {
      clearTimeout(intro);
      cancelAnimationFrame(raf);
      themeObserver.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
      window.removeEventListener("trusta-ink", onEvent);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisible);
      water.destroy();
    };
  }, [moving]);

  return (
    <div className={`backdrop ${dim ? "dim" : ""} ${failed ? "still" : ""}`} aria-hidden="true">
      {!failed && <canvas ref={canvasRef} key={moving ? "m" : "s"} />}
    </div>
  );
}

// Helper for components: disturb the water from an element (kind: "type" | "send" | "bloom").
export function stirInk(kind, element) {
  if (!element) return;
  window.dispatchEvent(new CustomEvent("trusta-ink", { detail: { kind, rect: element.getBoundingClientRect() } }));
}
