import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Hero from "./Hero.jsx";
import "./intro.css";

const GITHUB = "https://github.com/piggyz666/Trusta";
createRoot(document.getElementById("root")).render(<StrictMode><Hero github={GITHUB} /></StrictMode>);
