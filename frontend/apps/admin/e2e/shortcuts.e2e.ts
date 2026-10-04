import { expect, test } from "@playwright/test";

import { settled, usePreferences } from "./support";

/* Keyboard-first admin (SPEC §8.2): "go to" sequences, the palette and table rows. */

test.beforeEach(async ({ page }) => {
  await usePreferences(page, "en", "light");
});

test("g then c goes to customers; j and Enter open a row", async ({ page }) => {
  await page.goto("/");
  await settled(page);
  await page.locator("main").focus();
  await page.keyboard.press("g");
  await page.keyboard.press("c");
  await expect(page).toHaveURL(/\/customers$/u);
  const row = page.locator("main tbody tr[data-row]").first();
  await expect(row).toBeVisible();
  await page.locator("main").focus();
  await page.keyboard.press("j");
  await expect(row).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/customers\/[0-9a-f-]+/u);
});

test("the command palette finds a page and opens it", async ({ page }) => {
  await page.goto("/");
  await settled(page);
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await expect(palette).toBeVisible();
  await page.keyboard.type("health");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/health$/u);
  await expect(page.getByRole("heading", { level: 1, name: /System health/u })).toBeVisible();
});
