import { useFormatters, type Formatters } from "@smart-iptv/ui";

import { useMe } from "./auth";

/** Numbers and dates in the UI language, with dates in the customer's own time zone. */
export function useCustomerFormatters(): Formatters {
  const me = useMe();
  return useFormatters(me?.timezone ?? undefined);
}
