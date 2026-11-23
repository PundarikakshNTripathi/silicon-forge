// SPDX-License-Identifier: AGPL-3.0-or-later
import { rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_BASE: where the static demo is served from; GitHub Pages project sites use the repo name. Written without slashes
// ("silicon-forge") because Git Bash on Windows rewrites a value that starts with one. Normal builds stay at /.
const sub = (process.env.VITE_BASE ?? "").replace(/^\/+|\/+$/g, "");

export default defineConfig(({ mode }) => ({
  base: sub ? `/${sub}/` : "/",
  plugins: [
    react(),
    {
      // The demo's data lives in public/ so Vite copies it; only the demo build should ship it (the desktop app embeds dist/).
      name: "drop-demo-data-outside-demo",
      apply: "build" as const,
      closeBundle() {
        if (mode === "demo") return;
        const out = fileURLToPath(new URL("./dist", import.meta.url));
        for (const d of ["demo-data", "demo-assets", "_redirects"]) rmSync(`${out}/${d}`, { recursive: true, force: true });
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.SF_API ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
}));
