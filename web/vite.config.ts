import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/strixrun/web_dist",
    emptyOutDir: true,
    assetsInlineLimit: 0, // CSP: no data: scripts; fonts and images ship as files
  },
  server: {
    // The dev server proxies the API so the dashboard runs same-origin, like in production.
    proxy: Object.fromEntries(["/v1", "/admin", "/upstream", "/metrics"].map((p) => [p, { target: api, changeOrigin: false }])),
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
