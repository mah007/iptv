import { parseTableSearch } from "@smart-iptv/ui";

import { compact, dateParam, enumParam, stringParam } from "../../lib/search";

/** Audit log state in the URL: filters, date range and paging. */
export function parseAuditSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    action: stringParam(raw.action),
    target_type: stringParam(raw.target_type),
    target_id: stringParam(raw.target_id),
    from: dateParam(raw.from),
    to: dateParam(raw.to),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, ["at", "-at"] as const),
  });
}

export type AuditSearch = ReturnType<typeof parseAuditSearch>;
