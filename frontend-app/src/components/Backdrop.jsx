import { useEffect, useRef, useState } from "react";

// Full-screen flowing background (a pre-rendered fluid-smoke loop, shipped with the app — no external media).
// Replace /bg/flow-1080.mp4 and /bg/flow-720.mp4 (+ poster) with any other loop to change the look.
// Paused for "reduce motion", when the user turns motion off, and while the tab is hidden.
function motionAllowed() {
  let off = false;
  try { off = localStorage.getItem("trusta-motion") === "off"; } catch { /* ignore */ }
  return !off && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function Backdrop({ dim = false }) {
  const ref = useRef(null);
  const [moving, setMoving] = useState(motionAllowed);
  const small = typeof window !== "undefined" && Math.min(window.innerWidth, window.innerHeight) * (window.devicePixelRatio || 1) < 1100;
  const src = small ? "/bg/flow-720.mp4" : "/bg/flow-1080.mp4";

  useEffect(() => {
    const update = () => setMoving(motionAllowed());
    const visibility = () => { const v = ref.current; if (!v) return; document.hidden ? v.pause() : moving && v.play().catch(() => {}); };
    window.addEventListener("trusta-motion", update);
    document.addEventListener("visibilitychange", visibility);
    return () => { window.removeEventListener("trusta-motion", update); document.removeEventListener("visibilitychange", visibility); };
  }, [moving]);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (moving) { v.play().catch(() => {}); } else { v.pause(); }
  }, [moving]);

  return (
    <div className={`backdrop ${dim ? "dim" : ""}`} aria-hidden="true">
      <video ref={ref} src={src} poster="/bg/flow-poster.jpg" muted loop playsInline autoPlay={moving} preload="auto" disablePictureInPicture />
    </div>
  );
}
