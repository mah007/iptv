import type { Page, Response } from "@playwright/test";

import { expect, expectAccessible, inEnglish, login, password, signIn, test } from "./fixtures";

/*
 * Live TV, catch-up, a full plan and the password (ADR-0023). The customer of
 * `make e2e-portal` may watch one stream at a time and live TV; `make live-ready`
 * has the test channel live and recording (three minutes of catch-up at least).
 * These run after journey.e2e.ts, the password change last.
 */

const CHANNEL = "Test Pattern";
const START = "/api/v1/playback/start";

interface Channel {
  id: string;
  name: string;
  archive_from: string | null;
}

test.beforeAll(() => {
  if (!login || !password) {
    throw new Error("Set E2E_PORTAL_USER and E2E_PORTAL_PASS (make e2e-portal does).");
  }
});

function started(page: Page): Promise<Response> {
  return page.waitForResponse((response) => response.url().endsWith(START));
}

/** The video advances by more than `seconds` (pressing play if autoplay was refused). */
async function plays(page: Page, seconds: number): Promise<void> {
  const video = page.locator("video");
  await expect
    .poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState), {
      message: "the stream loads",
      timeout: 45_000,
    })
    .toBeGreaterThanOrEqual(2);
  if (await video.evaluate((element: HTMLVideoElement) => element.paused)) {
    await page.getByRole("button", { name: "Play", exact: true }).first().click();
  }
  const from = await video.evaluate((element: HTMLVideoElement) => element.currentTime);
  await expect
    .poll(
      () => video.evaluate((element: HTMLVideoElement, start) => element.currentTime - start, from),
      {
        message: "the video plays",
        timeout: 45_000,
      },
    )
    .toBeGreaterThan(seconds);
}

/** Back from the player; the session stops. */
async function leave(page: Page): Promise<void> {
  const stopped = page.waitForResponse((response) =>
    /\/api\/v1\/playback\/[^/]+\/stop$/u.test(response.url()),
  );
  await page.locator("video").hover();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  expect((await stopped).status()).toBe(204);
}

async function testChannel(page: Page): Promise<Channel> {
  const response = await page.request.get("/api/v1/live/channels", {
    headers: { "Accept-Language": "en" },
  });
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { channels: Channel[] };
  const channel = body.channels.find((item) => item.name === CHANNEL);
  if (!channel) throw new Error(`${CHANNEL} isn't listed: run make live-ready.`);
  return channel;
}

test("watch the live channel, then a recorded part of it", async ({ page }) => {
  await inEnglish(page);
  await signIn(page);

  await test.step("the Live page and the channel's guide", async () => {
    await page.getByRole("link", { name: "Live TV" }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Live TV" })).toBeVisible();
    const channels = page.getByRole("list", { name: "Channels" });
    await expect(channels.getByRole("heading", { name: CHANNEL })).toBeVisible();
    await expectAccessible(page, "live");
    await channels.getByRole("button", { name: `Guide for ${CHANNEL}` }).click();
    const guide = page.getByRole("dialog");
    await expect(guide.getByRole("list", { name: "Programmes" })).toBeVisible();
    await expect(guide.getByText("On now")).toBeVisible();
    await expectAccessible(page, "guide");
    await page.keyboard.press("Escape");
  });

  await test.step("watch live", async () => {
    const grant = started(page);
    await page.getByRole("link", { name: `Watch ${CHANNEL}` }).click();
    const response = await grant;
    expect(response.status()).toBe(200);
    expect(((await response.json()) as { live: boolean }).live).toBe(true);
    await plays(page, 5);
    // Live: a LIVE mark instead of the clock and the seek bar.
    await expect(page.getByText("Live", { exact: true })).toBeVisible();
    await expect(page.getByRole("slider", { name: "Seek" })).toHaveCount(0);
    await leave(page);
    await expect(page.getByRole("heading", { level: 1, name: "Live TV" })).toBeVisible();
  });

  await test.step("watch a recorded part from its start (catch-up)", async () => {
    const channel = await testChannel(page);
    expect(channel.archive_from, "the channel records").not.toBeNull();
    // Two minutes that the archive holds: from at most 150 s ago.
    const from = Math.max(Date.parse(channel.archive_from ?? "") + 5_000, Date.now() - 150_000);
    const start = new Date(Math.floor(from / 1000) * 1000).toISOString();
    const grant = started(page);
    await page.goto(`/watch/live/${channel.id}?start=${encodeURIComponent(start)}&duration=2`);
    const response = await grant;
    expect(response.status()).toBe(200);
    expect(((await response.json()) as { title_type: string }).title_type).toBe("catchup");
    await plays(page, 5);
    await expect(page.getByRole("slider", { name: "Seek" })).toHaveCount(1);
    await leave(page);
  });
});

test("a full plan offers to stop the stream playing elsewhere", async ({ page, browser }) => {
  // Another browser plays the live channel: the customer's one stream.
  const elsewhere = await browser.newContext({ locale: "en-GB" });
  try {
    const other = await elsewhere.newPage();
    await inEnglish(other);
    await signIn(other);
    const channel = await testChannel(other);
    const first = started(other);
    await other.goto(`/watch/live/${channel.id}`);
    expect((await first).status()).toBe(200);

    await inEnglish(page);
    await signIn(page);
    const refused = started(page);
    await page.goto(`/watch/live/${channel.id}`);
    expect((await refused).status()).toBe(409);
    await expect(page.getByRole("heading", { name: "Too many screens at once" })).toBeVisible();
    const streams = page.getByRole("list", { name: "Playing elsewhere" });
    await expect(streams.getByText(CHANNEL)).toBeVisible();
    await expectAccessible(page, "too many screens");
    const granted = started(page);
    await streams.getByRole("button", { name: "Stop it and play here" }).first().click();
    expect((await granted).status()).toBe(200);
    await plays(page, 3);
    await leave(page);
  } finally {
    await elsewhere.close();
  }
});

test("change my password, then back", async ({ page }) => {
  // A new password from parts of the generated one, never printed.
  const interim = `${password.slice(0, 16)}-Interim-${password.slice(16)}`;
  await inEnglish(page);
  await signIn(page);
  await page.goto("/account");

  const change = async (current: string, next: string) => {
    await page.getByLabel("Current password").fill(current);
    await page.getByLabel("New password", { exact: true }).fill(next);
    await page.getByLabel("New password again").fill(next);
    await page.getByRole("button", { name: "Change password" }).click();
  };

  await test.step("a wrong current password is refused", async () => {
    await change(`${password}-wrong`, interim);
    await expect(page.getByText("The current password is not right.")).toBeVisible();
    await expectAccessible(page, "password error");
  });

  await test.step("changed, then changed back", async () => {
    await change(password, interim);
    await expect(page.getByText("Password changed. Your other sign-ins have ended.")).toBeVisible();
    await page.reload();
    await change(interim, password);
    await expect(page.getByText("Password changed. Your other sign-ins have ended.")).toBeVisible();
  });
});
