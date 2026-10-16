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
} from "../../../admin/e2e/visual/harness";
import { signIn } from "../fixtures";

/*
 * The portal's key pages (SPEC §15 visual regression): sign-in, home, a title, the
 * player's chrome, account and live TV, in English (light) and Arabic (dark, right to
 * left), at desktop width and at 390 px. Baselines: visual/baselines/portal-*.png.
 * Recording signs in as the end-to-end customer (make e2e-portal's); replays don't.
 */

const SUITE: Suite = { fixtures: path.resolve(import.meta.dirname, "fixtures") };
const MOVIE = "The Matrix";
/** The player reports progress on its own timer; replays answer what wasn't recorded. */
const PLAYER_REPORTS = /\/api\/v1\/playback\/[^/]+\/(progress|stop)$/u;

/** The Matrix's page (recording only: replays use the recorded path). */
function moviePage(page: Page): () => Promise<string> {
  return async () => {
    await page.goto("/movies");
    await settle(page);
    const href = await page.getByRole("link", { name: MOVIE }).first().getAttribute("href");
    if (href === null) throw new Error(`${MOVIE} isn't in the catalogue (make media-ready).`);
    return href;
  };
}

/** The player of The Matrix: its page's play link. */
function playerPage(page: Page): () => Promise<string> {
  return async () => {
    const title = await moviePage(page)();
    return title.replace(/^\/movies\//u, "/watch/movie/");
  };
}

/** The player at rest: the clip loaded, paused at the start, so the controls show. */
async function playerAtRest(page: Page): Promise<void> {
  const video = page.locator("video");
  await expect(video).toBeAttached();
  await expect
    .poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState), {
      message: "the clip loads",
      timeout: 30_000,
    })
    .toBeGreaterThanOrEqual(1);
  await video.evaluate(async (element: HTMLVideoElement) => {
    element.pause();
    if (element.currentTime !== 0) {
      const seeked = new Promise((done) => {
        element.addEventListener("seeked", done, { once: true });
      });
      element.currentTime = 0;
      await seeked;
    }
  });
}

const PAGES: readonly {
  name: string;
  path: string | ((page: Page) => () => Promise<string>);
  signedIn: boolean;
  ready?: (page: Page) => Promise<void>;
}[] = [
  { name: "sign-in", path: "/login", signedIn: false },
  { name: "home", path: "/", signedIn: true },
  { name: "title", path: moviePage, signedIn: true },
  { name: "player", path: playerPage, signedIn: true, ready: playerAtRest },
  { name: "account", path: "/account", signedIn: true },
  { name: "live", path: "/live", signedIn: true },
];

for (const spec of PAGES) {
  for (const variant of VARIANTS) {
    test(`${spec.name}, ${variant.label}`, async ({ page }) => {
      await page.setViewportSize({ width: variant.width, height: variant.height });
      await usePreferences(page, variant.language, variant.theme);
      if (RECORDING && spec.signedIn) await signIn(page);
      const replay = await useFixtures(
        page,
        SUITE,
        spec.name,
        typeof spec.path === "string" ? spec.path : spec.path(page),
        { reports: PLAYER_REPORTS },
      );
      await page.goto(replay.path);
      await expect(page.locator("html")).toHaveAttribute(
        "dir",
        variant.language === "ar" ? "rtl" : "ltr",
      );
      await expect(page.locator("main").first()).toBeVisible();
      if (spec.ready) await spec.ready(page);
      await settle(page);
      if (RECORDING) {
        replay.save();
        return;
      }
      expect(replay.missing, "requests without a fixture (make visual RECORD=1)").toEqual([]);
      await expectLooksSame(page, `portal-${spec.name}-${variant.label}`);
    });
  }
}
