import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Intro from "./Intro.jsx";
import "./intro.css";

document.documentElement.dataset.theme = "dark";
createRoot(document.getElementById("root")).render(<StrictMode><Intro /></StrictMode>);
