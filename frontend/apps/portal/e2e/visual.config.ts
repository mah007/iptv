import path from "node:path";

import { defineConfig } from "@playwright/test";

/*
 * Visual regression of the portal's key pages (SPEC §15, ADR-0024): `make visual`.
 * Pages replay recorded API fixtures under a fixed clock (the admin's
 * e2e/visual/harness.ts), so only the SPA served by the dev stack is live.
 * `make visual RECORD=1` signs the end-to-end customer in and records the fixtures
 * again; `UPDATE=1` rewrites baselines.
 */

const PORTAL_URL = process.env.PORTAL_URL ?? "http://app.localhost:8080";
const ARTIFACTS =
  process.env.E2E_ARTIFACTS ?? path.resolve(import.meta.dirname, "../../../../dist/portal-visual");

export default defineConfig({
  testDir: "visual",
  testMatch: "**/*.visual.ts",
  snapshotPathTemplate: "{testDir}/baselines/{arg}{ext}",
  outputDir: `${ARTIFACTS}/results`,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: {
    timeout: 15_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.001, threshold: 0.2 },
  },
  reporter: [["list"], ["html", { outputFolder: `${ARTIFACTS}/report`, open: "never" }]],
  use: {
    baseURL: PORTAL_URL,
    channel: process.env.E2E_CHANNEL ?? "chrome",
    headless: true,
    locale: "en-GB",
    timezoneId: "Asia/Riyadh",
    contextOptions: { reducedMotion: "reduce" },
    trace: "retain-on-failure",
  },
});
