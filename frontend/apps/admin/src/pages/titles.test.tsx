import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import {
  category,
  library,
  mediaSignedIn,
  movieDetail,
  movieSummary,
  seriesDetail,
} from "../test/media-fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const MOVIES = "/api/v1/admin/movies";

describe("movies and series", () => {
  it("shows posters with a synthetic badge, and filters by status and library in the URL", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}`]: { body: page([movieSummary()]) },
        "GET /api/v1/admin/libraries": { body: page([library()]) },
      }),
    );
    const { router, user } = renderApp("/movies?status=ready&library=lib-1&q=matrix");
    const card = await screen.findByRole("link", { name: "The Matrix" });
    expect(card.getAttribute("href")).toBe("/movies/movie-1");
    expect(within(card).getByText("Synthetic")).toBeTruthy();
    const sent = api.sent("GET", MOVIES).at(-1);
    expect(sent?.query.get("status")).toBe("ready");
    expect(sent?.query.get("library")).toBe("lib-1");
    expect(sent?.query.get("search")).toBe("matrix");
    expect(await screen.findByText("Library: Movies")).toBeTruthy();

    await (
      await import("@testing-library/user-event")
    ).default
      .setup()
      .click(screen.getByRole("button", { name: "Clear all filters" }));
    await waitFor(() => {
      expect(router.state.location.search).toEqual({});
    });
  });

  it("lists series in the list view with the Arabic title under the English one", async () => {
    mockApi(
      mediaSignedIn({
        "GET /api/v1/admin/series": {
          body: page([
            {
              ...movieSummary({ id: "series-1", title: "Breaking Bad", title_ar: "بريكنج باد" }),
              episode_count: 7,
            },
          ]),
        },
      }),
    );
    renderApp("/series?view=list");
    const table = await screen.findByRole("table", { name: "Series" });
    const row = (await within(table).findByRole("link", { name: "Breaking Bad" })).closest("tr");
    expect(row?.textContent).toContain("بريكنج باد");
    expect(row?.textContent).toContain("7");
  });

  it("shows a movie's artwork, both overviews and its files by library-relative path", async () => {
    mockApi(mediaSignedIn({ [`GET ${MOVIES}/movie-1`]: { body: movieDetail() } }));
    renderApp("/movies/movie-1");
    expect(await screen.findByRole("heading", { level: 1, name: /The Matrix/ })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Poster of The Matrix" })).toBeTruthy();
    expect(screen.getByText("مخترق يكتشف الحقيقة.").getAttribute("dir")).toBe("rtl");
    expect(screen.getByText("The.Matrix.1999.1080p.BluRay.x265.mkv")).toBeTruthy();
    expect(screen.getAllByText("Synthetic").length).toBeGreaterThan(0);
    expect(screen.getByText("Offline fixtures")).toBeTruthy();
    expect(document.body.textContent).not.toContain("/media/");
  });

  it("edits only the fields the admin changed, and queues a metadata refresh", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}/movie-1`]: { body: movieDetail() },
        "GET /api/v1/admin/categories": {
          body: page([
            category({ id: "cat-1", name_en: "Action" }),
            category({ id: "cat-2", name_en: "Drama", name_ar: "دراما" }),
          ]),
        },
        [`PATCH ${MOVIES}/movie-1`]: (request) => ({
          body: movieDetail({
            ...(request.body as object),
            categories: [],
            metadata_locked_fields: ["overview_ar", "categories"],
          }),
        }),
        [`POST ${MOVIES}/movie-1/refresh-metadata`]: { status: 202, body: { queued: true } },
      }),
    );
    const { user } = renderApp("/movies/movie-1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit metadata" });
    const overview = within(sheet).getByLabelText("Overview (Arabic)");
    await user.clear(overview);
    await user.type(overview, "ملخص جديد");
    await user.click(await within(sheet).findByRole("checkbox", { name: "Drama" }));
    await user.click(within(sheet).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Metadata saved")).toBeTruthy();
    expect(api.sent("PATCH", `${MOVIES}/movie-1`)[0]?.body).toEqual({
      overview_ar: "ملخص جديد",
      categories: ["cat-1", "cat-2"],
    });
    expect(await screen.findByText("overview_ar")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Refresh metadata" }));
    expect(await screen.findByText("Metadata refresh queued")).toBeTruthy();
  });

  it("hides a title from customers", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}/movie-1`]: { body: movieDetail() },
        [`PATCH ${MOVIES}/movie-1`]: { body: movieDetail({ status: "hidden" }) },
      }),
    );
    const { user } = renderApp("/movies/movie-1");
    await user.click(await screen.findByRole("button", { name: "Hide" }));
    expect(await screen.findByText("Title hidden from customers")).toBeTruthy();
    expect(api.sent("PATCH", `${MOVIES}/movie-1`)[0]?.body).toEqual({ status: "hidden" });
    expect(await screen.findByRole("button", { name: "Show" })).toBeTruthy();
  });

  it("shows a series' seasons with each episode's file or its absence", async () => {
    mockApi(mediaSignedIn({ "GET /api/v1/admin/series/series-1": { body: seriesDetail() } }));
    renderApp("/series/series-1");
    expect(await screen.findByText("1 of 2 episodes on disk")).toBeTruthy();
    expect(screen.getByText("Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv")).toBeTruthy();
    const missing = screen.getByText("Cat's in the Bag...").closest("tr");
    expect(missing?.textContent).toContain("No file");
  });
});
