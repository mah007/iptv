import { describe, expect, it } from "vitest";

import {
  paginationFromSearch,
  paginationToSearch,
  parseTableSearch,
  sortingFromOrdering,
  sortingToOrdering,
} from "./table-search";

describe("parseTableSearch", () => {
  it("keeps valid values and accepts numeric strings", () => {
    expect(parseTableSearch({ page: "3", page_size: 50, ordering: " -created_at " })).toEqual({
      page: 3,
      page_size: 50,
      ordering: "-created_at",
    });
  });

  it("drops junk instead of failing", () => {
    expect(parseTableSearch({ page: "zero", page_size: -5, ordering: "" })).toEqual({
      page: undefined,
      page_size: undefined,
      ordering: undefined,
    });
    expect(parseTableSearch({ page: 1.5, page_size: "abc", ordering: 7 })).toEqual({
      page: undefined,
      page_size: undefined,
      ordering: undefined,
    });
  });

  it("caps the page size at the API maximum", () => {
    expect(parseTableSearch({ page_size: 5000 }).page_size).toBe(100);
  });
});

describe("pagination <-> search", () => {
  it("maps 1-based pages to 0-based indexes", () => {
    expect(paginationFromSearch({ page: 2, page_size: 50 })).toEqual({
      pageIndex: 1,
      pageSize: 50,
    });
    expect(paginationFromSearch({})).toEqual({ pageIndex: 0, pageSize: 25 });
  });

  it("leaves defaults out of the URL", () => {
    expect(paginationToSearch({ pageIndex: 0, pageSize: 25 })).toEqual({
      page: undefined,
      page_size: undefined,
    });
    expect(paginationToSearch({ pageIndex: 3, pageSize: 100 })).toEqual({
      page: 4,
      page_size: 100,
    });
  });

  it("round-trips", () => {
    const pagination = { pageIndex: 4, pageSize: 10 };
    expect(paginationFromSearch(paginationToSearch(pagination))).toEqual(pagination);
  });
});

describe("sorting <-> DRF ordering", () => {
  it("parses ascending and descending fields", () => {
    expect(sortingFromOrdering("name,-created_at")).toEqual([
      { id: "name", desc: false },
      { id: "created_at", desc: true },
    ]);
    expect(sortingFromOrdering(undefined)).toEqual([]);
    expect(sortingFromOrdering(" , -")).toEqual([]);
  });

  it("serialises back, leaving no sort out of the URL", () => {
    expect(sortingToOrdering([{ id: "ends_at", desc: true }])).toBe("-ends_at");
    expect(sortingToOrdering([])).toBeUndefined();
  });
});
