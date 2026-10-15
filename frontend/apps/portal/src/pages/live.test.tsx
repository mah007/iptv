import type { LiveChannel, LiveListing, Programme } from "@smart-iptv/api-portal";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ACCESS, GRANT, ME } from "../test/fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";
import { catchupMinutes, elapsedRatio } from "./live";
import { liveRenewDelay } from "./watch";

// Shaka can't run in jsdom: the player is a stub that shows what it was given.
vi.mock("../features/player/video-player", () => ({
  VideoPlayer: ({
    grant,
    heading,
    subheading,
  }: {
    grant: { url: string; live: boolean };
    heading: string;
    subheading?: string;
  }) => (
    <div data-testid="player" data-url={grant.url} data-live={String(grant.live)}>
      {heading} {subheading}
    </div>
  ),
}));

const HOUR = 3_600_000;
const now = Date.now();

function programme(overrides: Partial<Programme> = {}): Programme {
  return {
    id: "p-now",
    title: "Morning News",
    description: "The day's stories.",
    start: new Date(now - HOUR / 4).toISOString(),
    stop: new Date(now + HOUR / 4).toISOString(),
    catchup: false,
    ...overrides,
  };
}

function channel(overrides: Partial<LiveChannel> = {}): LiveChannel {
  return {
    id: "ch-1",
    number: 101,
    name: "Showcase 24/7",
    logo_url: null,
    group_id: "g-news",
    catchup_days: 2,
    archive_from: new Date(now - 3 * HOUR).toISOString(),
    web_playable: true,
    now: programme(),
    next: programme({
      id: "p-next",
      title: "Weather",
      start: new Date(now + HOUR / 4).toISOString(),
      stop: new Date(now + HOUR / 2).toISOString(),
    }),
    ...overrides,
  };
}

const LISTING: LiveListing = {
  groups: [
    { id: "g-news", name: "News" },
    { id: "g-sport", name: "Sport" },
  ],
  channels: [
    channel(),
    channel({
      id: "ch-2",
      number: 202,
      name: "HEVC Sports",
      group_id: "g-sport",
      web_playable: false,
      catchup_days: 0,
      archive_from: null,
      now: null,
      next: null,
    }),
  ],
};

const RECORDED = programme({
  id: "p-old",
  title: "Late Show",
  start: new Date(now - 2 * HOUR).toISOString(),
  stop: new Date(now - HOUR).toISOString(),
  catchup: true,
});

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
  "GET /api/v1/live/channels": { body: LISTING },
};

describe("the Live page (ADR-0023)", () => {
  it("lists channels by group with what's on now and next", async () => {
    mockApi(SIGNED_IN);
    const { user, router } = renderApp("/live");
    const list = await screen.findByRole("list", { name: "Channels" });
    expect(within(list).getByRole("heading", { name: "Showcase 24/7" })).toBeTruthy();
    expect(within(list).getByText("Morning News")).toBeTruthy();
    expect(within(list).getByText("Weather")).toBeTruthy();
    expect(within(list).getByText("Catch-up: 2 days")).toBeTruthy();
    expect(
      within(list).getByRole("link", { name: "Watch Showcase 24/7" }).getAttribute("href"),
    ).toBe("/watch/live/ch-1");
    // A channel a browser can't decode: no Watch, a pointer to TV apps.
    expect(within(list).getByText("Plays in TV apps only")).toBeTruthy();
    expect(within(list).queryByRole("link", { name: "Watch HEVC Sports" })).toBeNull();
    expect(screen.getAllByRole("link", { name: "Live TV" }).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Sport" }));
    await waitFor(() => {
      expect(router.state.location.search).toEqual({ group: "g-sport" });
    });
    expect(within(list).queryByText("Showcase 24/7")).toBeNull();
    expect(screen.getByRole("button", { name: "Sport" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("opens a channel's guide with recorded programmes to watch from the start", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/live/channels/ch-1/guide": {
        body: { channel: channel(), programmes: [RECORDED, programme()] },
      },
    });
    const { user } = renderApp("/live");
    await user.click(await screen.findByRole("button", { name: "Guide for Showcase 24/7" }));
    const drawer = await screen.findByRole("dialog");
    const programmes = await within(drawer).findByRole("list", { name: "Programmes" });
    expect(within(programmes).getByText("Late Show")).toBeTruthy();
    expect(within(programmes).getByText("On now")).toBeTruthy();
    const replay = within(programmes).getByRole("link", {
      name: "Watch from the start: Late Show",
    });
    const href = new URL(replay.getAttribute("href") ?? "", "http://app.localhost");
    expect(href.pathname).toBe("/watch/live/ch-1");
    expect(href.searchParams.get("start")).toBe(RECORDED.start);
    expect(href.searchParams.get("duration")).toBe("60");
    expect(within(programmes).getByRole("link", { name: "Watch live" })).toBeTruthy();
    expect(api.sent("GET", "/api/v1/live/channels/ch-1/guide")).toHaveLength(1);
  });

  it("says when live TV isn't in the plan, and hides it from the menu", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me": { body: { ...ME, access: { ...ACCESS, allow_live: false } } },
    });
    renderApp("/live");
    expect(await screen.findByText("Live TV isn't in your plan")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Live TV" })).toBeNull();
  });
});

describe("watching live (ADR-0023)", () => {
  it("starts a live channel", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "POST /api/v1/playback/start": {
        body: { ...GRANT, title_type: "live", title_id: "ch-1", live: true, duration_ms: 0 },
      },
    });
    renderApp("/watch/live/ch-1");
    const player = await screen.findByTestId("player");
    expect(player.getAttribute("data-live")).toBe("true");
    await waitFor(() => {
      expect(player.textContent).toContain("Showcase 24/7");
    });
    expect(api.sent("POST", "/api/v1/playback/start")[0]?.body).toEqual({
      title_type: "live",
      title_id: "ch-1",
      prefer: "hls",
    });
  });

  it("starts a recorded programme from its start", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "POST /api/v1/playback/start": {
        body: { ...GRANT, title_type: "catchup", title_id: "ch-1", duration_ms: HOUR },
      },
    });
    const search = new URLSearchParams({ start: RECORDED.start, duration: "60" });
    renderApp(`/watch/live/ch-1?${search.toString()}`);
    expect(await screen.findByTestId("player")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/playback/start")[0]?.body).toEqual({
      title_type: "catchup",
      title_id: "ch-1",
      start: RECORDED.start,
      duration_min: 60,
      prefer: "hls",
    });
  });

  it("points to TV apps for a channel a browser can't play", async () => {
    const api = mockApi(SIGNED_IN);
    renderApp("/watch/live/ch-2");
    expect(
      await screen.findByRole("heading", { name: "This channel plays in TV apps" }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Manage devices" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/playback/start")).toHaveLength(0);
  });
});

describe("live helpers", () => {
  it("renews a live grant two minutes before it expires, never sooner than 30 s", () => {
    const at = Date.parse("2026-10-16T12:00:00Z");
    const grant = { ...GRANT, live: true, expires_at: "2026-10-16T18:00:00Z" };
    expect(liveRenewDelay(grant, at)).toBe(6 * HOUR - 120_000);
    expect(liveRenewDelay({ ...grant, expires_at: "2026-10-16T12:01:00Z" }, at)).toBe(30_000);
    expect(liveRenewDelay(GRANT, at)).toBeNull();
  });

  it("measures programmes", () => {
    expect(catchupMinutes(RECORDED)).toBe(60);
    expect(elapsedRatio(programme(), now)).toBeCloseTo(0.5, 2);
    expect(elapsedRatio(RECORDED, now)).toBe(1);
  });
});
