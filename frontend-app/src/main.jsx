import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { ToastProvider } from "./components/ui.jsx";
import "./styles.css";

try {
  const theme = localStorage.getItem("trusta-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch { /* storage blocked */ }

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
