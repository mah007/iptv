import { compact, enumParam } from "../../lib/search";

export const ACCESS_RULE_SCOPES = ["global", "customer"] as const;

/** Access rules page state in the URL: which rules are shown. */
export function parseAccessRulesSearch(raw: Record<string, unknown>) {
  return compact({ scope: enumParam(raw.scope, ACCESS_RULE_SCOPES) });
}
