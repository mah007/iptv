import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { category, mediaSignedIn } from "../test/media-fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const CATEGORIES = "/api/v1/admin/categories";
const VOD = [
  category({ id: "action", name_en: "Action", name_ar: "أكشن" }),
  category({ id: "drama", name_en: "Drama", name_ar: "دراما" }),
  category({ id: "kids", name_en: "Kids", name_ar: "أطفال" }),
];

describe("categories", () => {
  it("lists one kind at a time with both names", async () => {
    const api = mockApi(mediaSignedIn({ [`GET ${CATEGORIES}`]: { body: page(VOD) } }));
    const { user } = renderApp("/categories");
    const row = (await screen.findByText("Drama")).closest("tr");
    expect(row?.textContent).toContain("دراما");
    expect(api.sent("GET", CATEGORIES).at(-1)?.query.get("kind")).toBe("vod");
    await user.click(screen.getByRole("tab", { name: "Series" }));
    expect(api.sent("GET", CATEGORIES).at(-1)?.query.get("kind")).toBe("series");
  });

  it("moves a category down and sends the whole order", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${CATEGORIES}`]: { body: page(VOD) },
        [`POST ${CATEGORIES}/reorder`]: { body: VOD },
      }),
    );
    const { user } = renderApp("/categories");
    await user.click(await screen.findByRole("button", { name: "Move Action down" }));
    expect(api.sent("POST", `${CATEGORIES}/reorder`)[0]?.body).toEqual({
      kind: "vod",
      ids: ["drama", "action", "kids"],
    });
  });

  it("hides a category from IPTV apps", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${CATEGORIES}`]: { body: page(VOD) },
        [`PATCH ${CATEGORIES}/kids`]: { body: { ...VOD[2], visible_in_xtream: false } },
      }),
    );
    const { user } = renderApp("/categories");
    await user.click(await screen.findByRole("switch", { name: "Show Kids in IPTV apps" }));
    expect(await screen.findByText("Kids hidden from IPTV apps")).toBeTruthy();
    expect(api.sent("PATCH", `${CATEGORIES}/kids`)[0]?.body).toEqual({ visible_in_xtream: false });
  });

  it("creates a category, with the API's field errors inline", async () => {
    let attempts = 0;
    const api = mockApi(
      mediaSignedIn({
        [`GET ${CATEGORIES}`]: { body: page(VOD) },
        [`POST ${CATEGORIES}`]: () => {
          attempts += 1;
          return attempts === 1
            ? problem(400, "VALIDATION_ERROR", { slug: ["This slug is taken."] })
            : { status: 201, body: category({ id: "anime", name_en: "Anime" }) };
        },
      }),
    );
    const { user } = renderApp("/categories");
    await user.click(await screen.findByRole("button", { name: "New category" }));
    const dialog = await screen.findByRole("dialog", { name: "New category: Movies" });
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));
    expect(within(dialog).getAllByText("Enter a name.")).toHaveLength(2);

    await user.type(within(dialog).getByLabelText("English name"), "Anime");
    await user.type(within(dialog).getByLabelText("Arabic name"), "أنمي");
    await user.type(within(dialog).getByLabelText("Slug"), "kids");
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));
    expect(await within(dialog).findByText("This slug is taken.")).toBeTruthy();
    await user.clear(within(dialog).getByLabelText("Slug"));
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));
    expect(await screen.findByText("Anime created")).toBeTruthy();
    expect(api.sent("POST", CATEGORIES).at(-1)?.body).toEqual({
      kind: "vod",
      name_en: "Anime",
      name_ar: "أنمي",
      visible_in_xtream: true,
      is_adult: false,
    });
  });

  it("deletes a category after confirmation", async () => {
    const api = mockApi(
      mediaSignedIn({
        [`GET ${CATEGORIES}`]: { body: page(VOD) },
        [`DELETE ${CATEGORIES}/drama`]: { status: 204 },
      }),
    );
    const { user } = renderApp("/categories");
    await user.click(await screen.findByRole("button", { name: "Delete Drama" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Delete Drama?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete category" }));
    expect(await screen.findByText("Drama deleted")).toBeTruthy();
    expect(api.sent("DELETE", `${CATEGORIES}/drama`)).toHaveLength(1);
  });
});
