import { defineConfig, devices } from "@playwright/test";

// Run against a running stack (frontend :3000 proxying to API :8000). Uses the system Chrome.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], channel: "chrome" }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium", channel: "chrome" }, testMatch: /mobile\.spec\.ts/ },
  ],
});
