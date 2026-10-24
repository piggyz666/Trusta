import { useEffect, useState } from "react";

// One language choice shared by the landing page and the docs: explicit choice > ?lang= > browser language.
const KEY = "trusta-lang";
export function initialLang() {
  try {
    const q = new URLSearchParams(location.search).get("lang");
    if (q === "zh" || q === "en") return q;
    const saved = localStorage.getItem(KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch { /* storage blocked */ }
  return (navigator.language || "zh").toLowerCase().startsWith("zh") ? "zh" : "en";
}
export function setLang(lang) {
  try { localStorage.setItem(KEY, lang); } catch { /* storage blocked */ }
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  window.dispatchEvent(new CustomEvent("trusta-lang", { detail: lang }));
}
export function useLang() {
  const [lang, set] = useState(initialLang);
  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    const on = (e) => set(e.detail);
    window.addEventListener("trusta-lang", on);
    return () => window.removeEventListener("trusta-lang", on);
  }, [lang]);
  return [lang, setLang];
}
