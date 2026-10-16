import { useBrandingRetrieve, type Branding } from "@smart-iptv/api-portal";
import { cn, useTheme } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** The admin's branding (Settings → Branding, ADR-0019), shared by the portal's frames. */
export function useBranding(): Branding | undefined {
  return useBrandingRetrieve({ query: { staleTime: 5 * 60_000, retry: false } }).data;
}

/** The service's name and logo for this language and theme; the built-in ones without. */
export function useBrand(): { name: string; logo: string | null } {
  const { t, i18n } = useTranslation();
  const [theme] = useTheme();
  const data = useBranding();
  const configured = i18n.language === "ar" ? data?.service_name.ar : data?.service_name.en;
  const name = configured !== undefined && configured !== "" ? configured : t("brand");
  const logo = (theme === "dark" ? data?.logo_dark_url : data?.logo_light_url) ?? null;
  return { name, logo };
}

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

/** The logo (or the mark) plus the service name. */
export function BrandLockup({
  className,
  compactOnPhones = false,
}: {
  className?: string;
  /** Only the logo on narrow screens (the name stays for screen readers). */
  compactOnPhones?: boolean;
}) {
  const { name, logo } = useBrand();
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      {logo ? (
        <img src={logo} alt="" className="h-7 max-w-32 shrink-0 object-contain" />
      ) : (
        <BrandMark />
      )}
      <span
        className={cn(
          "truncate text-base font-semibold tracking-tight text-foreground",
          compactOnPhones && "max-sm:sr-only",
        )}
      >
        {name}
      </span>
    </span>
  );
}
