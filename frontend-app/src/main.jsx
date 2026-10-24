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
  const theme = chosen === "light" || chosen === "dark" ? chosen : systemLight.matches ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  // the browser's own toolbars take this colour; the page fades into the same colour at its edges so they read as one
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#dcefee" : "#041214");
}
syncTheme();
systemLight.addEventListener?.("change", syncTheme);
window.__trustaSyncTheme = syncTheme;

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ToastProvider><App /></ToastProvider>
  </StrictMode>,
);
