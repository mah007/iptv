import path from "node:path";

import { defineConfig } from "@playwright/test";

/*
 * The admin end-to-end suite (SPEC §15: login with MFA, create a customer,
 * resolve a review, kill a session; axe on every page in Arabic and English).
 * It drives the running dev stack with the host's Chrome, as compat's
 * IPTVnator journey does: run it with `make e2e-admin`, which creates the
 * end-to-end admin and passes everything below as environment variables.
 */

const ADMIN_URL = process.env.ADMIN_URL ?? "http://admin.localhost:8080";
const ARTIFACTS =
  process.env.E2E_ARTIFACTS ?? path.resolve(import.meta.dirname, "../../../../dist/admin-e2e");

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.e2e.ts",
  outputDir: `${ARTIFACTS}/results`,
  // One stack, one admin session and live data: run in order, one at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: `${ARTIFACTS}/report`, open: "never" }]],
  use: {
    baseURL: ADMIN_URL,
    channel: process.env.E2E_CHANNEL ?? "chrome",
    headless: true,
    locale: "en-GB",
    timezoneId: "Asia/Riyadh",
    viewport: { width: 1440, height: 900 },
    // No fades or pops: axe measures contrast on settled colours.
    contextOptions: { reducedMotion: "reduce" },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "sign-in", testMatch: "sign-in.e2e.ts" },
    {
      name: "admin",
      testIgnore: "sign-in.e2e.ts",
      dependencies: ["sign-in"],
      use: { storageState: `${ARTIFACTS}/admin-state.json` },
    },
  ],
});
