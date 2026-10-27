import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { renameSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Public static website (landing page + docs), deployable to Cloudflare Pages or any static host.
//   npm run build:site   →   ../site-dist   (landing page becomes index.html)
const out = resolve(__dirname, "../site-dist");
export default defineConfig({
  plugins: [react(), {
    name: "landing-as-index",
    closeBundle() {
      renameSync(resolve(out, "intro.html"), resolve(out, "index.html"));
      // Cloudflare Pages: long cache for fingerprinted assets, a day for media, sane security headers
      writeFileSync(resolve(out, "_headers"), [
        "/*", "  X-Content-Type-Options: nosniff", "  Referrer-Policy: strict-origin-when-cross-origin", "  X-Frame-Options: DENY",
        "  Permissions-Policy: camera=(), microphone=(), geolocation=()",
        "  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'", "",
        "/assets/*", "  Cache-Control: public, max-age=31536000, immutable", "",
        "/hero/*", "  Cache-Control: public, max-age=86400", "", "/figs/*", "  Cache-Control: public, max-age=86400", "",
        "/fonts/*", "  Cache-Control: public, max-age=2592000", "",
      ].join("\n"));
      writeFileSync(resolve(out, "_redirects"), "/intro.html / 301\n");
    },
  }],
  base: "/",
  define: { "import.meta.env.VITE_SITE": JSON.stringify("static") },
  build: { outDir: out, emptyOutDir: true, assetsInlineLimit: 0, sourcemap: false,
    rollupOptions: { input: { intro: "intro.html", docs: "docs.html" } } },
});
