import { useEffect, useRef } from "react";

// A slow, drifting light fog drawn on a small canvas (no video, works offline). Dense toward the lower right and
// fading to black at the upper left, the way the reference card reads. Static when the user prefers reduced motion,
// paused when off-screen.
const BLOBS = [
  { color: [156, 201, 255], x: 0.78, y: 0.72, r: 0.55, ax: 0.08, ay: 0.06, sx: 0.07, sy: 0.05, a: 0.55 },
  { color: [124, 131, 255], x: 0.92, y: 0.38, r: 0.5, ax: 0.06, ay: 0.1, sx: 0.05, sy: 0.08, a: 0.6 },
  { color: [176, 140, 255], x: 0.62, y: 0.95, r: 0.45, ax: 0.1, ay: 0.05, sx: 0.06, sy: 0.04, a: 0.5 },
  { color: [80, 120, 255], x: 1.02, y: 0.88, r: 0.6, ax: 0.05, ay: 0.07, sx: 0.04, sy: 0.06, a: 0.5 },
  { color: [190, 225, 255], x: 0.86, y: 0.58, r: 0.22, ax: 0.12, ay: 0.09, sx: 0.09, sy: 0.11, a: 0.45 },
  { color: [140, 110, 255], x: 0.45, y: 0.82, r: 0.3, ax: 0.09, ay: 0.06, sx: 0.08, sy: 0.07, a: 0.32 },
  { color: [110, 170, 255], x: 0.7, y: 0.25, r: 0.25, ax: 0.07, ay: 0.08, sx: 0.1, sy: 0.06, a: 0.28 },
];

export default function Fog({ intensity = 1 }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas.getContext("2d");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, visible = true, last = 0, start = performance.now();
    const scale = 0.35; // low resolution + CSS upscaling = soft, cheap blur

    function resize() {
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(64, Math.round(rect.width * scale));
      canvas.height = Math.max(64, Math.round(rect.height * scale));
    }

    function draw(t) {
      const w = canvas.width, h = canvas.height, m = Math.max(w, h);
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";
      for (const b of BLOBS) {
        const x = (b.x + Math.sin(t * b.sx) * b.ax) * w;
        const y = (b.y + Math.cos(t * b.sy * 1.3) * b.ay) * h;
        const r = b.r * m * (1 + Math.sin(t * (b.sx + b.sy)) * 0.08);
        const g = ctx.createRadialGradient(x, y, 0, x, y, r);
        const [cr, cg, cb] = b.color;
        g.addColorStop(0, `rgba(${cr},${cg},${cb},${b.a * intensity})`);
        g.addColorStop(0.45, `rgba(${cr},${cg},${cb},${b.a * intensity * 0.35})`);
        g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.ellipse(x, y, r, r * 0.62, Math.sin(t * 0.05 + b.x) * 0.9, 0, Math.PI * 2);
        ctx.fill();
      }
      // fade to black toward the upper left so the headline stays legible
      ctx.globalCompositeOperation = "source-over";
      const fade = ctx.createLinearGradient(0, 0, w * 0.75, h * 0.9);
      fade.addColorStop(0, "rgba(0,0,0,0.92)");
      fade.addColorStop(0.55, "rgba(0,0,0,0.35)");
      fade.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = fade;
      ctx.fillRect(0, 0, w, h);
    }

    function frame(now) {
      raf = requestAnimationFrame(frame);
      if (!visible || now - last < 33) return; // ~30 fps is plenty for fog
      last = now;
      draw((now - start) / 1000);
    }

    resize();
    draw(12);
    const ro = new ResizeObserver(() => { resize(); draw((performance.now() - start) / 1000 + 12); });
    ro.observe(canvas);
    const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
    io.observe(canvas);
    if (!reduce) raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
  }, [intensity]);
  return <canvas ref={ref} className="fog" aria-hidden="true" />;
}
