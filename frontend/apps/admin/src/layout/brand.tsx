import { cn } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** The product mark: a play triangle on the brand accent (follows the runtime accent colour). */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden="true"
      className={cn("size-7 shrink-0 rounded-[7px] shadow-xs", className)}
    >
      <rect width="32" height="32" rx="7" className="fill-primary" />
      <path d="M12 9.5v13l11-6.5z" className="fill-primary-foreground" />
    </svg>
  );
}

/** Mark plus "Smart IPTV" and the "Admin" area name. */
export function BrandLockup({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span className={cn("flex min-w-0 items-center gap-2.5", className)}>
      <BrandMark />
      <span className={cn("grid min-w-0 leading-tight", compact && "sr-only")}>
        <span className="truncate text-sm font-semibold tracking-tight text-foreground">
          {t("brand")}
        </span>
        <span className="truncate text-xs text-muted-foreground">{t("area")}</span>
      </span>
    </span>
  );
}
