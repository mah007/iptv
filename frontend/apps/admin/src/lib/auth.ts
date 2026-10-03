import { getAuthMeQueryOptions, isApiError, type Me } from "@smart-iptv/api";
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";

/**
 * The signed-in admin (`GET /api/v1/auth/me`). The app guard loads it before
 * any page renders, so inside the shell it is always in the cache. It changes
 * only when someone edits this admin, so it is not refetched on every focus.
 */
export function meQueryOptions() {
  return getAuthMeQueryOptions({
    query: {
      staleTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: false,
      // The route guard handles "not signed in" for this query itself.
      meta: { signInCheck: true },
    },
  });
}

/** The signed-in admin; undefined only outside the signed-in shell. */
export function useMe(): Me | undefined {
  return useQuery(meQueryOptions()).data;
}

/** A permission code, or several of which any one is enough. */
export type PermissionRequirement = string | readonly string[];

export function hasPermission(me: Me | undefined, requirement: PermissionRequirement): boolean {
  if (me === undefined) return false;
  const codes = typeof requirement === "string" ? [requirement] : requirement;
  return codes.some((code) => me.permissions.includes(code));
}

/**
 * What the UI may offer the admin. The API checks every request again, so
 * this only hides actions that would be refused.
 */
export function useCan(): (requirement: PermissionRequirement) => boolean {
  const me = useMe();
  return useCallback((requirement) => hasPermission(me, requirement), [me]);
}

/**
 * The request failed because nobody is signed in: the session expired (30
 * minutes idle), was signed out elsewhere, or only finished the password step.
 */
export function isSignedOutError(error: unknown): boolean {
  return (
    isApiError(error) &&
    (error.code === "NOT_AUTHENTICATED" ||
      error.code === "MFA_REQUIRED" ||
      error.code === "MFA_SETUP_REQUIRED")
  );
}

/**
 * Where to go after signing in: only a path on this site, so a crafted link
 * can't send an admin elsewhere ("//evil.example", "/\evil.example" and
 * "https://…" are refused; browsers read a backslash as a slash).
 */
export function safeRedirect(target: string | undefined): string {
  if (target === undefined || !/^\/(?![/\\])/u.test(target)) return "/";
  if (target.startsWith("/login")) return "/";
  return target;
}
