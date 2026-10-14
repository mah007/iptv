import { type Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { env, expectAccessible, settled, totpCode, usePreferences } from "./support";

/*
 * Dynamic configuration (ADR-0019): set SMTP to the dev stack's Mailpit on the
 * Integrations page, send the test email and find it in Mailpit's API; a secret typed
 * in is never shown again; the System page lists the installation. The SMTP settings
 * are cleared at the end, so the stack falls back to its server config (Mailpit too).
 */

interface MailpitMessage {
  Subject: string;
  To: { Address: string }[];
}

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page }) => {
  await usePreferences(page, "en", "light");
});

function card(page: Page, title: string) {
  return page.locator("[data-integration]").filter({
    has: page.getByRole("heading", { name: title, exact: true }),
  });
}

async function clearEmailSettings(page: Page): Promise<void> {
  const keys = [
    "email.smtp_host",
    "email.smtp_port",
    "email.smtp_security",
    "email.smtp_username",
    "email.smtp_password",
    "email.from_address",
  ];
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "csrftoken");
  const headers = { "X-CSRFToken": csrf?.value ?? "" };
  const clear = () =>
    page.request.patch("/api/v1/admin/settings", {
      data: { values: Object.fromEntries(keys.map((key) => [key, null])) },
      headers,
    });
  let reply = await clear();
  if (reply.status() === 403) {
    // Settings need a recent confirmation (ADR-0021): give one, as the dialog would.
    const confirmed = await page.request.post("/api/v1/auth/step-up", {
      data: { code: totpCode() },
      headers,
    });
    expect(confirmed.status()).toBe(200);
    reply = await clear();
  }
  expect(reply.status()).toBe(200);
}

/**
 * Fill the SMTP form and save it. The card replaces its draft whenever newer values
 * arrive from the server (the refetch after "Move into settings", a background refresh),
 * so a fill can be undone before Save: fill again until the saved values are these.
 */
async function saveSmtp(email: ReturnType<typeof card>, port: string): Promise<void> {
  const page = email.page();
  const save = email.getByRole("button", { name: "Save" });
  const portField = email.getByLabel("SMTP port");
  await expect(async () => {
    await email.locator("[data-setting='email.smtp_host']").getByRole("textbox").fill("mailpit");
    await portField.fill(port);
    const encryption = email.getByRole("combobox", { name: "Encryption" });
    if ((await encryption.textContent())?.trim() !== "None") {
      await encryption.click();
      await page.getByRole("option", { name: "None" }).click();
    }
    await email.getByLabel("Send from").fill("Smart IPTV E2E <e2e@localhost>");
    // A test uses the saved settings: it waits for Save.
    await expect(email.getByRole("button", { name: "Send a test email to me" })).toBeDisabled({
      timeout: 2_000,
    });
    await save.click({ timeout: 2_000 });
    // Saved: nothing left to save, and the server's values are the ones typed in.
    await expect(save).toBeDisabled({ timeout: 5_000 });
    await expect(portField).toHaveValue(port, { timeout: 1_000 });
  }).toPass({ timeout: 45_000 });
}

test("send a test email through Mailpit from the Integrations page", async ({ page }) => {
  await page.goto("/settings/integrations");
  await settled(page);
  const email = card(page, "Email (SMTP)");
  await expect(email).toBeVisible();

  // The dev stack's server config already points at Mailpit: move the host in.
  const host = email.locator("[data-setting='email.smtp_host']");
  const move = host.getByRole("button", { name: "Move into settings" });
  if ((await move.count()) > 0) {
    await move.click();
    await expect(page.getByText("Email (SMTP): moved into the settings")).toBeVisible();
    // The card reloads its values once the move is stored; fill the form after that.
    await expect(move).toHaveCount(0);
  }

  // A wrong port first: the test explains the failure.
  await saveSmtp(email, "2525");
  await email.getByRole("button", { name: "Send a test email to me" }).click();
  const result = email.locator("[data-test-result]");
  await expect(result).toHaveAttribute("data-test-result", "unreachable", { timeout: 30_000 });
  await expect(result).toContainText("The service could not be reached");

  // Mailpit's real port: the test email goes out.
  await saveSmtp(email, "1025");
  await expect(email.getByRole("button", { name: "Send a test email to me" })).toBeEnabled();
  await email.getByRole("button", { name: "Send a test email to me" }).click();
  await expect(result).toHaveAttribute("data-test-result", "ok", { timeout: 30_000 });
  const reference = /Reference (\w+)/u.exec((await result.textContent()) ?? "")?.[1];
  expect(reference).toBeTruthy();

  // The message reached Mailpit, addressed to the signed-in admin.
  await expect
    .poll(
      async () => {
        const reply = await page.request.get(
          `${env.mailUrl}/api/v1/search?query=${encodeURIComponent(`subject:"${reference ?? ""}"`)}`,
        );
        if (!reply.ok()) return [];
        const body = (await reply.json()) as { messages?: MailpitMessage[] };
        return (body.messages ?? []).map((message) => message.To[0]?.Address ?? "");
      },
      { timeout: 30_000 },
    )
    .toEqual([`${env.user}@example.test`]);

  await clearEmailSettings(page);
});

test("a secret typed in is saved, never shown, and cleared", async ({ page }) => {
  // Built from parts: a key-shaped literal would trip secret scanners.
  const secret = ["sk", "test", `e2e${Date.now().toString(36)}SECRETVALUE`].join("_");
  const answers: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/v1/admin/")) {
      void response.text().then(
        (body) => answers.push(body),
        () => undefined,
      );
    }
  });
  await page.goto("/settings/integrations");
  await settled(page);
  const stripe = card(page, "Stripe");
  await stripe.getByRole("button", { name: "Set" }).first().click();
  await stripe.getByLabel("Stripe secret key").fill(secret);
  await stripe.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Stripe: saved")).toBeVisible();
  await expect(stripe.getByText("Incomplete")).toBeVisible(); // the webhook secret is missing
  await expect(stripe.getByRole("button", { name: "Replace" })).toBeVisible();
  await page.reload();
  await settled(page);
  expect(await page.content()).not.toContain(secret);
  expect(answers.join("\n")).not.toContain(secret);

  await card(page, "Stripe").getByRole("button", { name: "Clear" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Clear" }).click();
  await expect(card(page, "Stripe").getByText("Not set").first()).toBeVisible();
});

test("the System page and the Integrations page pass axe in Arabic", async ({ page }) => {
  await usePreferences(page, "ar", "dark");
  await page.goto("/settings/system");
  await settled(page);
  await expect(page.getByRole("heading", { level: 1, name: "النظام" })).toBeVisible();
  await expect(page.getByText("FIELD_ENCRYPTION_KEY")).toBeVisible();
  await expectAccessible(page, "system-ar");
  await page.goto("/settings/integrations");
  await settled(page);
  await expectAccessible(page, "integrations-ar");
});
