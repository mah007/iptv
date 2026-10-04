import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { candidate, mediaSignedIn, review } from "../test/media-fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const QUEUE = "/api/v1/admin/review-queue";

describe("review queue", () => {
  it("shows the file, and the candidates with their score breakdown", async () => {
    mockApi(mediaSignedIn({ [`GET ${QUEUE}`]: { body: page([review()]) } }));
    renderApp("/review");
    expect(await screen.findByRole("heading", { name: "The Matrix.mp4" })).toBeTruthy();
    const candidates = screen.getByRole("region", { name: "Candidates (2)" });
    expect(within(candidates).getByText("The Matrix Resurrections")).toBeTruthy();
    expect(within(candidates).getAllByRole("progressbar", { name: "Popularity" })).toHaveLength(2);
    expect(within(candidates).getByText("91%")).toBeTruthy();
  });

  it("resolves by choosing a candidate", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${QUEUE}`]: { body: page([review()]) },
        [`POST ${QUEUE}/review-1/resolve`]: { body: review({ status: "resolved" }) },
      }),
    );
    const { user } = renderApp("/review");
    const card = (await screen.findByText("The Matrix Resurrections")).closest("[data-slot=card]");
    if (!(card instanceof HTMLElement)) throw new Error("no candidate card");
    await user.click(within(card).getByRole("button", { name: "Choose" }));
    expect(await screen.findByText("Matched to The Matrix Resurrections")).toBeTruthy();
    expect(api.sent("POST", `${QUEUE}/review-1/resolve`)[0]?.body).toEqual({ tmdb_id: 624860 });
  });

  it("resolves with a TMDB id typed in, as another kind, or from a search", async () => {
    // Search results carry no score.
    const unscored = candidate({ id: 1396, kind: "tv", title: "Breaking Bad" });
    delete unscored.score;
    const api = mockApi(
      mediaSignedIn({
        [`GET ${QUEUE}`]: { body: page([review()]) },
        [`POST ${QUEUE}/review-1/resolve`]: { body: review({ status: "resolved" }) },
        "GET /api/v1/admin/metadata/search": {
          body: [unscored],
        },
      }),
    );
    const { user } = renderApp("/review");
    const id = await screen.findByLabelText("TMDB ID");
    await user.type(id, "603");
    await user.click(screen.getByRole("button", { name: "Match" }));
    expect(api.sent("POST", `${QUEUE}/review-1/resolve`)[0]?.body).toEqual({ tmdb_id: 603 });

    const query = await screen.findByLabelText("Search TMDB");
    await user.type(query, "Breaking");
    await user.click(screen.getByRole("button", { name: "Search" }));
    const found = (await screen.findByText("Breaking Bad")).closest("[data-slot=card]");
    expect(api.sent("GET", "/api/v1/admin/metadata/search")[0]?.query.get("query")).toBe(
      "Breaking",
    );
    await user.click(within(found as HTMLElement).getByRole("button", { name: "Choose" }));
    expect(api.sent("POST", `${QUEUE}/review-1/resolve`).at(-1)?.body).toEqual({
      tmdb_id: 1396,
      kind: "tv",
    });
  });

  it("skips a file", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${QUEUE}`]: { body: page([review()]) },
        [`POST ${QUEUE}/review-1/skip`]: { body: review({ status: "skipped" }) },
      }),
    );
    const { user } = renderApp("/review");
    await user.click(await screen.findByRole("button", { name: "Skip" }));
    expect(await screen.findByText("File skipped")).toBeTruthy();
    expect(api.sent("POST", `${QUEUE}/review-1/skip`)).toHaveLength(1);
  });

  it("offers no decisions without library.review", async () => {
    mockApi(
      mediaSignedIn({ [`GET ${QUEUE}`]: { body: page([review()]) } }, [
        "dashboard.view",
        "library.view",
      ]),
    );
    renderApp("/review");
    expect(await screen.findByRole("heading", { name: "The Matrix.mp4" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Choose" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  });
});
