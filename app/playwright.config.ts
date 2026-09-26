import { defineConfig, devices } from "@playwright/test";

// Same port as `npm run dev`: Next 16 allows one dev server per project, so a running one is reused.
const PORT = 3100;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  retries: 0,
  reporter: "list",
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "mobile-chromium", use: { ...devices["Pixel 7"] } }],
  webServer: {
    command: `npx next dev --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { NEXT_PUBLIC_API_MODE: "mock" },
  },
});
