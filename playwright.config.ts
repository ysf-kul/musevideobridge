import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/browser",
  workers: 1,
  timeout: 60000,
  use: {
    actionTimeout: 10000,
    baseURL: "http://127.0.0.1:1420",
    headless: true,
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
      args: ["--no-sandbox"],
    },
    viewport: { width: 1160, height: 860 },
  },
  reporter: [
    ["list"],
    ["json", { outputFile: ".verification/phase3/browser-results.json" }],
  ],
});
