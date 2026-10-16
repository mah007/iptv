import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { category, mediaSignedIn, movieDetail, movieSummary, review } from "../test/media-fixtures";
import { mockApi, type MockReply } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* Manual titles (ADR-0026): the review queue's "Create movie manually", reopening
 * skipped reviews, the Manual badge and filter, and a manual title's page. */

const QUEUE = "/api/v1/admin/review-queue";
const MANUAL = "/api/v1/admin/movies/manual";
const MOVIES = "/api/v1/admin/movies";

const DEFAULTS = {
  title: "Our Trip to AlUla",
  year: 2025,
  runtime_min: 91,
  rights_holder: "Studio Al Khobar",
  license_ref: "",
  license_expires_at: null,
  categories: ["cat-family"],
};

const MANUAL_MOVIE = movieDetail({
  id: "movie-9",
  tmdb_id: null,
  title: "Our Trip to AlUla",
  title_ar: "رحلتنا إلى العلا",
  metadata_source: "manual",
  synthetic: false,
  metadata_locked_fields: ["title", "title_ar", "categories"],
});

function lonely(overrides: Parameters<typeof review>[0] = {}) {
  return review({
    reason: "no_candidates",
    candidates: [],
    media_file: { ...review().media_file, relative_path: "Our Trip to AlUla (2025).mp4" },
    ...overrides,
  });
}

function conflict(code: string, message: string): MockReply {
  return {
    status: 409,
    body: {
      type: "urn:smart-iptv:problem:conflict",
      title: "Conflict",
      status: 409,
      code: "CONFLICT",
      detail: message,
      field_errors: { non_field_errors: [message] },
      field_error_codes: { non_field_errors: [code] },
    },
  };
}

const CATEGORIES = {
  "GET /api/v1/admin/categories": {
    body: page([
      category({ id: "cat-family", name_en: "Family", name_ar: "عائلي" }),
      category({ id: "cat-late", name_en: "Late night", name_ar: "ليلي", is_adult: true }),
    ]),
  },
};

describe("manual titles", () => {
  it("creates a movie by hand from a review with no candidates", async () => {
    const api = mockApi(
      mediaSignedIn({
        ...CATEGORIES,
        [`GET ${QUEUE}`]: { body: page([lonely()]) },
        [`GET ${MANUAL}/defaults`]: { body: DEFAULTS },
        [`POST ${MANUAL}`]: { status: 201, body: MANUAL_MOVIE },
      }),
    );
    const { user } = renderApp("/review");
    expect(await screen.findByText(/No provider knows this file/u)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Create movie manually" }));
    const sheet = await screen.findByRole("dialog", { name: "Create a movie manually" });
    expect(api.sent("GET", `${MANUAL}/defaults`)[0]?.query.get("review")).toBe("review-1");

    // Prefilled: the name's title and year, the file's runtime, the import's rights and
    // the library's category; adult categories are marked.
    expect(await within(sheet).findByDisplayValue("Our Trip to AlUla")).toBeTruthy();
    expect(within(sheet).getByLabelText(/^Runtime/u)).toHaveProperty("value", "91");
    expect(within(sheet).getByText("The file runs 91 min.")).toBeTruthy();
    expect(within(sheet).getByLabelText("Rights holder")).toHaveProperty(
      "value",
      "Studio Al Khobar",
    );
    expect(within(sheet).getByRole("checkbox", { name: "Family" })).toHaveProperty(
      "ariaChecked",
      "true",
    );
    expect(within(sheet).getByRole("checkbox", { name: /Late night\s*Adult/u })).toBeTruthy();

    // The licence reference and the rights confirmation are required.
    await user.click(within(sheet).getByRole("button", { name: "Create movie" }));
    expect(
      await within(sheet).findByText(
        "Enter the licence reference (your own, for content you own).",
      ),
    ).toBeTruthy();
    expect(within(sheet).getByText("Confirm the rights to create the title.")).toBeTruthy();
    expect(api.sent("POST", MANUAL)).toHaveLength(0);

    await user.type(within(sheet).getByLabelText("Title (Arabic)"), "رحلتنا إلى العلا");
    await user.type(within(sheet).getByLabelText("Licence reference"), "OWN-2026-001");
    await user.type(within(sheet).getByLabelText("Countries"), "sa, eg");
    expect(within(sheet).getByText(/Saudi Arabia/u)).toBeTruthy();
    const poster = new File([new Uint8Array([137, 80, 78, 71])], "poster.png", {
      type: "image/png",
    });
    fireEvent.change(within(sheet).getByLabelText("Poster"), { target: { files: [poster] } });
    expect(within(sheet).getByRole("img", { name: "Poster preview" })).toBeTruthy();
    await user.click(
      within(sheet).getByRole("checkbox", {
        name: /I own this content or hold the rights to distribute it/u,
      }),
    );
    await user.click(within(sheet).getByRole("button", { name: "Create movie" }));

    expect(await screen.findByText("Our Trip to AlUla created")).toBeTruthy();
    const form = api.sent("POST", MANUAL)[0]?.form;
    expect(form?.get("review")).toBe("review-1");
    expect(form?.get("title")).toBe("Our Trip to AlUla");
    expect(form?.get("title_ar")).toBe("رحلتنا إلى العلا");
    expect(form?.get("year")).toBe("2025");
    expect(form?.get("runtime_min")).toBe("91");
    expect(form?.getAll("countries")).toEqual(["SA", "EG"]);
    expect(form?.getAll("categories")).toEqual(["cat-family"]);
    expect(form?.get("rights_holder")).toBe("Studio Al Khobar");
    expect(form?.get("license_ref")).toBe("OWN-2026-001");
    expect(form?.get("rights_confirmed")).toBe("true");
    expect(form?.get("poster")).toBeInstanceOf(File);
    expect(form?.has("backdrop")).toBe(false);
    expect(form?.has("overview")).toBe(false); // left empty: not sent
  });

  it("shows the API's field errors in the admin's language", async () => {
    mockApi(
      mediaSignedIn({
        ...CATEGORIES,
        [`GET ${QUEUE}`]: { body: page([lonely()]) },
        [`GET ${MANUAL}/defaults`]: { body: { ...DEFAULTS, license_ref: "LIC-1" } },
        [`POST ${MANUAL}`]: {
          status: 400,
          body: {
            type: "urn:smart-iptv:problem:validation-error",
            title: "Invalid request",
            status: 400,
            code: "VALIDATION_ERROR",
            detail: "Invalid request",
            field_errors: {
              countries: ["Enter an ISO 3166-1 alpha-2 country code, e.g. SA."],
              poster: ["Use a JPEG, PNG, WebP, AVIF or GIF image."],
            },
            field_error_codes: { countries: ["invalid_country"], poster: ["unsupported_format"] },
          },
        },
      }),
    );
    const { user } = renderApp("/review", { language: "ar" });
    await user.click(await screen.findByRole("button", { name: "إنشاء فيلم يدويًا" }));
    const sheet = await screen.findByRole("dialog", { name: "إنشاء فيلم يدويًا" });
    await within(sheet).findByDisplayValue("Our Trip to AlUla");
    await user.click(within(sheet).getByRole("checkbox", { name: /أملك هذا المحتوى/u }));
    await user.click(within(sheet).getByRole("button", { name: "إنشاء الفيلم" }));
    expect(await within(sheet).findByText("أدخل رمز دولة من حرفين، مثل SA.")).toBeTruthy();
  });

  it("reopens a skipped review, and offers a manual movie for it", async () => {
    const skipped = review({
      status: "skipped",
      decided_by: "admin",
      decided_at: "2026-10-04T10:00:00Z",
    });
    const api = mockApi(
      mediaSignedIn({
        [`GET ${QUEUE}`]: { body: page([skipped]) },
        [`POST ${QUEUE}/review-1/reopen`]: { body: review() },
      }),
    );
    const { user } = renderApp("/review?status=skipped");
    expect(await screen.findByRole("button", { name: "Create movie manually" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Choose/u })).toBeNull(); // decided: no choosing
    await user.click(screen.getByRole("button", { name: "Reopen" }));
    expect(await screen.findByText("Review reopened")).toBeTruthy();
    expect(api.sent("POST", `${QUEUE}/review-1/reopen`)).toHaveLength(1);
  });

  it("explains a refused reopen in the admin's language", async () => {
    mockApi(
      mediaSignedIn({
        [`GET ${QUEUE}`]: { body: page([review({ status: "skipped" })]) },
        [`POST ${QUEUE}/review-1/reopen`]: conflict(
          "review_open_exists",
          "The file already has an open review.",
        ),
      }),
    );
    const { user } = renderApp("/review?status=skipped", { language: "ar" });
    await user.click(await screen.findByRole("button", { name: "إعادة الفتح" }));
    expect(await screen.findByText("للملف مراجعة مفتوحة بالفعل.")).toBeTruthy();
  });

  it("names the movie a resolved manual review made", async () => {
    const resolved = lonely({
      status: "resolved",
      chosen_provider: "manual",
      decided_by: "admin",
      decided_at: "2026-10-04T10:00:00Z",
      media_file: { ...lonely().media_file, movie: "movie-9" },
    });
    mockApi(mediaSignedIn({ [`GET ${QUEUE}`]: { body: page([resolved]) } }));
    renderApp("/review?status=resolved");
    expect(await screen.findByText(/Created manually by admin/u)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the movie" }).getAttribute("href")).toBe(
      "/movies/movie-9",
    );
    expect(screen.queryByRole("button", { name: "Create movie manually" })).toBeNull();
  });

  it("badges manual movies and filters the list by source", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}`]: {
          body: page([
            movieSummary({
              id: "movie-9",
              title: "Our Trip",
              metadata_source: "manual",
              synthetic: false,
            }),
          ]),
        },
      }),
    );
    renderApp("/movies?source=manual");
    const card = await screen.findByRole("link", { name: "Our Trip" });
    expect(within(card).getByText("Manual")).toBeTruthy();
    expect(api.sent("GET", MOVIES).at(-1)?.query.get("metadata_source")).toBe("manual");
    expect(await screen.findByText("Source: Manual")).toBeTruthy();
  });

  it("offers no refresh for a manual title, and matches it to TMDB without the automatic option", async () => {
    mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}/movie-9`]: { body: MANUAL_MOVIE },
        "GET /api/v1/admin/titles/movie-9/images": {
          body: { images: [], alternatives: [], alternatives_error: null },
        },
      }),
    );
    const { user } = renderApp("/movies/movie-9");
    expect(
      await screen.findByRole("heading", { level: 1, name: /Our Trip to AlUla/u }),
    ).toBeTruthy();
    expect(screen.getAllByText("Manual").length).toBeGreaterThan(0);
    expect(screen.getByText("All fields (entered by hand)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Refresh metadata" })).toBeNull();
    expect(
      await screen.findByText("No provider offers artwork for a manual title: upload your own."),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Match to TMDB" }));
    const dialog = await screen.findByRole("dialog", { name: "Match this manual title to TMDB" });
    expect(within(dialog).queryByRole("button", { name: "Match automatically" })).toBeNull();
  });

  it("edits genres, countries and the rights of any title", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${MOVIES}/movie-1`]: { body: movieDetail() },
        "GET /api/v1/admin/genres": {
          body: page([{ id: "genre-1", tmdb_id: 18, name_en: "Drama", name_ar: "دراما" }]),
        },
        [`PATCH ${MOVIES}/movie-1`]: { body: movieDetail() },
      }),
    );
    const { user } = renderApp("/movies/movie-1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit metadata" });
    await user.click(await within(sheet).findByRole("checkbox", { name: "Drama" }));
    const countries = within(sheet).getByLabelText("Countries");
    await user.clear(countries);
    await user.type(countries, "us au");
    await user.type(within(sheet).getByLabelText("Rights holder"), "Warner Bros.");
    fireEvent.change(within(sheet).getByLabelText("Licence ends"), {
      target: { value: "2030-12-31" },
    });
    await user.click(within(sheet).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Metadata saved")).toBeTruthy();
    expect(api.sent("PATCH", `${MOVIES}/movie-1`)[0]?.body).toEqual({
      genres: ["g-1", "genre-1"],
      countries: ["US", "AU"],
      rights_holder: "Warner Bros.",
      license_expires_at: "2030-12-31T23:59:59Z",
    });
  });
});
