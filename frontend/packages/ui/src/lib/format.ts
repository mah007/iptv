import { useMemo } from "react";
import { useTranslation } from "react-i18next";

/** Admin dates are shown in Riyadh time unless the admin's profile says otherwise (SPEC §8.1). */
export const DEFAULT_TIME_ZONE = "Asia/Riyadh";

/**
 * BCP 47 locale for formatting. Always the Gregorian calendar and Latin digits:
 * plain `ar-SA` would default to the Umm al-Qura calendar and Arabic-Indic digits.
 */
export function formatLocale(language: string): string {
  return language === "ar" ? "ar-SA-u-ca-gregory-nu-latn" : "en-GB-u-ca-gregory-nu-latn";
}

export interface Formatters {
  locale: string;
  number: (value: number, options?: Intl.NumberFormatOptions) => string;
  percent: (ratio: number, options?: Intl.NumberFormatOptions) => string;
  dateTime: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
  date: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
}

export function createFormatters(language: string, timeZone = DEFAULT_TIME_ZONE): Formatters {
  const locale = formatLocale(language);
  return {
    locale,
    number: (value, options) => new Intl.NumberFormat(locale, options).format(value),
    percent: (ratio, options) =>
      new Intl.NumberFormat(locale, {
        style: "percent",
        maximumFractionDigits: 1,
        ...options,
      }).format(ratio),
    dateTime: (value, options) =>
      new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone,
        ...options,
      }).format(new Date(value)),
    date: (value, options) =>
      new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone, ...options }).format(
        new Date(value),
      ),
  };
}

/** Locale-aware number and date formatters for the current UI language. */
export function useFormatters(timeZone = DEFAULT_TIME_ZONE): Formatters {
  const { i18n } = useTranslation("ui");
  const language = i18n.language;
  return useMemo(() => createFormatters(language, timeZone), [language, timeZone]);
}
