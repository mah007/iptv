import { expect, request, test, type Page } from "@playwright/test";

import { env, settled, usePreferences } from "./support";

/*
 * The admin journeys of SPEC §15: create a customer with chosen credentials,
 * reset a device, start a scan, resolve a review and kill a live session.
 * They run in order against the dev stack; the customer made first is the one
 * that plays and gets stopped at the end.
 */

const stamp = Date.now().toString(36);
const customer = {
  name: `E2E Admin ${stamp}`,
  username: `e2e-acct-${stamp}`,
  device: "Living room TV",
  login: `e2e-tv-${stamp}`,
  password: `E2e.pass~${stamp}`,
};

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page }) => {
  await usePreferences(page, "en", "light");
});

async function openCustomer(page: Page): Promise<void> {
  await page.goto(`/customers?q=${encodeURIComponent(customer.name)}`);
  await page.getByRole("link", { name: customer.name }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(customer.name, "u") }),
  ).toBeVisible();
}

test("create a customer with chosen credentials", async ({ page }) => {
  await page.goto("/customers");
  await page.getByRole("link", { name: "New customer" }).click();
  const sheet = page.getByRole("dialog", { name: "New customer" });

  await sheet.getByLabel("Full name").fill(customer.name);
  await sheet.getByLabel("Account username (optional)").fill(customer.username);
  await sheet.getByRole("button", { name: "Next" }).click();
  await expect(sheet.getByRole("radiogroup", { name: "Access length" })).toBeVisible();
  await sheet.getByRole("button", { name: "Next" }).click();

  await sheet.getByLabel("Device name").fill(customer.device);
  await sheet.getByRole("radio", { name: /Set manually/u }).click();
  await sheet.getByLabel("Username", { exact: true }).fill(customer.login);
  await sheet.getByLabel("Password", { exact: true }).fill(customer.password);
  await sheet.getByRole("button", { name: "Create customer" }).click();

  const done = page.getByRole("dialog", { name: "Customer created" });
  await expect(done).toBeVisible();
  await expect(done.getByRole("textbox", { name: "Username" })).toHaveValue(customer.login);
  await done.getByRole("button", { name: "Reveal" }).click();
  await expect(done.getByText(customer.password)).toBeVisible();
  await done.getByRole("link", { name: "Open customer" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(customer.name, "u") }),
  ).toBeVisible();
});

test("reset a device's password and see the new one once", async ({ page }) => {
  await openCustomer(page);
  await page.getByRole("tab", { name: /Devices/u }).click();
  await page.getByRole("button", { name: `Actions for ${customer.device}` }).click();
  await page.getByRole("menuitem", { name: "Reset password" }).click();
  const confirm = page.getByRole("dialog", { name: `Reset the password of ${customer.device}?` });
  await confirm.getByRole("radio", { name: /Set manually/u }).click();
  await confirm.getByLabel("Password", { exact: true }).fill(customer.password);
  await confirm.getByRole("button", { name: "Reset password" }).click();
  const issued = page.getByRole("dialog", { name: "New password issued" });
  await expect(issued).toBeVisible();
  await expect(issued.getByRole("textbox", { name: "Username" })).toHaveValue(customer.login);
  await issued.getByRole("button", { name: "Done" }).click();
  await expect(issued).toBeHidden();
});

test("start a library scan", async ({ page }) => {
  await page.goto("/libraries");
  await settled(page);
  await page.getByRole("button", { name: "Scan now" }).first().click();
  await expect(
    page.getByText(/Scan of .+ started|is already being scanned/u).first(),
  ).toBeVisible();
});

test("resolve a review with the keyboard", async ({ page }) => {
  await page.goto("/review");
  await settled(page);
  const choose = page.getByRole("button", { name: /^Choose/u }).first();
  await expect(choose).toBeVisible();
  // SPEC §8.3.7: 1–5 choose a candidate.
  await page.locator("main").focus();
  await page.keyboard.press("1");
  await expect(page.getByText(/^Matched to /u).first()).toBeVisible();
});

test("see a live session and stop it", async ({ page }) => {
  // An IPTV app plays a movie with the customer's login (Xtream play URL → 302).
  const tv = await request.newContext({ baseURL: env.tvUrl });
  const auth = `username=${encodeURIComponent(customer.login)}&password=${encodeURIComponent(customer.password)}`;
  const streams = await tv.get(`/player_api.php?${auth}&action=get_vod_streams`);
  expect(streams.ok()).toBe(true);
  const list = (await streams.json()) as { stream_id: number; container_extension: string }[];
  const movie = list[0];
  expect(movie, "a ready movie to play (make media-ready)").toBeDefined();
  if (movie === undefined) return;
  const play = await tv.get(
    `/movie/${encodeURIComponent(customer.login)}/${encodeURIComponent(customer.password)}/${String(movie.stream_id)}.${movie.container_extension}`,
    { maxRedirects: 0 },
  );
  expect(play.status()).toBe(302);
  await tv.dispose();

  await page.goto("/sessions");
  const row = page.getByRole("row").filter({ hasText: customer.name });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Stop" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Stop this session?" });
  await confirm.getByRole("button", { name: "Stop session" }).click();
  await expect(page.getByText(`Session of ${customer.name} stopped`)).toBeVisible();
  // The row fades while the edge lets go, then the live feed drops it.
  await expect(row).toHaveCount(0, { timeout: 60_000 });
});
