import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";

import {
  expect,
  test as base,
  type Locator,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

/*
 * Visual regression harness (SPEC §15, ADR-0024), shared by the admin and portal suites.
 *
 * Screenshots only compare when the pixels can't change between runs, so a page never
 * talks to the live API here:
 * - every `/api/v1/` request is answered from a fixture recorded once from the dev stack
 *   (`make visual RECORD=1`), keyed by method, path, sorted query and the language the
 *   app asked for; a request without a fixture fails the test, so drift shows up;
 * - the clock is fixed at the recording's time, so relative times and date ranges match
 *   what was recorded;
 * - images are one neutral placeholder, video is a two-second synthetic clip, server-sent
 *   event streams replay their first recorded events and then wait, and any other host
 *   is refused;
 * - animations are off, lazy rows are mounted and images loaded before the screenshot,
 *   regions that move on their own (relative times, live durations, avatars,
 *   `[data-visual-mask]`) are masked, and video frames are hidden.
 * Recording passes requests through to the stack and writes what came back, with media
 * URLs rewritten so no signed token is ever committed.
 */

export const RECORDING = process.env.VISUAL_RECORD === "1";

declare global {
  interface Window {
    __cspViolations?: string[];
  }
}

interface RecordedResponse {
  status: number;
  contentType: string;
  /** Parsed JSON for JSON responses (readable diffs), the raw text otherwise. */
  body: unknown;
}

interface PageFixture {
  /** The path the page was recorded at (detail pages resolve theirs while recording). */
  path: string;
  responses: Record<string, RecordedResponse>;
}

/** Where a suite keeps its fixtures, baselines and the recording's clock. */
export interface Suite {
  fixtures: string;
}

const PLACEHOLDER_IMAGE = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">' +
    '<rect width="300" height="450" fill="#5b6475"/>' +
    '<path d="M0 450 L300 0 L300 450 Z" fill="#7a8396"/></svg>',
);
const SAMPLE_VIDEO = path.resolve(import.meta.dirname, "media/sample.mp4");
const SILENT_VTT = "WEBVTT\n\n";
const CSRF_PATH = "/api/v1/auth/csrf";
/** Media URLs in recorded fixtures point here (no signed token is committed). */
export const MEDIA_FIXTURE_SEGMENT = "/v/visual-fixture/";

/** What the screenshots hide: regions that change by themselves, not by a regression. */
const MASKED = [
  "[data-slot='relative-time']",
  "[data-slot='live-duration']",
  "[data-slot='avatar']",
  "[data-visual-mask]",
].join(", ");

function readJson(file: string): unknown {
  if (!existsSync(file)) return null;
  return JSON.parse(readFileSync(file, "utf8")) as unknown;
}

function writeJson(file: string, value: unknown): void {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

/**
 * The fixed "now" of a suite: the moment its fixtures were recorded. A recording run
 * starts from an empty fixtures folder (the Makefile clears it) and writes it first.
 */
export function frozenNow(suite: Suite): Date {
  const file = path.join(suite.fixtures, "clock.json");
  const saved = readJson(file) as { now: string } | null;
  if (saved !== null) return new Date(saved.now);
  if (!RECORDING) {
    throw new Error(`No ${file}: record the fixtures first (make visual RECORD=1).`);
  }
  const now = new Date(Math.floor(Date.now() / 60_000) * 60_000);
  writeJson(file, { now: now.toISOString() });
  return now;
}

function isEventStream(request: Request): boolean {
  return (request.headers().accept ?? "").includes("text/event-stream");
}

/** Replayed streams wait a day before reconnecting: the page keeps what it was sent. */
const STREAM_RETRY = "retry: 86400000\n\n";
/** How long a recording listens to an event stream for its first events. */
const STREAM_LISTEN_MS = 3_000;

/** A replayed event stream: the recorded events, then the connection closes. */
async function replayStream(route: Route, recorded: RecordedResponse | undefined): Promise<void> {
  const events = typeof recorded?.body === "string" ? recorded.body : "";
  await route.fulfill({
    status: 200,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
    body: `${STREAM_RETRY}${events}`,
  });
}

/**
 * The first events of a live stream (the snapshot pages wait for), read beside the
 * browser with its cookies: the browser's own request would never finish.
 */
async function listenToStream(request: Request): Promise<RecordedResponse> {
  const url = new URL(request.url());
  const headers = { ...(await request.allHeaders()), host: url.host };
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    const call = http.request(
      {
        host: "127.0.0.1",
        port: url.port || 80,
        path: `${url.pathname}${url.search}`,
        method: "GET",
        headers,
      },
      (response) => {
        const done = () => {
          const text = Buffer.concat(chunks).toString("utf8");
          // Keep whole events only; comments (keep-alives) and the retry hint are dropped.
          const events = text
            .split("\n\n")
            .filter((block) => block.split("\n").some((line) => line.startsWith("data:")))
            .map((block) => `${block}\n\n`)
            .join("");
          resolve({
            status: response.statusCode ?? 0,
            contentType: "text/event-stream",
            body: events,
          });
        };
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", done);
        setTimeout(() => {
          call.destroy();
          done();
        }, STREAM_LISTEN_MS);
      },
    );
    call.on("error", (error) => {
      if (chunks.length === 0) reject(error);
    });
    call.end();
  });
}

async function keyOf(request: Request): Promise<string> {
  const url = new URL(request.url());
  const query = new URLSearchParams(
    [...url.searchParams.entries()].sort(([a, av], [b, bv]) =>
      a === b ? av.localeCompare(bv) : a.localeCompare(b),
    ),
  ).toString();
  const language = (await request.allHeaders())["accept-language"]?.split(/[,;-]/u)[0] ?? "";
  return `${request.method()} ${url.pathname}${query ? `?${query}` : ""}${language ? ` [${language}]` : ""}`;
}

/** Signed media URLs (`/v/{token}/…`) become fixture URLs, keeping host and file name. */
function withoutTokens(value: unknown): unknown {
  if (typeof value === "string") {
    return value.replace(/\/v\/[^/?#\s"]+\//gu, MEDIA_FIXTURE_SEGMENT);
  }
  if (Array.isArray(value)) return value.map(withoutTokens);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        withoutTokens(item),
      ]),
    );
  }
  return value;
}

/**
 * A playback grant replays as progressive MP4 on the fixture clip: the recorded stream
 * (HLS segments, thumbnails) isn't kept, and the player's chrome looks the same.
 */
function asFixtureGrant(body: unknown): unknown {
  if (body === null || typeof body !== "object" || !("url" in body)) return body;
  const grant = body as Record<string, unknown>;
  const url = typeof grant.url === "string" ? new URL(grant.url) : null;
  const clip = url ? `${url.origin}${MEDIA_FIXTURE_SEGMENT}sample.mp4` : null;
  return {
    ...grant,
    delivery: "mp4",
    url: clip,
    compat_url: clip,
    hls_master_url: null,
    thumbnails_url: null,
  };
}

function recordedBody(request: Request, contentType: string, text: string): unknown {
  if (!contentType.includes("json") || text === "") return text;
  const parsed = JSON.parse(text) as unknown;
  const body = new URL(request.url()).pathname.endsWith("/playback/start")
    ? asFixtureGrant(parsed)
    : parsed;
  return withoutTokens(body);
}

function isAppAsset(url: URL, origin: string): boolean {
  // The SPA's own files (Vite modules, fonts, icons) come from its dev server.
  return (
    url.origin === origin &&
    !url.pathname.startsWith("/api/") &&
    !url.pathname.startsWith("/media/")
  );
}

export interface Replay {
  /** The path to visit: as recorded, or what `resolve` finds while recording. */
  path: string;
  /** Requests the fixtures couldn't answer (must be empty). */
  missing: string[];
  /** Saves the fixture after a recording (a no-op when replaying). */
  save: () => void;
}

/**
 * Serve the page's API from its fixture (or record it). Call before the first `goto`.
 * `resolve` finds the path while recording (for example the first row of a list).
 */
export async function useFixtures(
  page: Page,
  suite: Suite,
  name: string,
  resolve: string | (() => Promise<string>),
  { reports }: { reports?: RegExp } = {},
): Promise<Replay> {
  const file = path.join(suite.fixtures, `${name}.json`);
  await page.clock.setFixedTime(frozenNow(suite));
  const missing: string[] = [];

  if (RECORDING) {
    const fixture = (readJson(file) as PageFixture | null) ?? { path: "", responses: {} };
    await page.route("**/api/v1/**", async (route, request) => {
      if (isEventStream(request)) {
        const recorded = await listenToStream(request);
        fixture.responses[await keyOf(request)] = recorded;
        await replayStream(route, recorded);
        return;
      }
      const response = await route.fetch();
      const contentType = response.headers()["content-type"] ?? "";
      const text = await response.text();
      fixture.responses[await keyOf(request)] = {
        status: response.status(),
        contentType,
        body: recordedBody(request, contentType, text),
      };
      await route.fulfill({ response, body: text });
    });
    // Every variant of a page shows the same item, even if a list's order moved meanwhile.
    const target = fixture.path || (typeof resolve === "string" ? resolve : await resolve());
    fixture.path = target;
    return {
      path: target,
      missing,
      save: () => {
        writeJson(file, fixture);
      },
    };
  }

  const fixture = readJson(file) as PageFixture | null;
  if (fixture === null) {
    throw new Error(`No fixture ${file}: record it first (make visual RECORD=1).`);
  }
  await page.route("**/*", async (route, request) => {
    const url = new URL(request.url());
    const pageOrigin = new URL(page.url() === "about:blank" ? request.url() : page.url()).origin;
    if (url.pathname.startsWith("/api/v1/") && url.origin === pageOrigin) {
      const key = await keyOf(request);
      const recorded = fixture.responses[key];
      if (isEventStream(request)) {
        if (recorded === undefined) missing.push(key);
        await replayStream(route, recorded);
        return;
      }
      if (recorded === undefined && url.pathname === CSRF_PATH) {
        // Recordings ran signed in, with the cookie: a replay is handed one the same way.
        await route.fulfill({
          status: 204,
          headers: { "Set-Cookie": "csrftoken=visual-fixture; Path=/; SameSite=Lax" },
        });
        return;
      }
      if (recorded === undefined) {
        // Reports the page sends on its own timer (a player's progress) may come or not.
        if (reports?.test(url.pathname) === true) {
          await route.fulfill({ status: 204 });
          return;
        }
        missing.push(key);
        await route.fulfill({ status: 599, body: "" });
        return;
      }
      const status = recorded.status;
      if (status === 204 || status === 304) {
        await route.fulfill({ status });
        return;
      }
      await route.fulfill({
        status,
        contentType: recorded.contentType,
        body: typeof recorded.body === "string" ? recorded.body : JSON.stringify(recorded.body),
      });
      return;
    }
    if (url.pathname.includes(MEDIA_FIXTURE_SEGMENT)) {
      const headers = {
        "Access-Control-Allow-Origin": pageOrigin,
        "Access-Control-Allow-Credentials": "true",
      };
      if (url.pathname.endsWith(".vtt")) {
        await route.fulfill({ status: 200, headers, contentType: "text/vtt", body: SILENT_VTT });
      } else {
        await route.fulfill({ status: 200, headers, contentType: "video/mp4", path: SAMPLE_VIDEO });
      }
      return;
    }
    if (request.resourceType() === "image" && !isAppAsset(url, pageOrigin)) {
      await route.fulfill({ status: 200, contentType: "image/svg+xml", body: PLACEHOLDER_IMAGE });
      return;
    }
    if (isAppAsset(url, pageOrigin) || url.protocol === "data:" || url.protocol === "blob:") {
      await route.continue();
      return;
    }
    missing.push(`${request.method()} ${url.origin}${url.pathname} (another host)`);
    await route.abort("blockedbyclient");
  });
  return { path: fixture.path, missing, save: () => undefined };
}

/** Everything mounted and loaded, fonts too, no skeleton or busy region left, nothing fetching. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  // Rows mounted on approach (the portal's LazyMount) all mount: scroll to the end once.
  for (let step = 0; step < 50; step += 1) {
    const atEnd = await page.evaluate(() => {
      window.scrollBy(0, window.innerHeight);
      return window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 1;
    });
    await page.waitForLoadState("networkidle");
    if (atEnd) break;
  }
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  await page.evaluate(async () => {
    await document.fonts.ready;
    // Lazy images below the fold would load (or not) while the full page is captured.
    const images = Array.from(document.images);
    for (const image of images) image.loading = "eager";
    await Promise.all(
      images
        .filter((image) => !image.complete)
        .map(
          (image) =>
            new Promise((done) => {
              image.addEventListener("load", done, { once: true });
              image.addEventListener("error", done, { once: true });
            }),
        ),
    );
  });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-slot='skeleton'], [aria-busy='true']")).toHaveCount(0, {
    timeout: 20_000,
  });
  // Contrast and hover states at rest, not under the pointer of the last click.
  await page.mouse.move(0, 0);
}

/**
 * The page against its baseline, dynamic regions masked. Long pages (settings: over
 * 20,000 px) compare their first screen only (`fullPage: false`), to keep baselines small.
 */
export async function expectLooksSame(
  page: Page,
  name: string,
  { fullPage = true, masks = [] }: { fullPage?: boolean; masks?: Locator[] } = {},
): Promise<void> {
  // Video frames are hidden rather than masked: a mask would cover the player's controls,
  // which sit on top of the video. (CSSOM, so the strict CSP allows it.)
  await page.evaluate(() => {
    for (const video of Array.from(document.querySelectorAll("video"))) {
      video.style.visibility = "hidden";
    }
  });
  await expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage,
    animations: "disabled",
    caret: "hide",
    mask: [page.locator(MASKED), ...masks],
    maskColor: "#808080",
  });
}

export type Language = "en" | "ar";
export type Theme = "light" | "dark";

/** The variants SPEC §15 asks for: both themes and both directions, desktop and phone. */
export const VARIANTS: readonly {
  language: Language;
  theme: Theme;
  width: number;
  height: number;
  label: string;
}[] = [
  { language: "en", theme: "light", width: 1440, height: 900, label: "en-light-desktop" },
  { language: "en", theme: "light", width: 390, height: 844, label: "en-light-390" },
  { language: "ar", theme: "dark", width: 1440, height: 900, label: "ar-dark-desktop" },
  { language: "ar", theme: "dark", width: 390, height: 844, label: "ar-dark-390" },
];

/** Language and theme before the app starts (both apps keep them in localStorage). */
export async function usePreferences(page: Page, language: Language, theme: Theme) {
  await page.addInitScript(
    ([lang, mode]) => {
      window.localStorage.setItem("smart-iptv.language", lang);
      window.localStorage.setItem("smart-iptv.theme", mode);
    },
    [language, theme] as const,
  );
}

/**
 * Every visual test's page fails on a Content-Security-Policy violation (ADR-0021), as
 * the E2E suites do, and on a request its fixture couldn't answer.
 */
export const test = base.extend({
  page: async ({ page }, provide) => {
    await page.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__cspViolations?.push(
          `${event.effectiveDirective} blocked ${event.blockedURI || "inline"}`,
        );
      });
    });
    const fromConsole: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && message.text().includes("Content Security Policy")) {
        fromConsole.push(message.text());
      }
    });
    await provide(page);
    const fromPage = page.isClosed()
      ? []
      : await page.evaluate(() => window.__cspViolations ?? []).catch(() => []);
    expect([...fromPage, ...fromConsole], "Content-Security-Policy violations").toEqual([]);
  },
});

export { expect };
