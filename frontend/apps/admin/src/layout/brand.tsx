import { useBrandingRetrieve } from "@smart-iptv/api";
import { cn, useTheme } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/**
 * The service's name and logo for this language and theme (Settings → Branding,
 * ADR-0019), from the public branding endpoint; the built-in name and mark until it
 * answers, and when no logo is set.
 */
export function useBrand(): { name: string; logo: string | null } {
  const { t, i18n } = useTranslation();
  const [theme] = useTheme();
  const query = useBrandingRetrieve({ query: { staleTime: 5 * 60_000, retry: false } });
  const data = query.data;
  const configured = i18n.language === "ar" ? data?.service_name.ar : data?.service_name.en;
  const name = configured !== undefined && configured !== "" ? configured : t("brand");
  const logo = (theme === "dark" ? data?.logo_dark_url : data?.logo_light_url) ?? null;
  return { name, logo };
}

/** The product mark: a play triangle on the brand accent (follows the runtime accent colour). */
export function BrandMark({ className }: { className?: string }) {
  const { logo } = useBrand();
  if (logo) {
    return (
      <img
        src={logo}
        alt=""
        className={cn("size-7 shrink-0 rounded-[7px] object-contain", className)}
      />
    );
  }
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

/** The admin's logo (or the mark) plus the service's name and the "Admin" area name. */
export function BrandLockup({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const { name, logo } = useBrand();
  return (
    <span className={cn("flex min-w-0 items-center gap-2.5", className)}>
      {logo ? (
        <img src={logo} alt="" className="h-7 max-w-28 shrink-0 object-contain" />
      ) : (
        <BrandMark />
      )}
      <span className={cn("grid min-w-0 leading-tight", compact && "sr-only")}>
        <span className="truncate text-sm font-semibold tracking-tight text-foreground">
          {name}
        </span>
        <span className="truncate text-xs text-muted-foreground">{t("area")}</span>
      </span>
    </span>
  );
}
