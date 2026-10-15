import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GRANT, MATRIX_DETAIL, ME } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

// Shaka can't run in jsdom: the player is a stub that shows what it was given.
vi.mock("../features/player/video-player", () => ({
  VideoPlayer: ({ grant, heading }: { grant: { url: string }; heading: string }) => (
    <div data-testid="player" data-url={grant.url}>
      {heading}
    </div>
  ),
}));

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
};

const ELSEWHERE = {
  id: "s-1",
  title_kind: "movie",
  title_id: "inception",
  title_name: "Inception",
  device: { id: "d-9", name: "Chrome on Windows", kind: "web", app_hint: "other" },
  current: false,
  country: "SA",
  started_at: "2026-10-15T20:00:00Z",
  last_seen: "2026-10-15T20:10:00Z",
};

describe("the player (ADR-0023)", () => {
  it("offers to stop the other stream on a full plan, then plays here", async () => {
    let started = 0;
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": () => {
        started += 1;
        return started === 1 ? problem(409, "CONCURRENCY_LIMIT") : { body: GRANT };
      },
      "GET /api/v1/me/streams": { body: [ELSEWHERE] },
      "POST /api/v1/me/streams/s-1/stop": { status: 204 },
    });
    const { user } = renderApp("/watch/movie/matrix?restart=true");
    expect(await screen.findByRole("heading", { name: "Too many screens at once" })).toBeTruthy();
    expect(await screen.findByText("Inception")).toBeTruthy();
    expect(screen.getByText("Chrome on Windows")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Stop it and play here" }));
    expect(await screen.findByTestId("player")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/me/streams/s-1/stop")).toHaveLength(1);
  });

  it("says when a title being prepared should be ready", async () => {
    const preparing = problem(409, "TITLE_PREPARING");
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/movies/matrix": { body: MATRIX_DETAIL },
      "POST /api/v1/playback/start": {
        ...preparing,
        body: { ...(preparing.body as object), eta_s: 150 },
      },
    });
    renderApp("/watch/movie/matrix");
    expect(await screen.findByRole("heading", { name: "Getting it ready" })).toBeTruthy();
    expect(
      screen.getByText("It should be ready in about 3 minutes. We'll try again then."),
    ).toBeTruthy();
  });
});

describe("my password (ADR-0023)", () => {
  it("changes it with the current one and explains a wrong one", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "POST /api/v1/me/password": (request) =>
        (request.body as { current_password: string }).current_password === "right-one"
          ? { status: 204 }
          : {
              status: 400,
              body: {
                ...(problem(400, "VALIDATION_ERROR").body as object),
                field_errors: { current_password: ["The current password is not right."] },
                field_error_codes: { current_password: ["wrong_password"] },
              },
            },
    });
    const { user } = renderApp("/account");
    await user.type(await screen.findByLabelText("Current password"), "wrong-one");
    await user.type(screen.getByLabelText("New password"), "a-new-long-password");
    await user.type(screen.getByLabelText("New password again"), "a-new-long-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("The current password is not right.")).toBeTruthy();
    await user.clear(screen.getByLabelText("Current password"));
    await user.type(screen.getByLabelText("Current password"), "right-one");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(
      await screen.findByText("Password changed. Your other sign-ins have ended."),
    ).toBeTruthy();
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/me/password").at(-1)?.body).toEqual({
        current_password: "right-one",
        new_password: "a-new-long-password",
      });
    });
  });
});
