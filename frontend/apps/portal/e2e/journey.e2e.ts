import AxeBuilder from "@axe-core/playwright";
import { type Page } from "@playwright/test";

import { expect, test } from "./fixtures";

/*
 * A customer's evening (SPEC §15): sign in, look around, search in Arabic,
 * open The Matrix, watch a few seconds, find it in Continue watching, sign
 * out. `make e2e-portal` creates the customer (manage.py e2e_portal_account)
 * with a fresh password in E2E_PORTAL_USER / E2E_PORTAL_PASS, with an empty
 * history, and needs the sample media ready (make media-ready).
 */

const login = process.env.E2E_PORTAL_USER ?? "";
const password = process.env.E2E_PORTAL_PASS ?? "";

/** WCAG 2.2 AA checks on what is on screen; the page must have none. */
async function expectAccessible(page: Page, name: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const problems = results.violations.map(
    (violation) => `${violation.id} (${String(violation.nodes.length)}): ${violation.help}`,
  );
  expect(problems, `accessibility problems on ${name}`).toEqual([]);
}

test.beforeAll(() => {
  if (!login || !password) {
    throw new Error("Set E2E_PORTAL_USER and E2E_PORTAL_PASS (make e2e-portal does).");
  }
});

test("sign in, search in Arabic, play The Matrix, continue watching, sign out", async ({
  page,
}) => {
  // An English evening: a first visit would otherwise speak the service's default
  // language (Settings → Branding, Arabic by default; ADR-0019).
  await page.addInitScript(() => {
    if (window.localStorage.getItem("smart-iptv.language") === null) {
      window.localStorage.setItem("smart-iptv.language", "en");
    }
  });
  await test.step("sign in", async () => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expectAccessible(page, "sign-in");
    await page.getByLabel("Username, email or phone").fill(login);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/$/u);
  });

  await test.step("home", async () => {
    await expect(page.getByRole("region", { name: "Featured" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Recently added" })).toBeVisible();
    // A fresh history: nothing to continue yet.
    await expect(page.getByRole("region", { name: "Continue watching" })).toHaveCount(0);
    await expectAccessible(page, "home");
  });

  await test.step("search in Arabic", async () => {
    await page.getByRole("link", { name: "Search" }).first().click();
    await page
      .getByRole("searchbox", { name: "Search movies, series and people" })
      .fill("المصفوفة");
    const movies = page.getByRole("region", { name: "Movies" });
    await expect(movies.getByRole("link", { name: "The Matrix" })).toBeVisible();
    await expectAccessible(page, "search");
    await movies.getByRole("link", { name: "The Matrix" }).click();
  });

  await test.step("title page", async () => {
    await expect(page.getByRole("heading", { level: 1, name: "The Matrix" })).toBeVisible();
    await expectAccessible(page, "title page");
  });

  await test.step("play a few seconds", async () => {
    const started = page.waitForResponse(
      (response) => response.url().endsWith("/api/v1/playback/start") && response.ok(),
    );
    await page.getByRole("link", { name: "Play", exact: true }).click();
    await started;
    const video = page.locator("video");
    await expect(video).toBeAttached();
    await expect
      .poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState), {
        message: "the stream loads",
        timeout: 45_000,
      })
      .toBeGreaterThanOrEqual(2);
    // Headless Chrome may refuse to autoplay with sound: then the viewer presses play.
    if (await video.evaluate((element: HTMLVideoElement) => element.paused)) {
      await page.getByRole("button", { name: "Play", exact: true }).first().click();
    }
    await expect
      .poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime), {
        message: "the video plays",
        timeout: 45_000,
      })
      .toBeGreaterThan(5);
    const progress = page.waitForResponse((response) =>
      /\/api\/v1\/playback\/[^/]+\/stop$/u.test(response.url()),
    );
    // The controls fade while playing; moving the pointer brings them back.
    await video.hover();
    await expectAccessible(page, "player");
    await page.getByRole("button", { name: "Back", exact: true }).click();
    expect((await progress).status()).toBe(204);
  });

  await test.step("continue watching", async () => {
    await expect(page.getByRole("heading", { level: 1, name: "The Matrix" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Resume" })).toBeVisible();
    await page.getByRole("link", { name: "Home" }).first().click();
    const row = page.getByRole("region", { name: "Continue watching" });
    await expect(row.getByRole("link", { name: /^Resume The Matrix/u })).toBeVisible();
    await expect(row.getByText(/ left$/u)).toBeVisible();
  });

  await test.step("sign out", async () => {
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.goto("/my-list");
    await expect(page).toHaveURL(/\/login\?redirect=/u);
  });
});

/** Signs in through the form (any language: the fields are found by their input purpose). */
async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.locator('input[autocomplete="username"]').fill(login);
  await page.locator('input[autocomplete="current-password"]').fill(password);
  await page.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/$/u);
}

for (const { language, theme } of [
  { language: "ar", theme: "light" },
  { language: "en", theme: "dark" },
] as const) {
  test(`every page is accessible in ${language}, ${theme}, at phone width`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(
      (preferences) => {
        window.localStorage.setItem("smart-iptv.language", preferences.language);
        window.localStorage.setItem("smart-iptv.theme", preferences.theme);
      },
      { language, theme },
    );
    await signIn(page);
    expect(await page.evaluate(() => document.documentElement.dir)).toBe(
      language === "ar" ? "rtl" : "ltr",
    );
    const pages = [
      "/",
      "/movies",
      "/series",
      "/search?q=matrix",
      "/my-list",
      "/history",
      "/account",
      "/account/devices",
      "/account/subscription",
      "/plans",
      "/about",
    ];
    for (const path of pages) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      // No sideways scrolling at 390 px.
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        `${path} fits the phone's width`,
      ).toBe(true);
      await expectAccessible(page, `${path} (${language}, ${theme})`);
    }
  });
}

test("adding a TV app asks for the password again once the confirmation is old", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("smart-iptv.language", "en");
  });
  await signIn(page);
  // Signing in counts as a confirmation (G-31): lock again, as if it were old.
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "csrftoken");
  const locked = await page.request.delete("/api/v1/auth/step-up", {
    headers: { "X-CSRFToken": csrf?.value ?? "" },
  });
  expect(locked.status()).toBe(204);

  await page.goto("/account/devices");
  await page.getByRole("button", { name: "Add TV app" }).first().click();
  const add = page.getByRole("dialog", { name: "Add a TV app" });
  const name = `E2E step-up ${Date.now().toString(36)}`;
  await add.getByLabel("Device name").fill(name);
  await add.getByRole("button", { name: "Create login" }).click();

  const confirm = page.getByRole("dialog", { name: "Confirm your password" });
  await expect(confirm).toBeVisible();
  await expectAccessible(page, "password confirmation");
  await confirm.getByLabel("Password", { exact: true }).fill(password);
  await confirm.getByRole("button", { name: "Confirm" }).click();

  const done = page.getByRole("dialog", { name: "Your TV app login" });
  await expect(done).toBeVisible();
  await done.getByRole("button", { name: "Done" }).click();

  // Leave the account as it was: the login is removed again (no confirmation needed).
  await page.getByRole("button", { name: `Actions for ${name}` }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page
    .getByRole("alertdialog", { name: `Remove ${name}?` })
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(page.getByText("Device removed")).toBeVisible();
});
