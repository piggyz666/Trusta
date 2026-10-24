import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import { NAV, PAGES } from "./nav.js";
import { useLang } from "../site/lang.js";
import { GITHUB, BRANCH } from "../site/links.js";

const RAW = import.meta.glob("./content/*/*.md", { query: "?raw", import: "default", eager: true });
const source = (lang, slug) => RAW[`./content/${lang}/${slug}.md`] ?? RAW[`./content/zh/${slug}.md`] ?? "";
marked.setOptions({ gfm: true, breaks: false });

const UI = {
  zh: { docs: "文档", search: "搜索文档", searchHint: "输入关键词", noResults: "没有找到相关内容", onPage: "本页目录", prev: "上一页", next: "下一页",
    edit: "在 GitHub 上编辑此页", menu: "目录", home: "首页", lang: "EN", langLabel: "Switch to English", theme: "切换深浅色", copy: "复制", copied: "已复制", top: "回到顶部" },
  en: { docs: "Docs", search: "Search docs", searchHint: "Type to search", noResults: "Nothing found", onPage: "On this page", prev: "Previous", next: "Next",
    edit: "Edit this page on GitHub", menu: "Menu", home: "Home", lang: "中文", langLabel: "切换到中文", theme: "Toggle theme", copy: "Copy", copied: "Copied", top: "Back to top" },
};

function currentSlug() {
  const m = location.hash.match(/^#\/([a-z0-9-]+)/);
  return m && PAGES.some((p) => p.slug === m[1]) ? m[1] : "overview";
}

// ---------------------------------------------------------------- theme
function initialTheme() {
  try { const t = localStorage.getItem("trusta-docs-theme"); if (t === "light" || t === "dark") return t; } catch { /* blocked */ }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// ---------------------------------------------------------------- search index: one entry per section
function plain(md) {
  return md.replace(/```[\s\S]*?```/g, " ").replace(/`([^`]*)`/g, "$1").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_|-]+/g, " ").replace(/\s+/g, " ").trim();
}
function buildIndex(lang) {
  const out = [];
  for (const p of PAGES) {
    const md = source(lang, p.slug);
    const parts = md.split(/^(?=#{2,3} )/m);
    parts.forEach((part, i) => {
      const head = i === 0 ? null : part.match(/^#{2,3} (.+)$/m)?.[1];
      out.push({ slug: p.slug, page: p[lang], head, n: i, text: plain(i === 0 ? part.replace(/^# .+$/m, "") : part.replace(/^#{2,3} .+$/m, "")) });
    });
  }
  return out;
}
function searchIn(index, q) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const scored = [];
  for (const e of index) {
    const hay = `${e.page} ${e.head || ""} ${e.text}`.toLowerCase();
    if (!terms.every((t) => hay.includes(t))) continue;
    let score = 0;
    for (const t of terms) { if (e.page.toLowerCase().includes(t)) score += 6; if ((e.head || "").toLowerCase().includes(t)) score += 4; }
    const at = e.text.toLowerCase().indexOf(terms[0]);
    const snippet = at < 0 ? e.text.slice(0, 90) : (at > 30 ? "…" : "") + e.text.slice(Math.max(0, at - 30), at + 70) + "…";
    scored.push({ ...e, score, snippet });
  }
  return scored.sort((a, b) => b.score - a.score).slice(0, 12);
}

function Search({ ui, lang, onClose, go }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const index = useMemo(() => buildIndex(lang), [lang]);
  const results = useMemo(() => searchIn(index, q), [index, q]);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => setSel(0), [q]);
  const pick = (r) => { go(r.slug, r.n); onClose(); };
  const onKey = (e) => {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(results.length - 1, s + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    if (e.key === "Enter" && results[sel]) pick(results[sel]);
  };
  return (
    <div className="search-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="search" role="dialog" aria-modal="true" aria-label={ui.search}>
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder={ui.searchHint}
          aria-label={ui.search} aria-controls="search-results" aria-activedescendant={results[sel] ? `sr-${sel}` : undefined} />
        <ul id="search-results" role="listbox">
          {q && !results.length && <li className="empty">{ui.noResults}</li>}
          {results.map((r, i) => (
            <li key={`${r.slug}-${r.n}`} id={`sr-${i}`} role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => pick(r)}>
              <b>{r.page}{r.head ? <span> · {r.head}</span> : null}</b>
              <small>{r.snippet}</small>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page
export default function Docs() {
  const [lang, setLang] = useLang();
  const ui = UI[lang];
  const [slug, setSlug] = useState(currentSlug);
  const [theme, setTheme] = useState(initialTheme);
  const [toc, setToc] = useState([]);
  const [active, setActive] = useState(null);
  const [searching, setSearching] = useState(false);
  const [menu, setMenu] = useState(false);
  const article = useRef(null);
  const pendingSection = useRef(null);

  const page = PAGES.find((p) => p.slug === slug);
  const idx = PAGES.indexOf(page);
  const prev = PAGES[idx - 1], next = PAGES[idx + 1];
  const html = useMemo(() => marked.parse(source(lang, slug)), [lang, slug]);

  useEffect(() => {
    const on = () => { setSlug(currentSlug()); setMenu(false); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#111110" : "#edece8");
  }, [theme]);
  useEffect(() => { document.title = `${page[lang]} · TRUSTA ${ui.docs}`; }, [page, lang, ui.docs]);
  useEffect(() => {
    const on = (e) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !/input|textarea/i.test(document.activeElement?.tagName || ""))) {
        e.preventDefault(); setSearching(true);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  // after each render of the article: heading ids, table of contents, copy buttons, link handling
  useLayoutEffect(() => {
    const el = article.current;
    if (!el) return;
    const heads = [...el.querySelectorAll("h2, h3")];
    heads.forEach((h, i) => { h.id = `s-${i + 1}`; });
    setToc(heads.map((h) => ({ id: h.id, text: h.textContent, level: h.tagName === "H2" ? 2 : 3 })));
    el.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy")) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "copy"; b.textContent = ui.copy;
      b.addEventListener("click", async () => {
        const text = pre.querySelector("code")?.innerText ?? pre.innerText;
        try { await navigator.clipboard.writeText(text); } catch { /* clipboard blocked */ }
        b.textContent = ui.copied; setTimeout(() => { b.textContent = ui.copy; }, 1400);
      });
      pre.append(b);
    });
    el.querySelectorAll("table").forEach((t) => {
      if (t.parentElement.classList.contains("table-wrap")) return;
      const w = document.createElement("div"); w.className = "table-wrap"; t.replaceWith(w); w.append(t);
    });
    el.querySelectorAll("a[href^='http']").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
    const n = pendingSection.current; pendingSection.current = null;
    if (n) document.getElementById(`s-${n}`)?.scrollIntoView({ block: "start" });
    else window.scrollTo(0, 0);
  }, [html, ui.copy, ui.copied]);

  // scroll spy for the right-hand table of contents
  useEffect(() => {
    if (!toc.length) return undefined;
    const els = toc.map((t) => document.getElementById(t.id)).filter(Boolean);
    const io = new IntersectionObserver((entries) => {
      const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (vis[0]) setActive(vis[0].target.id);
    }, { rootMargin: "-72px 0px -70% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [toc]);

  const go = (s, n) => {
    pendingSection.current = n || null;
    if (s === slug) { if (n) document.getElementById(`s-${n}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    location.hash = `#/${s}`;
  };
  const toggleTheme = () => {
    const t = theme === "dark" ? "light" : "dark"; setTheme(t);
    try { localStorage.setItem("trusta-docs-theme", t); } catch { /* blocked */ }
  };
  const editUrl = `${GITHUB}/edit/${BRANCH}/frontend-app/src/docs/content/${lang}/${slug}.md`;

  return (
    <div className="docs">
      <header className="topbar">
        <button type="button" className="menu-btn" onClick={() => setMenu((m) => !m)} aria-expanded={menu} aria-controls="sidebar" aria-label={ui.menu}>
          <span /><span /><span />
        </button>
        <a className="brand" href="/intro.html"><img src="/mark.png" alt="" width="24" height="24" /><span>TRUSTA</span></a>
        <span className="crumb">{ui.docs}</span>
        <button type="button" className="search-btn" onClick={() => setSearching(true)}>
          <svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true"><path fill="none" stroke="currentColor" strokeWidth="1.8" d="M8.5 15a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13Zm9 3-4.5-4.5" /></svg>
          <span>{ui.search}</span><kbd>{/mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent) ? "⌘K" : "Ctrl K"}</kbd>
        </button>
        <nav className="top-links">
          <a className="wide" href="/intro.html">{ui.home}</a>
          <a className="icon" href={GITHUB} aria-label="GitHub">
            <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>
          </a>
          <button type="button" className="lang" onClick={() => setLang(lang === "zh" ? "en" : "zh")} aria-label={ui.langLabel}>{ui.lang}</button>
          <button type="button" className="theme" onClick={toggleTheme} aria-label={ui.theme}>
            {theme === "dark"
              ? <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden="true"><circle cx="10" cy="10" r="4" fill="currentColor" /><path stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6 16 16M4 16l1.4-1.4M14.6 5.4 16 4" /></svg>
              : <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden="true"><path fill="currentColor" d="M16.5 12.6A7 7 0 0 1 7.4 3.5a7 7 0 1 0 9.1 9.1Z" /></svg>}
          </button>
        </nav>
      </header>

      <div className="layout">
        <aside id="sidebar" className={`sidebar ${menu ? "open" : ""}`}>
          {NAV.map((g) => (
            <div key={g.en} className="group">
              <p>{g[lang]}</p>
              <ul>
                {g.pages.map(([s, zh, en]) => (
                  <li key={s}><a href={`#/${s}`} aria-current={s === slug ? "page" : undefined}>{lang === "zh" ? zh : en}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </aside>
        {menu && <div className="scrim" onClick={() => setMenu(false)} aria-hidden="true" />}

        <main className="content">
          <p className="eyebrow">{page.group[lang]}</p>
          <article ref={article} className="prose" dangerouslySetInnerHTML={{ __html: html }} />
          <div className="page-foot">
            <a className="edit" href={editUrl} target="_blank" rel="noopener noreferrer">{ui.edit} ↗</a>
            <nav className="pager">
              {prev ? <a href={`#/${prev.slug}`} className="prev"><small>{ui.prev}</small><b>{prev[lang]}</b></a> : <span />}
              {next ? <a href={`#/${next.slug}`} className="next"><small>{ui.next}</small><b>{next[lang]}</b></a> : <span />}
            </nav>
          </div>
        </main>

        <aside className="toc" aria-label={ui.onPage}>
          {toc.length > 1 && (
            <>
              <p>{ui.onPage}</p>
              <ul>
                {toc.map((t) => (
                  <li key={t.id} className={`l${t.level} ${active === t.id ? "on" : ""}`}>
                    <a href={`#/${slug}`} onClick={(e) => { e.preventDefault(); document.getElementById(t.id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{t.text}</a>
                  </li>
                ))}
              </ul>
              <button type="button" className="to-top" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>{ui.top} ↑</button>
            </>
          )}
        </aside>
      </div>
      {searching && <Search ui={ui} lang={lang} onClose={() => setSearching(false)} go={go} />}
    </div>
  );
}
