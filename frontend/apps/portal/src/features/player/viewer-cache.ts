import type { QueryClient } from "@tanstack/react-query";

/** Queries that show where the viewer is: home rows, continue watching, history, title pages. */
const VIEWER_PREFIXES = [
  "/api/v1/home",
  "/api/v1/continue-watching",
  "/api/v1/watch-history",
  "/api/v1/movies/",
  "/api/v1/series/",
  "/api/v1/episodes/",
  "/api/v1/recommendations",
];

/** After playback stops, everything that shows progress is refetched on its next use. */
export function invalidateViewerState(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const head = query.queryKey[0];
      return typeof head === "string" && VIEWER_PREFIXES.some((prefix) => head.startsWith(prefix));
    },
  });
}
