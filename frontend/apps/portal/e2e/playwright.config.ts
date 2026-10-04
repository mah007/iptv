import path from "node:path";

import { defineConfig } from "@playwright/test";

/*
 * The portal journey (SPEC §15 E2E: sign in, browse, search in Arabic, play,
 * resume). It drives the running dev stack with the host's Chrome, as the
 * IPTVnator and admin journeys do: Playwright's bundled Chromium can't decode
 * H.264/AAC, so it couldn't play the sample media. Run it with
 * `make e2e-portal`, which creates the customer and passes everything below
 * as environment variables.
 */

const PORTAL_URL = process.env.PORTAL_URL ?? "http://app.localhost:8080";
const ARTIFACTS =
  process.env.E2E_ARTIFACTS ?? path.resolve(import.meta.dirname, "../../../../dist/portal-e2e");

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.e2e.ts",
  outputDir: `${ARTIFACTS}/results`,
  // One stack and one customer: in order, one at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: `${ARTIFACTS}/report`, open: "never" }]],
  use: {
    baseURL: PORTAL_URL,
    channel: process.env.E2E_CHANNEL ?? "chrome",
    headless: process.env.HEADED !== "1",
    locale: "en-GB",
    timezoneId: "Asia/Riyadh",
    viewport: { width: 1280, height: 800 },
    // No fades: axe measures contrast on settled colours.
    contextOptions: { reducedMotion: "reduce" },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
