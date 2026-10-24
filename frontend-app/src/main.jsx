import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { ToastProvider } from "./components/ui.jsx";
import "./styles.css";

// Theme: an explicit choice wins; otherwise follow the operating system (and keep following it while open).
const systemLight = window.matchMedia("(prefers-color-scheme: light)");
function syncTheme() {
  let chosen = null;
  try { chosen = localStorage.getItem("trusta-theme"); } catch { /* storage blocked */ }
  document.documentElement.dataset.theme = chosen === "light" || chosen === "dark" ? chosen : systemLight.matches ? "light" : "dark";
}
syncTheme();
systemLight.addEventListener?.("change", syncTheme);
window.__trustaSyncTheme = syncTheme;

// Chromium can refract through backdrop-filter: url(#svg-filter); other engines get frosted glass only.
try {
  const brands = navigator.userAgentData?.brands?.map((b) => b.brand) || [];
  if (brands.some((b) => /Chromium|Google Chrome|Microsoft Edge/.test(b))) document.documentElement.classList.add("liquid");
} catch { /* ignore */ }

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ToastProvider><App /></ToastProvider>
  </StrictMode>,
);
