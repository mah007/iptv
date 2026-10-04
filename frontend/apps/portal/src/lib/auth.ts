import { getMeRetrieveQueryOptions, isApiError, type CustomerMe } from "@smart-iptv/api-portal";
import { useQuery } from "@tanstack/react-query";

/**
 * The signed-in customer (`GET /api/v1/me`). The route guard loads it before any
 * page of the signed-in area renders, so inside it the query is always cached.
 */
export function meQueryOptions() {
  return getMeRetrieveQueryOptions({
    query: {
      staleTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: false,
      // The route guard handles "not signed in" for this query itself.
      meta: { signInCheck: true },
    },
  });
}

/** The signed-in customer; undefined only outside the signed-in area. */
export function useMe(): CustomerMe | undefined {
  return useQuery(meQueryOptions()).data;
}

/** The request failed because nobody is signed in: the session expired or was ended. */
export function isSignedOutError(error: unknown): boolean {
  return isApiError(error) && error.code === "NOT_AUTHENTICATED";
}

/** Pages that work without a session; a 401 there is not "your session ended". */
const PUBLIC_PATHS = ["/login", "/forgot-password", "/reset-password"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

/**
 * Where to go after signing in: only a path on this site, so a crafted link
 * can't send a customer elsewhere ("//evil.example", "/\evil.example" and
 * "https://…" are refused; browsers read a backslash as a slash).
 */
export function safeRedirect(target: string | undefined): string {
  if (target === undefined || !/^\/(?![/\\])/u.test(target)) return "/";
  if (isPublicPath(target.split("?")[0] ?? "")) return "/";
  return target;
}
