import { CategoryKind } from "@smart-iptv/api";

import { compact, enumParam } from "../../lib/search";

/** The categories page shows one kind at a time (VOD by default). */
export function parseCategoriesSearch(raw: Record<string, unknown>) {
  return compact({ kind: enumParam(raw.kind, Object.values(CategoryKind)) });
}
