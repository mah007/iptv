import { compact, dateParam, enumParam, stringParam } from "../../lib/search";

export const LIVE_TABS = ["channels", "groups", "integrations"] as const;
export type LiveTab = (typeof LIVE_TABS)[number];

/** The Live TV page: a tab, and the channels' group filter and search. */
export function parseLiveSearch(raw: Record<string, unknown>) {
  return compact({
    tab: enumParam(raw.tab, LIVE_TABS),
    group: stringParam(raw.group),
    q: stringParam(raw.q),
  });
}

/** The EPG page: the channel whose programmes are previewed, and the day shown. */
export function parseEpgSearch(raw: Record<string, unknown>) {
  return compact({ channel: stringParam(raw.channel), day: dateParam(raw.day) });
}
