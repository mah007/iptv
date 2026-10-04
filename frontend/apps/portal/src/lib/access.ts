import type { CustomerMe, MySubscription } from "@smart-iptv/api-portal";

/**
 * Where the customer stands, for banners and the subscription page: the
 * governing subscription when there is one (ADR-0012 §5), else the access
 * profile an admin manages.
 */
export type AccessState =
  | { kind: "active"; endsAt: string | null }
  | { kind: "grace"; graceUntil: string | null }
  | { kind: "expired" }
  | { kind: "suspended" }
  | { kind: "none" };

export function accessState(
  me: CustomerMe | undefined,
  subscription: MySubscription | undefined,
  now: Date = new Date(),
): AccessState {
  if (me?.status === "suspended") return { kind: "suspended" };
  const current = subscription?.subscription;
  if (current) {
    switch (current.status) {
      case "active":
      case "pending":
        return { kind: "active", endsAt: current.ends_at };
      case "grace":
        return { kind: "grace", graceUntil: current.grace_until };
      case "suspended":
        return { kind: "suspended" };
      case "expired":
      case "cancelled":
        return subscription.pending ? { kind: "active", endsAt: null } : { kind: "expired" };
    }
  }
  const access = me?.access;
  if (!access) return { kind: "none" };
  if (access.status === "suspended" || access.status === "disabled") return { kind: "suspended" };
  if (access.status === "expired") return { kind: "expired" };
  if (access.expires_at !== null && new Date(access.expires_at) <= now) return { kind: "expired" };
  return { kind: "active", endsAt: access.expires_at };
}
