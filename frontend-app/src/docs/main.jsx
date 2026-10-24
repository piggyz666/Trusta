import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Docs from "./Docs.jsx";
import "./docs.css";

// pick the theme before the first paint so dark-mode readers never see a light flash
try {
  const t = localStorage.getItem("trusta-docs-theme");
  document.documentElement.dataset.theme = t === "light" || t === "dark" ? t : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch { /* storage blocked */ }

createRoot(document.getElementById("root")).render(<StrictMode><Docs /></StrictMode>);
