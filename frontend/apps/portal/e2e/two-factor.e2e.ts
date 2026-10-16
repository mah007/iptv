import {
  expect,
  expectAccessible,
  inEnglish,
  login,
  password,
  signIn,
  test,
  totpCode,
} from "./fixtures";

/*
 * Optional two-factor sign-in (ADR-0024): the customer of `make e2e-portal` sets it up on
 * the account page with an authenticator code (the DEBUG-only totp_code command plays the
 * phone), signs out, signs in with the password and a code, and switches it off again,
 * leaving the account as it was. Runs after the other journeys (files run in order).
 */

test.beforeAll(() => {
  if (!login || !password) {
    throw new Error("Set E2E_PORTAL_USER and E2E_PORTAL_PASS (make e2e-portal does).");
  }
});

test("two-factor sign-in: set up, sign in with a code, switch off", async ({ page }) => {
  await inEnglish(page);
  await signIn(page);

  await test.step("set it up on the account page", async () => {
    await page.goto("/account");
    await page.getByRole("button", { name: "Set up two-factor sign-in" }).click();
    const dialog = page.getByRole("dialog", { name: "Set up two-factor sign-in" });
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(
      dialog.getByRole("img", { name: "QR code for your authenticator app" }),
    ).toBeVisible();
    await expectAccessible(page, "two-factor set-up");
    await dialog.getByLabel("Code from the app").fill(totpCode());
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Every new sign-in, here or in an app")).toBeVisible();
  });

  await test.step("sign out, then in with the password and a code", async () => {
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.locator('input[autocomplete="username"]').fill(login);
    await page.locator('input[autocomplete="current-password"]').fill(password);
    await page.locator('button[type="submit"]').click();
    await expect(page.getByRole("heading", { name: "Enter your code" })).toBeVisible();
    await expectAccessible(page, "sign-in code step");
    await page.getByLabel("Authentication code").fill("000000");
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(page.getByText(/That code didn't work/u)).toBeVisible();
    await page.getByLabel("Authentication code").fill(totpCode());
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(page).toHaveURL(/\/$/u);
  });

  await test.step("switch it off again", async () => {
    await page.goto("/account");
    await page.getByRole("button", { name: "Turn off" }).click();
    const confirm = page.getByRole("alertdialog", { name: "Turn off two-factor sign-in?" });
    await confirm.getByRole("button", { name: "Turn off" }).click();
    await expect(page.getByText("Two-factor sign-in is off.").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Set up two-factor sign-in" })).toBeVisible();
  });
});
