import {
  DevicesListAppHint,
  DevicesListKind,
  DevicesListOrderingItem,
  DevicesListStatus,
} from "@smart-iptv/api";
import { parseTableSearch } from "@smart-iptv/ui";

import { compact, enumParam, intParam, stringParam } from "../../lib/search";

/** "Seen within" choices of the Devices page, in days. */
export const SEEN_WINDOWS = [1, 7, 30] as const;

/** The Devices page's state in the URL (SPEC §8.2: shareable views; ADR-0023). */
export function parseDevicesSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    kind: enumParam(raw.kind, Object.values(DevicesListKind)),
    app: enumParam(raw.app, Object.values(DevicesListAppHint)),
    status: enumParam(raw.status, Object.values(DevicesListStatus)),
    seen: intParam(raw.seen, SEEN_WINDOWS),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, Object.values(DevicesListOrderingItem)),
  });
}

export type DevicesSearch = ReturnType<typeof parseDevicesSearch>;
