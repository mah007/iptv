import { expect, test, type Page } from "@playwright/test";

import { expectAccessible, settled, usePreferences, type Language, type Theme } from "./support";

/*
 * axe on every admin page (SPEC §15): English in the light theme and Arabic
 * (right to left) in the dark theme, so both languages and both themes are
 * checked. Detail pages use the first row of their list. Every page must also
 * fit a 390 px phone in Arabic without scrolling sideways (SPEC §8.1).
 */

const PAGES = [
  "/",
  "/sessions",
  "/customers",
  "/subscriptions",
  "/libraries",
  "/movies",
  "/series",
  "/review",
  "/categories",
  "/collections",
  "/transcode",
  "/storage",
  "/plans",
  "/payments",
  "/invoices",
  "/notifications",
  "/templates",
  "/admins",
  "/access-rules",
  "/audit",
  "/health",
  "/settings",
] as const;

const VARIANTS: readonly { language: Language; theme: Theme }[] = [
  { language: "en", theme: "light" },
  { language: "ar", theme: "dark" },
];

/** The href of the first link to a detail page under `prefix`, e.g. "/customers/…". */
async function firstDetail(page: Page, list: string, prefix: string): Promise<string | null> {
  await page.goto(list);
  await settled(page);
  const link = page.locator(`main a[href^='${prefix}']`).first();
  if ((await link.count()) === 0) return null;
  return link.getAttribute("href");
}

for (const { language, theme } of VARIANTS) {
  test.describe(`${language}, ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await usePreferences(page, language, theme);
    });

    for (const path of PAGES) {
      test(`page ${path}`, async ({ page }) => {
        await page.goto(path);
        await expect(page.locator("html")).toHaveAttribute(
          "dir",
          language === "ar" ? "rtl" : "ltr",
        );
        await expect(page.locator("main h1").first()).toBeVisible();
        await settled(page);
        await expectAccessible(page, `${path} (${language}, ${theme})`);
      });
    }

    for (const [list, prefix, tabs] of [
      [
        "/customers",
        "/customers/",
        [
          "overview",
          "devices",
          "subscriptions",
          "billing",
          "sessions",
          "history",
          "security",
          "activity",
        ],
      ],
      ["/movies", "/movies/", []],
      ["/series", "/series/", []],
    ] as const) {
      test(`detail of ${list}`, async ({ page }) => {
        const href = await firstDetail(page, list, prefix);
        test.skip(href === null, `no item in ${list}`);
        if (href === null) return;
        for (const tab of tabs.length > 0 ? tabs : [null]) {
          await page.goto(tab ? `${href}?tab=${tab}` : href);
          await expect(page.locator("main h1").first()).toBeVisible();
          await settled(page);
          await expectAccessible(page, `${href}${tab ? ` ${tab}` : ""} (${language}, ${theme})`);
        }
      });
    }

    test("every setting is translated", async ({ page }) => {
      await page.goto("/settings");
      await settled(page);
      await expect(page.locator("[data-setting]").first()).toBeVisible();
      const untranslated = await page
        .locator("[data-untranslated]")
        .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-setting")));
      expect(untranslated, `settings without a ${language} label`).toEqual([]);
    });

    test("command palette and shortcuts help", async ({ page }) => {
      await page.goto("/");
      await settled(page);
      await page.keyboard.press("Control+k");
      await expect(page.getByRole("dialog")).toBeVisible();
      await expectAccessible(page, `command palette (${language}, ${theme})`);
      await page.keyboard.press("Escape");
      await page.locator("main").focus();
      await page.keyboard.press("?");
      await expect(page.getByRole("dialog")).toBeVisible();
      await expectAccessible(page, `shortcuts help (${language}, ${theme})`);
    });
  });
}

test.describe("phone width, Arabic", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await usePreferences(page, "ar", "dark");
  });

  async function expectNoSidewaysScroll(page: Page, what: string): Promise<void> {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${what} scrolls sideways at 390 px`).toBeLessThanOrEqual(0);
  }

  for (const path of PAGES) {
    test(`no sideways scroll on ${path}`, async ({ page }) => {
      await page.goto(path);
      await settled(page);
      await expectNoSidewaysScroll(page, path);
    });
  }

  for (const [list, prefix] of [
    ["/customers", "/customers/"],
    ["/movies", "/movies/"],
    ["/series", "/series/"],
  ] as const) {
    test(`no sideways scroll on a detail of ${list}`, async ({ page }) => {
      const href = await firstDetail(page, list, prefix);
      test.skip(href === null, `no item in ${list}`);
      if (href === null) return;
      await page.goto(href);
      await settled(page);
      await expectNoSidewaysScroll(page, href);
    });
  }
});
