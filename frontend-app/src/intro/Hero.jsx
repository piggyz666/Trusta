import { useEffect, useRef, useState } from "react";
import { createBalloonHero } from "./balloon.js";

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function Hero({ github }) {
  const ref = useRef(null);
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    const canvas = ref.current;
    let hero, raf = 0, running = false, last = performance.now(), lastMove = 0, material = -1;
    let pointer = null, prev = null, alive = true;
    const HOLD = 1.1, HEAL = 0.32;            // seconds the opening stays, then how fast it closes (per second)
    const ages = { lastPaint: -1e9 };

    (async () => {
      try { await document.fonts.load('900 100px "Inter Variable"'); } catch { /* use fallback face */ }
      if (!alive) return;
      try { hero = createBalloonHero(canvas); } catch (e) { console.warn(e); hero = null; }
      if (!hero) { setFallback(true); return; }
      hero.render(0);
      if (!reduced()) setTimeout(intro, 700);
    })();

    const uv = (x, y) => { const r = canvas.getBoundingClientRect(); return [(x - r.left) / r.width, 1 - (y - r.top) / r.height]; };
    // brush ≈ 45% of the letter height, so a stroke opens part of a word, not all of it
    const radius = () => { const L = hero.layout; return Math.max(18, (L.capTop - L.capBottom) * canvas.height * 0.45); };

    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (hero.resize()) { /* re-laid out */ }
      const t = now / 1000;
      const decay = t - ages.lastPaint > HOLD ? HEAL * dt : 0;
      if (pointer) {
        hero.brush(pointer, prev || pointer, radius(), material, decay, true);
        prev = pointer; pointer = null;
      } else {
        hero.brush([0, 0], [0, 0], 1, material, decay, false);
      }
      hero.render(t);
      if (t - ages.lastPaint > HOLD + 1 / HEAL + 0.5) { running = false; return; }   // fully healed: stop
      raf = requestAnimationFrame(frame);
    }
    function wake() { if (!running && hero) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); } }
    function paint(p) {
      const now = performance.now() / 1000;
      if (now - lastMove > 0.45) { material = (material + 1) % hero.materials; prev = null; }   // a new stroke, a new material
      lastMove = now; ages.lastPaint = now;
      pointer = p;
      wake();
    }
    // opening: one stroke sweeps through the middle of the word
    function intro() {
      if (!hero || !alive) return;
      const L = hero.layout; const yMid = (L.capTop + L.capBottom) / 2;
      let i = 0; const n = 46;
      const step = () => {
        if (!alive) return;
        const x = 0.12 + (i / n) * 0.7, y = yMid + Math.sin(i / n * Math.PI * 1.6) * 0.06;
        paint([x, y]); i += 1;
        if (i <= n) setTimeout(step, 22);
      };
      step();
    }
    const onMove = (e) => { if (hero) paint(uv(e.clientX, e.clientY)); };
    const onLeave = () => { prev = null; };
    const onResize = () => { if (hero) { hero.resize(); hero.render(performance.now() / 1000); } };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    window.addEventListener("resize", onResize);
    return () => {
      alive = false; cancelAnimationFrame(raf);
      canvas.removeEventListener("pointermove", onMove); canvas.removeEventListener("pointerdown", onMove);
      canvas.removeEventListener("pointerleave", onLeave); window.removeEventListener("resize", onResize);
      hero?.destroy();
    };
  }, []);

  return (
    <section className="hero" aria-label="TRUSTA">
      <canvas ref={ref} className="hero-canvas" aria-hidden="true" />
      {fallback && <div className="hero-word" aria-hidden="true">TRUSTA</div>}
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
