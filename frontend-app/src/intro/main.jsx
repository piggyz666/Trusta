import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Hero from "./Hero.jsx";
import { Footer, Install, Nav, Numbers, Product, Statement, Trust } from "./Sections.jsx";
import { COPY } from "./copy.js";
import { useLang } from "../site/lang.js";
import "./intro.css";

function Page() {
  const [lang, setLang] = useLang();
  const c = COPY[lang];
  return (
    <>
      <Nav c={c} lang={lang} setLang={setLang} />
      <main>
        <Hero c={c} />
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
