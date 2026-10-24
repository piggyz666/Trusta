import { useEffect, useState } from "react";

// One light/dark choice for the whole site (landing, docs, app): explicit choice > system setting.
const KEY = "trusta-theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");
function stored() { try { const t = localStorage.getItem(KEY); return t === "light" || t === "dark" ? t : null; } catch { return null; } }
export function currentTheme() { return stored() || (media().matches ? "dark" : "light"); }
export function applyTheme(theme, colors = { light: "#edece8", dark: "#0f0f0e" }) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", colors[theme]);
}
export function useTheme(colors) {
  const [theme, setTheme] = useState(currentTheme);
  useEffect(() => { applyTheme(theme, colors); }, [theme]);
  useEffect(() => {
    const m = media();
    const on = () => { if (!stored()) setTheme(m.matches ? "dark" : "light"); };
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* storage blocked */ }
    setTheme(next);
  };
  return [theme, toggle];
}
