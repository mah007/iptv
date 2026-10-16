import path from "node:path";

import type { Page } from "@playwright/test";

import {
  expect,
  expectLooksSame,
  RECORDING,
  settle,
  test,
  usePreferences,
  useFixtures,
  VARIANTS,
  type Suite,
} from "./harness";

/*
 * The admin's key pages (SPEC §15 visual regression): dashboard, customers, a
 * customer, a movie, live TV and settings, in English (light) and Arabic (dark, right
 * to left), at desktop width and at 390 px. Baselines: visual/baselines/admin-*.png.
 */

const SUITE: Suite = { fixtures: path.resolve(import.meta.dirname, "fixtures/admin") };

/** The first detail link of a list (recording only: replays use the recorded path). */
function firstOf(page: Page, list: string, prefix: string): () => Promise<string> {
  return async () => {
    await page.goto(list);
    await settle(page);
    const href = await page.locator(`main a[href^='${prefix}']`).first().getAttribute("href");
    if (href === null) throw new Error(`Nothing in ${list} to record.`);
    return href;
  };
}

const PAGES: readonly {
  name: string;
  path: string | ((page: Page) => () => Promise<string>);
  /** Settings is over 20,000 px long: its first screen is enough. */
  fullPage?: boolean;
}[] = [
  { name: "dashboard", path: "/" },
  { name: "customers", path: "/customers" },
  { name: "customer-detail", path: (page) => firstOf(page, "/customers", "/customers/") },
  { name: "movie-detail", path: (page) => firstOf(page, "/movies", "/movies/") },
  { name: "live-tv", path: "/live" },
  { name: "settings", path: "/settings", fullPage: false },
];

for (const spec of PAGES) {
  for (const variant of VARIANTS) {
    test(`${spec.name}, ${variant.label}`, async ({ page }) => {
      await page.setViewportSize({ width: variant.width, height: variant.height });
      await usePreferences(page, variant.language, variant.theme);
      const replay = await useFixtures(
        page,
        SUITE,
        spec.name,
        typeof spec.path === "string" ? spec.path : spec.path(page),
      );
      await page.goto(replay.path);
      await expect(page.locator("html")).toHaveAttribute(
        "dir",
        variant.language === "ar" ? "rtl" : "ltr",
      );
      await expect(page.locator("main h1").first()).toBeVisible();
      await settle(page);
      if (RECORDING) {
        replay.save();
        return;
      }
      expect(replay.missing, "requests without a fixture (make visual RECORD=1)").toEqual([]);
      await expectLooksSame(page, `admin-${spec.name}-${variant.label}`, {
        fullPage: spec.fullPage ?? true,
      });
    });
  }
}
