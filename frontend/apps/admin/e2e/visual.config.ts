import path from "node:path";

import { defineConfig } from "@playwright/test";

/*
 * Visual regression of the admin's key pages (SPEC §15, ADR-0024): `make visual`.
 * Pages replay recorded API fixtures under a fixed clock (visual/harness.ts), so only
 * the SPA served by the dev stack is live. `make visual RECORD=1` signs in (as
 * `make e2e-admin` does) and records the fixtures again; `UPDATE=1` rewrites baselines.
 */

const ADMIN_URL = process.env.ADMIN_URL ?? "http://admin.localhost:8080";
const ARTIFACTS =
  process.env.E2E_ARTIFACTS ?? path.resolve(import.meta.dirname, "../../../../dist/admin-visual");
const RECORDING = process.env.VISUAL_RECORD === "1";

export default defineConfig({
  testDir: "visual",
  testMatch: "**/*.visual.ts",
  // Baselines are committed next to the tests; one file per page and variant.
  snapshotPathTemplate: "{testDir}/baselines/{arg}{ext}",
  outputDir: `${ARTIFACTS}/results`,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: {
    timeout: 15_000,
    // Anti-aliasing may differ by a few pixels between Chrome builds; layout shifts don't.
    toHaveScreenshot: { maxDiffPixelRatio: 0.001, threshold: 0.2 },
  },
  reporter: [["list"], ["html", { outputFolder: `${ARTIFACTS}/report`, open: "never" }]],
  use: {
    baseURL: ADMIN_URL,
    channel: process.env.E2E_CHANNEL ?? "chrome",
    headless: true,
    locale: "en-GB",
    timezoneId: "Asia/Riyadh",
    contextOptions: { reducedMotion: "reduce" },
    trace: "retain-on-failure",
  },
  projects: RECORDING
    ? [
        // Recording reads the live API, so it signs in first (sign-in.e2e.ts saves the session).
        { name: "sign-in", testDir: ".", testMatch: "sign-in.e2e.ts" },
        {
          name: "visual",
          dependencies: ["sign-in"],
          use: { storageState: `${ARTIFACTS}/admin-state.json` },
        },
      ]
    : [{ name: "visual" }],
});
