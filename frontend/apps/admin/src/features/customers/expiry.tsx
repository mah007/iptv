import { RelativeTime, cn } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

import { daysUntil } from "../../lib/time";

/** Access ending within this many days is flagged red (SPEC §8.3.2). */
export const EXPIRING_SOON_DAYS = 7;

/**
 * When a customer's access ends: "in 12 days" with the exact time in a
 * tooltip, red when it is close, "No expiry" when it never ends.
 */
export function ExpiryText({
  expiresAt,
  className,
}: {
  expiresAt: string | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!expiresAt) {
    return (
      <span className={cn("text-muted-foreground", className)}>{t("customers.noExpiry")}</span>
    );
  }
  const days = daysUntil(expiresAt);
  return (
    <RelativeTime
      value={expiresAt}
      className={cn(
        days <= 0 && "text-muted-foreground",
        days > 0 && days <= EXPIRING_SOON_DAYS && "font-medium text-danger-text",
        className,
      )}
    />
  );
}
