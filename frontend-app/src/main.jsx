import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { ToastProvider } from "./components/ui.jsx";
import "./styles.css";

try {
  const theme = localStorage.getItem("trusta-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch { /* storage blocked */ }

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ToastProvider><App /></ToastProvider>
  </StrictMode>,
);
