import { compact, enumParam, intParam, stringParam } from "./lib/search-params";

/** /login: where to return after signing in, and why the customer is here. */
export function parseLoginSearch(raw: Record<string, unknown>) {
  return compact({
    redirect: stringParam(raw.redirect),
    expired: raw.expired === true || raw.expired === "true" ? true : undefined,
    reset: raw.reset === true || raw.reset === "true" ? true : undefined,
  });
}

/** /reset-password?uid=&token=[&welcome=1]: the link from a reset or invitation email. */
export function parseResetSearch(raw: Record<string, unknown>) {
  return compact({
    uid: stringParam(raw.uid),
    token: stringParam(raw.token),
    welcome: raw.welcome === 1 || raw.welcome === "1" || raw.welcome === true ? true : undefined,
  });
}

export const SORTS = ["added", "popular", "rating", "year", "title"] as const;
export type BrowseSort = (typeof SORTS)[number];

/** /movies and /series filters, kept in the URL so a filtered view can be shared. */
export function parseBrowseSearch(raw: Record<string, unknown>) {
  return compact({
    genre: stringParam(raw.genre),
    category: stringParam(raw.category),
    year: intParam(raw.year, 1870, 2200),
    sort: enumParam(raw.sort, SORTS),
  });
}
export type BrowseSearch = ReturnType<typeof parseBrowseSearch>;

export const SEARCH_TYPES = ["movie", "series", "episode"] as const;

export function parseSearchSearch(raw: Record<string, unknown>) {
  return compact({ q: stringParam(raw.q), type: enumParam(raw.type, SEARCH_TYPES) });
}

/** /series/$id?season=2 */
export function parseSeriesSearch(raw: Record<string, unknown>) {
  return compact({ season: intParam(raw.season, 0, 1000) });
}

/** /watch/series/$id?episode=<id>; ?restart starts at the top instead of resuming. */
export function parseWatchSearch(raw: Record<string, unknown>) {
  return compact({
    episode: stringParam(raw.episode),
    restart: raw.restart === true || raw.restart === "true" || raw.restart === 1 ? true : undefined,
  });
}

/** /account/subscription?checkout=done after a payment provider sends the customer back. */
export function parseSubscriptionSearch(raw: Record<string, unknown>) {
  return compact({ checkout: enumParam(raw.checkout, ["done", "cancelled"] as const) });
}
