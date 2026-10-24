import { useEffect, useRef, useState } from "react";
import { createInkFluid } from "./inkFluid.js";

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const HERO = "/hero/";
const MATERIALS = ["gold", "teal", "pearl", "silver", "candy"].map((n) => `${HERO}${n}.webp`);

export default function Hero({ github }) {
  const ref = useRef(null);
  const [state, setState] = useState("loading");   // loading | ready | failed

  useEffect(() => {
    const canvas = ref.current;
    let ink = null, raf = 0, running = false, last = 0, alive = true;
    const now = () => performance.now() / 1000;

    function frame(ms) {
      const t = ms / 1000, dt = Math.min(1 / 30, Math.max(1 / 240, t - last)); last = t;
      ink.step(dt); ink.draw();
      // keep simulating until the ink has dissolved and the type has settled back
      if (ink.idleFor(t) < 7) raf = requestAnimationFrame(frame); else running = false;
    }
    const debug = location.hash === "#debug-ink";   // tests drive the simulation by hand
    function wake() { if (debug) return; if (!running && ink) { running = true; last = now(); raf = requestAnimationFrame(frame); } }

    // opening gesture: one unhurried pass through the word, so first-time visitors see what it does
    function intro() {
      if (!alive || !ink) return;
      const r = canvas.getBoundingClientRect(), B = ink.wordBox, d = B.dpr, t0 = performance.now();
      const tick = () => {
        if (!alive) return;
        const u = Math.min(1, (performance.now() - t0) / 1300);
        const e = u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2;
        const px = B.x + B.w * (0.06 + 0.55 * e), py = B.y + B.h * (0.55 - Math.sin(e * Math.PI * 1.5) * 0.35);
        ink.input(r.left + px / d, r.top + py / d, now()); wake();
        if (u < 1) setTimeout(tick, 16); else ink.leave();
      };
      tick();
    }

    createInkFluid(canvas, { materials: MATERIALS }).then((h) => {
      if (!alive) return;
      ink = h; ink.draw(); setState("ready");
      if (debug) { window.__ink = ink; return; }
      if (!reduced()) setTimeout(intro, 700);
    }).catch((e) => { console.warn(e); if (alive) setState("failed"); });

    const onMove = (e) => { if (ink) { ink.input(e.clientX, e.clientY, now()); wake(); } };
    const onLeave = () => ink?.leave();
    const onResize = () => { if (ink && ink.layout()) { ink.draw(); } };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    const ro = new ResizeObserver(onResize); ro.observe(canvas);
    return () => {
      alive = false; cancelAnimationFrame(raf); ro.disconnect();
      canvas.removeEventListener("pointermove", onMove); canvas.removeEventListener("pointerdown", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return (
    <section className="hero" aria-label="TRUSTA" data-state={state}>
      <canvas ref={ref} className="hero-canvas" aria-hidden="true" />
      {state === "failed" && <div className="hero-word" aria-hidden="true">TRUSTA</div>}
      <h1 className="visually-hidden">TRUSTA 银行助手</h1>
      <div className="corner tl">
        <p>AI 提议，你授权。<br />运行在你自己电脑上的银行助手。</p>
        <a className="pill" href="/">打开 TRUSTA</a>
      </div>
      <nav className="corner tr" aria-label="页面导航">
        <a href="#scenes">能做什么</a><a href="#trust">为什么可信</a><a href="#install">安装</a>
      </nav>
      <div className="corner bl">金融科技大赛参赛作品</div>
      <div className="corner br"><a href={github}>GitHub</a><span>/</span><a href="#install">安装</a></div>
      <div className="hint" aria-hidden="true">移动鼠标，划过这个词</div>
    </section>
  );
}
