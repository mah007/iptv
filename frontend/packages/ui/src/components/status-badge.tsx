import { useTranslation } from "react-i18next";

import { Badge, type BadgeProps, type BadgeTone } from "./badge";

/**
 * Status colours used consistently across the admin (SPEC §8.1): Active
 * green, Grace amber, Expired zinc, Suspended red, Trial violet, Processing
 * blue, Review amber, Ready green, Hidden zinc. The rest follow the same
 * logic: healthy green, needs attention amber, failed or blocked red, in
 * progress blue, inert zinc.
 */
export const STATUS_TONES = {
  active: "success",
  grace: "warning",
  expired: "neutral",
  suspended: "danger",
  trial: "violet",
  processing: "info",
  review: "warning",
  ready: "success",
  hidden: "neutral",
  pending: "info",
  cancelled: "neutral",
  disabled: "neutral",
  license_expired: "danger",
  running: "info",
  queued: "neutral",
  failed: "danger",
  blocked: "danger",
  approved: "success",
  revoked: "neutral",
  open: "warning",
  resolved: "success",
  skipped: "neutral",
  sent: "success",
  done: "success",
  matched: "success",
  matching: "info",
  error: "danger",
  enabled: "success",
} as const satisfies Record<string, BadgeTone>;

export type KnownStatus = keyof typeof STATUS_TONES;

export function isKnownStatus(status: string): status is KnownStatus {
  return Object.hasOwn(STATUS_TONES, status);
}

export interface StatusBadgeProps extends Omit<BadgeProps, "tone" | "children" | "dot"> {
  /** API status value, e.g. `active` or `license_expired`. */
  status: string;
  /** Overrides the translated label (e.g. a count or a more specific wording). */
  label?: string;
}

/** Coloured status pill; unknown statuses render neutral with their raw value. */
export function StatusBadge({ status, label, ...props }: StatusBadgeProps) {
  const { t } = useTranslation("ui");
  const known = isKnownStatus(status);
  const tone: BadgeTone = known ? STATUS_TONES[status] : "neutral";
  const text = label ?? (known ? t(`status.${status}`) : status);
  return (
    <Badge tone={tone} dot data-status={status} {...props}>
      {text}
    </Badge>
  );
}
