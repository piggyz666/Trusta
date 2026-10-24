import { useEffect, useRef, useState } from "react";
import { createHoverInk } from "./hoverInk.js";

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const HERO = "/hero/";
const MATERIALS = ["gold", "teal", "pearl", "silver", "candy"].map((n) => `${HERO}${n}.webp`);

export default function Hero({ github }) {
  const ref = useRef(null);
  const [state, setState] = useState("loading");   // loading | ready | failed

  useEffect(() => {
    const canvas = ref.current;
    let ink = null, raf = 0, running = false, last = 0, alive = true;

    function frame(now) {
      const t = now / 1000, dt = Math.min(0.05, t - last); last = t;
      ink.step(dt, t); ink.draw();
      if (ink.busy()) raf = requestAnimationFrame(frame); else running = false;
    }
    function wake() { if (!running && ink) { running = true; last = performance.now() / 1000; raf = requestAnimationFrame(frame); } }

    // opening gesture: one unhurried sweep through the word, so first-time visitors see what it does
    function intro() {
      if (!alive || !ink) return;
      const r = canvas.getBoundingClientRect(), R = ink.rect, d = ink.dpr;
      const t0 = performance.now();
      const tick = () => {
        if (!alive) return;
        const u = Math.min(1, (performance.now() - t0) / 1500);
        const e = u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2;
        const px = R.x + R.w * (0.04 + 0.92 * e), py = R.y + R.h * (0.5 + Math.sin(e * Math.PI * 2) * 0.16);
        ink.touch(r.left + px / d, r.top + py / d, performance.now() / 1000); wake();
        if (u < 1) setTimeout(tick, 16); else ink.release();
      };
      tick();
    }

    createHoverInk(canvas, { ink: `${HERO}ink.webp`, materials: MATERIALS }).then((h) => {
      if (!alive) return;
      ink = h; ink.draw(); setState("ready");
      if (!reduced()) setTimeout(intro, 600);
    }).catch((e) => { console.warn(e); if (alive) setState("failed"); });

    const onMove = (e) => { if (ink) { ink.touch(e.clientX, e.clientY, performance.now() / 1000); wake(); } };
    const onLeave = () => ink?.release();
    const onResize = () => { if (ink && ink.layout()) { ink.draw(); wake(); } };
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
