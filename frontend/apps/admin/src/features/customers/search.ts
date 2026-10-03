import { AccessStatus, CustomersListOrderingItem, UserStatus } from "@smart-iptv/api";
import { parseTableSearch } from "@smart-iptv/ui";

import { booleanParam, compact, enumParam, intParam, stringParam } from "../../lib/search";

/** "Expires within" choices of the customers list, in days. */
export const EXPIRING_WINDOWS = [7, 14, 30] as const;

const ORDERINGS = Object.values(CustomersListOrderingItem);

/** Customers list state in the URL (SPEC §8.2: shareable views). */
export function parseCustomersSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    access: enumParam(raw.access, Object.values(AccessStatus)),
    status: enumParam(raw.status, Object.values(UserStatus)),
    expiring: intParam(raw.expiring, EXPIRING_WINDOWS),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, ORDERINGS),
    /** Opens the create-customer wizard (a link target for "New customer"). */
    new: booleanParam(raw.new),
  });
}

export type CustomersSearch = ReturnType<typeof parseCustomersSearch>;

export const CUSTOMER_TABS = ["overview", "devices"] as const;
export type CustomerTab = (typeof CUSTOMER_TABS)[number];

export function parseCustomerSearch(raw: Record<string, unknown>) {
  return compact({ tab: enumParam(raw.tab, CUSTOMER_TABS) });
}
