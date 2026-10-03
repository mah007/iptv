import { compact, stringParam } from "../../lib/search";

/** Where to return after signing in (checked again by `safeRedirect`). */
export function parseLoginSearch(raw: Record<string, unknown>) {
  return compact({ redirect: stringParam(raw.redirect) });
}
