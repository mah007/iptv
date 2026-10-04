import { cn } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** The product mark: a play triangle on the brand accent. */
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

/** Mark plus the service name. */
export function BrandLockup({
  className,
  compactOnPhones = false,
}: {
  className?: string;
  /** Only the mark on narrow screens (the name stays for screen readers). */
  compactOnPhones?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      <BrandMark />
      <span
        className={cn(
          "truncate text-base font-semibold tracking-tight text-foreground",
          compactOnPhones && "max-sm:sr-only",
        )}
      >
        {t("brand")}
      </span>
    </span>
  );
}
