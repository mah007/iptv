import type { PaginationState, SortingState } from "@tanstack/react-table";

/**
 * Table state in the URL (SPEC §8.2: filters, sorting and pagination are
 * shareable), in the admin API's own vocabulary: DRF page-number pagination
 * (`page` is 1-based, `page_size` at most 100) and `ordering=name,-created_at`.
 */
export interface TableSearch {
  page?: number | undefined;
  page_size?: number | undefined;
  ordering?: string | undefined;
}

export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;
export const PAGE_SIZE_OPTIONS: readonly number[] = [10, 25, 50, 100];

function positiveInt(value: unknown): number | undefined {
  const number = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof number === "number" && Number.isInteger(number) && number > 0 ? number : undefined;
}

/** Validate raw search params (e.g. in a route's `validateSearch`); junk is dropped. */
export function parseTableSearch(raw: Record<string, unknown>): TableSearch {
  const pageSize = positiveInt(raw.page_size);
  const ordering = typeof raw.ordering === "string" ? raw.ordering.trim() : "";
  return {
    page: positiveInt(raw.page),
    page_size: pageSize === undefined ? undefined : Math.min(pageSize, MAX_PAGE_SIZE),
    ordering: ordering === "" ? undefined : ordering,
  };
}

export function paginationFromSearch(
  search: TableSearch,
  defaultPageSize = DEFAULT_PAGE_SIZE,
): PaginationState {
  return {
    pageIndex: (search.page ?? 1) - 1,
    pageSize: search.page_size ?? defaultPageSize,
  };
}

/** Defaults are left out so clean URLs stay clean. */
export function paginationToSearch(
  pagination: PaginationState,
  defaultPageSize = DEFAULT_PAGE_SIZE,
): Pick<TableSearch, "page" | "page_size"> {
  return {
    page: pagination.pageIndex > 0 ? pagination.pageIndex + 1 : undefined,
    page_size: pagination.pageSize === defaultPageSize ? undefined : pagination.pageSize,
  };
}

/** `"name,-created_at"` → `[{ id: "name", desc: false }, { id: "created_at", desc: true }]`. */
export function sortingFromOrdering(ordering: string | undefined): SortingState {
  if (!ordering) return [];
  return ordering
    .split(",")
    .map((token) => token.trim())
    .map((token) =>
      token.startsWith("-") ? { id: token.slice(1), desc: true } : { id: token, desc: false },
    )
    .filter((sort) => sort.id !== "");
}

export function sortingToOrdering(sorting: SortingState): string | undefined {
  if (sorting.length === 0) return undefined;
  return sorting.map((sort) => (sort.desc ? `-${sort.id}` : sort.id)).join(",");
}
