import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Hero from "./Hero.jsx";
import { Footer, Install, Nav, Numbers, Product, Statement, Trust } from "./Sections.jsx";
import { COPY } from "./copy.js";
import { useLang } from "../site/lang.js";
import { applyTheme, currentTheme, useTheme } from "../site/theme.js";

applyTheme(currentTheme(), { light: "#edece8", dark: "#0f0f0e" });
import "./intro.css";

function Page() {
  const [lang, setLang] = useLang();
  const [theme, toggleTheme] = useTheme({ light: "#edece8", dark: "#0f0f0e" });
  const c = COPY[lang];
  return (
    <>
      <Nav c={c} lang={lang} setLang={setLang} theme={theme} toggleTheme={toggleTheme} />
      <main>
        <Hero c={c} theme={theme} />
        <Statement c={c} />
        <Product c={c} />
        <Trust c={c} />
        <Numbers c={c} />
        <Install c={c} />
      </main>
      <Footer c={c} />
    </>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><Page /></StrictMode>);
