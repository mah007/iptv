import type { CDPSession, Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { env, expectAccessible, settled, usePreferences } from "./support";

/*
 * Security keys (ADR-0021): an admin adds a passkey on *Sign-in and security*,
 * then signs in with it instead of a code, and removes it again. Chrome's
 * virtual authenticator (DevTools protocol) stands in for the hardware; the
 * credential moves to a second, signed-out browser context for the sign-in, so
 * the suite's shared session is left alone.
 */

const KEY_NAME = `E2E virtual key ${Date.now().toString(36)}`;

interface VirtualCredential {
  credentialId: string;
  isResidentCredential: boolean;
  rpId?: string;
  privateKey: string;
  userHandle?: string;
  signCount: number;
}

async function virtualAuthenticator(page: Page): Promise<{ cdp: CDPSession; id: string }> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return { cdp, id: authenticatorId };
}

test.describe.configure({ mode: "serial" });

let credential: VirtualCredential | undefined;

test("an admin adds a security key", async ({ page }) => {
  await usePreferences(page, "en", "light");
  const authenticator = await virtualAuthenticator(page);
  await page.goto("/account/security");
  await settled(page);
  await expectAccessible(page, "sign-in and security");

  await page.getByRole("button", { name: "Add a key" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a security key or passkey" });
  await dialog.getByLabel("Name").fill(KEY_NAME);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Key added.")).toBeVisible();
  await expect(page.getByText(KEY_NAME)).toBeVisible();

  const { credentials } = await authenticator.cdp.send("WebAuthn.getCredentials", {
    authenticatorId: authenticator.id,
  });
  credential = credentials[0];
  expect(credential, "the virtual authenticator holds the new key").toBeDefined();
});

test("the admin signs in with the key, then removes it", async ({ browser }) => {
  if (credential === undefined) throw new Error("The previous test added no key.");
  // A second browser, signed out, that holds the same key.
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await usePreferences(page, "en", "light");
  const authenticator = await virtualAuthenticator(page);
  await authenticator.cdp.send("WebAuthn.addCredential", {
    authenticatorId: authenticator.id,
    credential,
  });

  await page.goto("/login");
  await page.getByLabel("Username or email").fill(env.user);
  await page.getByLabel("Password", { exact: true }).fill(env.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Use a security key or passkey" }).click();
  await expect(page).toHaveURL(/\/$/u);
  await settled(page);

  // Signing in counts as a confirmation, so removing the key asks nothing more.
  await page.goto("/account/security");
  await settled(page);
  const row = page.getByRole("listitem").filter({ hasText: KEY_NAME });
  await expect(row.getByText(/last used/u)).toBeVisible();
  await row.getByRole("button", { name: "Remove" }).click();
  await page
    .getByRole("alertdialog", { name: `Remove “${KEY_NAME}”?` })
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(page.getByText("Key removed.")).toBeVisible();
  await expect(page.getByText(KEY_NAME)).toHaveCount(0);
  await context.close();
});
