import { TitleStatus } from "@smart-iptv/api";
import { parseTableSearch } from "@smart-iptv/ui";

import { compact, enumParam, stringParam } from "../../lib/search";

export const TITLE_VIEWS = ["grid", "list"] as const;
export type TitleView = (typeof TITLE_VIEWS)[number];

/** The sort keys the title lists accept (`?ordering=`), ascending and descending. */
const ORDER_FIELDS = ["title", "year", "created_at", "updated_at", "rating", "popularity"];
const ORDERINGS = ORDER_FIELDS.flatMap((field) => [field, `-${field}`]);

/** Movies or series list state in the URL (SPEC §8.2: shareable views). */
export function parseTitlesSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    status: enumParam(raw.status, Object.values(TitleStatus)),
    library: stringParam(raw.library),
    category: stringParam(raw.category),
    view: enumParam(raw.view, TITLE_VIEWS),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, ORDERINGS),
  });
}

export type TitlesSearch = ReturnType<typeof parseTitlesSearch>;
