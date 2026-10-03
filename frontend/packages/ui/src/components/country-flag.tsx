import { Globe } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";

/** "sa" → "SA"; "UK" → "GB"; anything that isn't two letters → null. */
function normalizeCountry(code: string | null | undefined): string | null {
  const upper = (code ?? "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/u.test(upper)) return null;
  return upper === "UK" ? "GB" : upper;
}

/** Regional-indicator emoji for an ISO 3166-1 alpha-2 code, e.g. "SA" → 🇸🇦. */
export function countryFlagEmoji(code: string): string | null {
  const normalized = normalizeCountry(code);
  if (normalized === null) return null;
  return String.fromCodePoint(
    ...Array.from(normalized, (letter) => 0x1f1e6 + letter.charCodeAt(0) - 65),
  );
}

/** Localised country name, or null for codes that aren't assigned countries. */
export function countryName(code: string, locale: string): string | null {
  const normalized = normalizeCountry(code);
  // ZZ is CLDR's "Unknown Region"; GeoIP uses it (and XX) for unknown addresses.
  if (normalized === null || normalized === "ZZ") return null;
  try {
    return (
      new Intl.DisplayNames([locale], { type: "region", fallback: "none" }).of(normalized) ?? null
    );
  } catch {
    return null;
  }
}

export interface CountryFlagProps extends Omit<ComponentProps<"span">, "children"> {
  /** ISO 3166-1 alpha-2 code, e.g. "SA". Unknown or missing shows a globe. */
  code?: string | null | undefined;
  /** Print the country name next to the flag. */
  showName?: boolean;
}

/** Flag emoji with the country name as its label (or a globe when unknown). */
export function CountryFlag({ code, showName = false, className, ...props }: CountryFlagProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const name = code ? countryName(code, format.locale) : null;
  const flag = name !== null && code ? countryFlagEmoji(code) : null;
  const label = name ?? t("country.unknown");
  return (
    <span
      data-slot="country-flag"
      data-country={flag ? normalizeCountry(code) : undefined}
      title={showName ? undefined : label}
      className={cn("inline-flex min-w-0 items-center gap-1.5", className)}
      {...props}
    >
      <span
        {...(showName ? { "aria-hidden": true } : { role: "img", "aria-label": label })}
        className="inline-flex shrink-0 text-base leading-none"
      >
        {flag ?? <Globe aria-hidden="true" className="size-4 text-muted-foreground" />}
      </span>
      {showName ? <span className="truncate">{label}</span> : null}
    </span>
  );
}
