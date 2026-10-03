import { parseTableSearch } from "@smart-iptv/ui";

import { compact, enumParam, stringParam } from "../../lib/search";

export const ADMIN_TABS = ["admins", "roles"] as const;
export type AdminTab = (typeof ADMIN_TABS)[number];

export function parseAdminsSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    tab: enumParam(raw.tab, ADMIN_TABS),
    q: stringParam(raw.q),
    page: table.page,
    page_size: table.page_size,
  });
}

export type AdminsSearch = ReturnType<typeof parseAdminsSearch>;
