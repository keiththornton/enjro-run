import { defineConfig } from "@playwright/test";

const port = 18799;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  // One shared server holds model state, so tests run one at a time.
  workers: 1,
  fullyParallel: false,
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: {
    // Fake llama-server children and a seeded model; serves the built dashboard (run `npm run build` first).
    command: `uv run python ../tests/fixtures/seeded_server.py --port ${port}`,
    url: `http://127.0.0.1:${port}/v1/models`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1280, height: 860 } } },
    { name: "phone", use: { browserName: "chromium", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
