import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is served by the Python backend (backend/server.py) from ../frontend/dist.
// During development, `npm run dev` proxies /api to the local server on :8000.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../frontend/dist", emptyOutDir: true, assetsInlineLimit: 0, sourcemap: false },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
});
