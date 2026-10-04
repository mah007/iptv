import type { TFunction } from "i18next";

/** "Payment received": a notification event's name in the admin's language. */
export function eventLabel(t: TFunction, key: string): string {
  return t(`templates.events.${key}`, { defaultValue: key });
}

/** "per month", "per 3 months", "per 30 days", "per 1 month and 15 days". */
export function planDuration(t: TFunction, months: number, days: number): string {
  if (months > 0 && days > 0) return t("plans.duration.both", { months, days });
  if (months > 0) return t("plans.duration.months", { count: months });
  return t("plans.duration.days", { count: days });
}
