import { ReviewStatus } from "@smart-iptv/api";

import { compact, enumParam, intParam, stringParam } from "../../lib/search";

/** Why a file waits for a decision (`MatchReview.reason`); others show as sent. */
export const REVIEW_REASONS = [
  "ambiguous",
  "below_threshold",
  "no_candidates",
  "classification",
  "numbering",
  "episode_not_found",
] as const;

/** Review queue state in the URL: which list, which page, which item is open. */
export function parseReviewSearch(raw: Record<string, unknown>) {
  return compact({
    status: enumParam(raw.status, Object.values(ReviewStatus)),
    page: intParam(raw.page),
    item: stringParam(raw.item),
  });
}

export type ReviewSearch = ReturnType<typeof parseReviewSearch>;
