import { expect, test } from "@playwright/test";

import { env, expectAccessible, totpCode, usePreferences } from "./support";

/*
 * Sign-in with password and TOTP (SPEC §15 admin E2E: "login with MFA"). The
 * session it ends with is saved for every other test (project "admin").
 */

test("the sign-in page is accessible in Arabic", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await usePreferences(page, "ar", "dark");
  await page.goto("/login");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectAccessible(page, "login (ar, dark)");
  await context.close();
});

test("an admin signs in with a password and an authenticator code", async ({ page }) => {
  await usePreferences(page, "en", "light");
  await page.goto("/customers");
  // Not signed in: the app sends the admin to sign in, then back.
  await expect(page).toHaveURL(/\/login\?redirect=/u);
  await expectAccessible(page, "login (en, light)");

  await page.getByLabel("Username or email").fill(env.user);
  await page.getByLabel("Password", { exact: true }).fill(env.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  // Enrolled admins get the code step; a reset authenticator gets the setup step first.
  const code = page.getByLabel("Authentication code");
  await expect(code).toBeVisible();
  await expectAccessible(page, "MFA step");
  await code.fill(totpCode());
  await page.getByRole("button", { name: /^Verify/u }).click();

  await expect(page).toHaveURL(/\/customers$/u);
  await expect(page.getByRole("heading", { name: "Customers", level: 1 })).toBeVisible();
  await page.context().storageState({ path: `${env.artifacts}/admin-state.json` });
});
