import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Docs from "./Docs.jsx";
import "./docs.css";

// pick the theme before the first paint so dark-mode readers never see a light flash
import { applyTheme, currentTheme } from "../site/theme.js";
applyTheme(currentTheme(), { light: "#edece8", dark: "#111110" });

createRoot(document.getElementById("root")).render(<StrictMode><Docs /></StrictMode>);
