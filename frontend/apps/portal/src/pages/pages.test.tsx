import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { device, first, GRANT, MATRIX, MATRIX_DETAIL, ME, PLAN } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

// Shaka can't run in jsdom: the player is a stub that shows what it was given.
vi.mock("../features/player/video-player", () => ({
  VideoPlayer: ({
    grant,
    heading,
    startAt,
  }: {
    grant: { url: string };
    heading: string;
    startAt: number;
  }) => (
    <div data-testid="player" data-url={grant.url} data-start={startAt}>
      {heading}
    </div>
  ),
}));

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
};

describe("search", () => {
  it("searches as you type, in Arabic, and groups the results", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/search": (request) => ({
        body: {
          query: request.query.get("q"),
          engine: "meilisearch",
          page: 1,
          page_size: 40,
          total: 1,
          results: [{ type: "movie", title: MATRIX, episode: null }],
          people: [{ id: "p2", name: "Keanu Reeves", profile: null }],
        },
      }),
    });
    const { user, router } = renderApp("/search");
    expect(await screen.findByText("Find something to watch")).toBeTruthy();

    await user.type(
      screen.getByRole("searchbox", { name: "Search movies, series and people" }),
      "المصفوفة",
    );

    const movies = await screen.findByRole("region", { name: "Movies" });
    expect(within(movies).getByRole("link", { name: "The Matrix" }).getAttribute("href")).toBe(
      "/movies/matrix",
    );
    expect(screen.getByRole("link", { name: /Keanu Reeves/u }).getAttribute("href")).toBe(
      "/people/p2",
    );
    // Debounced: one request for the whole word, not one per letter.
    expect(api.sent("GET", "/api/v1/search").map((request) => request.query.get("q"))).toEqual([
      "المصفوفة",
    ]);
    await waitFor(() => {
      expect(router.state.location.search).toEqual({ q: "المصفوفة" });
    });
  });

  it("says when nothing matches", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/search": {
        body: {
          query: "zzz",
          engine: "database",
          page: 1,
          page_size: 40,
          total: 0,
          results: [],
          people: [],
        },
      },
    });
    renderApp("/search?q=zzz");
    expect(await screen.findByText("No results for “zzz”")).toBeTruthy();
  });
});

describe("title page", () => {
  it("shows the details, cast and similar titles, and toggles My List", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/favorites": {
        status: 201,
        body: { title_type: "movie", title_id: "matrix", added: true },
      },
      "GET /api/v1/favorites": { body: [MATRIX] },
      "GET /api/v1/home": { body: { hero: [], continue_watching: [], rows: [] } },
    });
    const { user } = renderApp("/movies/matrix");

    expect(await screen.findByRole("heading", { level: 1, name: "The Matrix" })).toBeTruthy();
    expect(screen.getByText("Welcome to the real world.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Play" }).getAttribute("href")).toBe(
      "/watch/movie/matrix",
    );
    expect(screen.getByRole("link", { name: /Keanu Reeves/u }).getAttribute("href")).toBe(
      "/people/p2",
    );
    expect(screen.getByText("Arabic")).toBeTruthy();
    expect(screen.getByRole("region", { name: "More like this" })).toBeTruthy();

    const myList = screen.getByRole("button", { name: "My List" });
    expect(myList.getAttribute("aria-pressed")).toBe("false");
    await user.click(myList);
    expect(myList.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/favorites")[0]?.body).toEqual({
        title_type: "movie",
        title_id: "matrix",
      });
    });
  });

  it("offers Resume and Start over when the movie is half watched", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": {
        body: {
          ...MATRIX_DETAIL,
          viewer: {
            favorite: true,
            rating: "up",
            progress: {
              position_ms: 60_000,
              duration_ms: 120_000,
              completed: false,
              updated_at: "2026-10-04T10:00:00Z",
            },
          },
        },
      },
    });
    renderApp("/movies/matrix");
    expect(await screen.findByRole("link", { name: "Resume" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start over" }).getAttribute("href")).toBe(
      "/watch/movie/matrix?restart=true",
    );
    expect(screen.getByRole("button", { name: "I like this" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });
});

describe("player page", () => {
  it("starts HLS playback and hands the stream to the player", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": { body: GRANT },
    });
    renderApp("/watch/movie/matrix");
    const player = await screen.findByTestId("player");
    expect(player.dataset.url).toBe(GRANT.url);
    expect(player.dataset.start).toBe("0");
    expect(api.sent("POST", "/api/v1/playback/start")[0]?.body).toEqual({
      title_type: "movie",
      title_id: "matrix",
      prefer: "hls",
    });
  });

  it("asks whether to resume", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": { body: { ...GRANT, resume_ms: 75_000 } },
    });
    const { user } = renderApp("/watch/movie/matrix");
    await user.click(await screen.findByRole("button", { name: "Resume from 1:15" }));
    expect((await screen.findByTestId("player")).dataset.start).toBe("75");
  });

  it("explains a full plan and links to the devices", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": problem(409, "CONCURRENCY_LIMIT"),
    });
    renderApp("/watch/movie/matrix");
    expect(await screen.findByRole("heading", { name: "Too many screens at once" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Manage devices" }).getAttribute("href")).toBe(
      "/account/devices",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("sends an expired subscription to renew", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": problem(403, "SUBSCRIPTION_EXPIRED"),
    });
    renderApp("/watch/movie/matrix");
    expect(
      await screen.findByRole("heading", { name: "Your subscription has ended" }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Renew subscription" }).getAttribute("href")).toBe(
      "/plans",
    );
  });

  it("says a title is still being prepared", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": problem(409, "TITLE_PREPARING"),
    });
    renderApp("/watch/movie/matrix");
    expect(await screen.findByRole("heading", { name: "Getting it ready" })).toBeTruthy();
  });
});

describe("devices", () => {
  it("adds a TV app and shows the login once, with a QR code", async () => {
    const created = device({ id: "d2", name: "Bedroom TV", xtream_username: "bedroom-tv" });
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me/devices": {
        body: [
          device({ id: "d1", name: "Living room" }),
          device({
            id: "w1",
            name: "Chrome on Linux",
            kind: "web",
            current: true,
            xtream_username: null,
          }),
        ],
      },
      "POST /api/v1/me/devices": {
        status: 201,
        body: {
          device: created,
          server_url: "http://tv.localhost",
          username: "bedroom-tv",
          password: "s3cret-pass-word",
        },
      },
    });
    const { user } = renderApp("/account/devices");

    const tvApps = await screen.findByRole("region", { name: "TV apps (IPTV logins)" });
    expect(within(tvApps).getByText("Living room")).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Browsers and apps" })).getByText("This device"),
    ).toBeTruthy();

    await user.click(first(screen.getAllByRole("button", { name: "Add TV app" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a TV app" });
    await user.type(within(dialog).getByLabelText("Device name"), "Bedroom TV");
    await user.click(within(dialog).getByRole("button", { name: "Create login" }));

    const done = await screen.findByRole("dialog", { name: "Your TV app login" });
    expect(api.sent("POST", "/api/v1/me/devices")[0]?.body).toEqual({
      name: "Bedroom TV",
      app_hint: "smarters",
    });
    expect(within(done).getByDisplayValue("bedroom-tv")).toBeTruthy();
    expect(within(done).queryByText("s3cret-pass-word")).toBeNull();
    expect(
      within(done).getByRole("img", { name: "QR code with the server, username and password" }),
    ).toBeTruthy();
    expect(within(done).getByRole("heading", { name: "Set up IPTV Smarters" })).toBeTruthy();
  });

  it("checks chosen credentials before sending them", async () => {
    const api = mockApi({ ...SIGNED_IN, "GET /api/v1/me/devices": { body: [] } });
    const { user } = renderApp("/account/devices");
    await user.click(first(await screen.findAllByRole("button", { name: "Add TV app" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a TV app" });
    await user.click(within(dialog).getByRole("radio", { name: /Choose my own/u }));
    await user.type(within(dialog).getByLabelText("Username"), "x");
    await user.type(within(dialog).getByLabelText("Password"), "short");
    await user.click(within(dialog).getByRole("button", { name: "Create login" }));
    expect(
      await within(dialog).findByText("Use 3 to 32 letters, digits, dots, dashes or underscores."),
    ).toBeTruthy();
    expect(within(dialog).getByText("Use 8 to 64 characters.")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/me/devices")).toHaveLength(0);
  });
});

describe("plans and checkout", () => {
  it("shows manual payment instructions with the reference", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/plans": { body: [PLAN] },
      "GET /api/v1/payment-providers": {
        body: [{ code: "manual", name: "Bank transfer or cash" }],
      },
      "POST /api/v1/checkout": {
        body: {
          kind: "manual",
          provider: "manual",
          invoice: null,
          redirect_url: null,
          reference: "SIP-12345",
          instructions: { en: "Transfer to IBAN SA00 0000.", ar: "حوّل إلى الآيبان SA00 0000." },
          subscription: null,
        },
      },
    });
    const { user } = renderApp("/plans");
    expect(await screen.findByText("29.00 SAR")).toBeTruthy();
    expect(screen.getByText("for 1 month")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Choose" }));
    await user.click(await screen.findByRole("button", { name: "Bank transfer or cash" }));
    expect(await screen.findByText("Transfer to IBAN SA00 0000.")).toBeTruthy();
    expect(screen.getByDisplayValue("SIP-12345")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/checkout")[0]?.body).toEqual({
      plan_id: "plan-1",
      provider: "manual",
    });
  });
});

describe("password links", () => {
  it("sets the password from an invitation and returns to sign in", async () => {
    const api = mockApi({
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
      "GET /api/v1/auth/csrf": { status: 204 },
      "POST /api/v1/auth/password/reset": { status: 204 },
    });
    const { user, router } = renderApp("/reset-password?uid=MQ&token=abc-123&welcome=1");
    expect(await screen.findByRole("heading", { name: "Welcome! Set your password" })).toBeTruthy();
    await user.type(screen.getByLabelText("New password"), "a long new password");
    await user.type(screen.getByLabelText("Repeat the password"), "a long new password");
    await user.click(screen.getByRole("button", { name: "Set password and continue" }));
    expect(await screen.findByText("Your password is set. Sign in with it.")).toBeTruthy();
    expect(router.state.location.pathname).toBe("/login");
    expect(api.sent("POST", "/api/v1/auth/password/reset")[0]?.body).toEqual({
      uid: "MQ",
      token: "abc-123",
      password: "a long new password",
    });
  });

  it("explains an expired link", async () => {
    mockApi({
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
      "POST /api/v1/auth/password/reset": {
        status: 400,
        body: {
          type: "urn:x",
          title: "Invalid",
          status: 400,
          code: "VALIDATION_ERROR",
          detail: "This link is invalid or has expired.",
          field_errors: { token: ["This link is invalid or has expired."] },
          field_error_codes: { token: ["invalid_token"] },
        },
      },
    });
    const { user } = renderApp("/reset-password?uid=MQ&token=old");
    await user.type(await screen.findByLabelText("New password"), "a long new password");
    await user.type(screen.getByLabelText("Repeat the password"), "a long new password");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("heading", { name: "This link doesn't work" })).toBeTruthy();
  });
});
