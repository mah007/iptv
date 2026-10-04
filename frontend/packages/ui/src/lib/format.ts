import { useMemo } from "react";
import { useTranslation } from "react-i18next";

/** Admin dates are shown in Riyadh time unless the admin's profile says otherwise (SPEC §8.1). */
export const DEFAULT_TIME_ZONE = "Asia/Riyadh";

/** Latin digits by default (SPEC §8.1); `native` gives Arabic-Indic digits in Arabic. */
export type Numerals = "latn" | "native";

/**
 * BCP 47 locale for formatting. Always the Gregorian calendar and, by default,
 * Latin digits: plain `ar-SA` would default to the Umm al-Qura calendar and
 * Arabic-Indic digits.
 */
export function formatLocale(language: string, numerals: Numerals = "latn"): string {
  if (language.split("-")[0]?.toLowerCase() === "ar") {
    return `ar-SA-u-ca-gregory-nu-${numerals === "native" ? "arab" : "latn"}`;
  }
  return "en-GB-u-ca-gregory-nu-latn";
}

// Intl formatters are costly to build and tables format thousands of cells.
const numberFormats = new Map<string, Intl.NumberFormat>();

function numberFormat(locale: string, options: Intl.NumberFormatOptions = {}): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let format = numberFormats.get(key);
  if (format === undefined) {
    format = new Intl.NumberFormat(locale, options);
    numberFormats.set(key, format);
  }
  return format;
}

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

const BYTE_UNITS = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte", "petabyte"] as const;

function bytesIn(locale: string, bytes: number): string {
  let value = Math.max(0, finite(bytes));
  let unit = 0;
  // Decimal (SI) units, as storage vendors and macOS report them; Intl has no GiB.
  while (value >= 1000 && unit < BYTE_UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return numberFormat(locale, {
    style: "unit",
    unit: BYTE_UNITS[unit],
    unitDisplay: "short",
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value);
}

function bitrateIn(locale: string, bitsPerSecond: number): string {
  // One fixed decimal keeps a column of bitrates aligned (tabular numerals).
  return numberFormat(locale, {
    style: "unit",
    unit: "megabit-per-second",
    unitDisplay: "short",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Math.max(0, finite(bitsPerSecond)) / 1_000_000);
}

/** `clock` gives "1:12:05"; `short` gives "1 hr 12 mins" (the two largest units). */
export type DurationStyle = "clock" | "short";

function durationIn(locale: string, seconds: number, style: DurationStyle): string {
  const total = Math.max(0, Math.floor(finite(seconds)));
  const parts = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60] as const;
  const [hours, minutes, secs] = parts;
  if (style === "clock") {
    const pad = numberFormat(locale, { minimumIntegerDigits: 2, useGrouping: false });
    const lead = numberFormat(locale, { useGrouping: false });
    return hours > 0
      ? `${lead.format(hours)}:${pad.format(minutes)}:${pad.format(secs)}`
      : `${lead.format(minutes)}:${pad.format(secs)}`;
  }
  const units = ["hour", "minute", "second"] as const;
  // Zero reads as "0 secs", not "0 hr".
  const first = total === 0 ? 2 : parts.findIndex((part) => part > 0);
  const shown = [first, first + 1].filter(
    (index) => index < units.length && (index === first || (parts[index] ?? 0) > 0),
  );
  const pieces = shown.map((index) =>
    numberFormat(locale, { style: "unit", unit: units[index], unitDisplay: "short" }).format(
      parts[index] ?? 0,
    ),
  );
  return new Intl.ListFormat(locale, { type: "unit", style: "narrow" }).format(pieces);
}

/** A plain number with the locale's separators and Latin digits, e.g. "12,480". */
export function formatNumber(
  value: number,
  language = "en",
  options?: Intl.NumberFormatOptions,
): string {
  return numberFormat(formatLocale(language), options).format(value);
}

/** File and storage sizes in decimal units, e.g. "1.5 GB". */
export function formatBytes(bytes: number, language = "en"): string {
  return bytesIn(formatLocale(language), bytes);
}

/** A bitrate in bits per second, shown in megabits, e.g. "8.2 Mb/s". */
export function formatBitrate(bitsPerSecond: number, language = "en"): string {
  return bitrateIn(formatLocale(language), bitsPerSecond);
}

/** A duration in seconds: "1:12:05" (clock) or "1 hr 12 mins" (short). */
export function formatDuration(
  seconds: number,
  language = "en",
  style: DurationStyle = "clock",
): string {
  return durationIn(formatLocale(language), seconds, style);
}

/** ISO 8601 duration for `<time dateTime>`, e.g. "PT1H12M5S". */
export function isoDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(finite(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `PT${String(hours)}H${String(minutes)}M${String(total % 60)}S`;
}

/** Digits after the decimal point of a currency (SAR 2, KWD 3, JPY 0). */
export function currencyDigits(currency: string): number {
  try {
    return (
      new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions()
        .maximumFractionDigits ?? 2
    );
  } catch {
    return 2;
  }
}

/** An amount in minor units (2900 = 29.00 SAR) as money: "SAR 29.00" / "29.00 ر.س.". */
export function formatMoney(minor: number, currency: string, language = "en"): string {
  const locale = formatLocale(language);
  const digits = currencyDigits(currency);
  const value = finite(minor) / 10 ** digits;
  try {
    return numberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  } catch {
    return `${numberFormat(locale, { minimumFractionDigits: digits }).format(value)} ${currency}`;
  }
}

export interface Formatters {
  locale: string;
  /** Minor units of `currency` as money. */
  money: (minor: number, currency: string) => string;
  number: (value: number, options?: Intl.NumberFormatOptions) => string;
  percent: (ratio: number, options?: Intl.NumberFormatOptions) => string;
  dateTime: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
  date: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
  bytes: (bytes: number) => string;
  bitrate: (bitsPerSecond: number) => string;
  duration: (seconds: number, style?: DurationStyle) => string;
}

export function createFormatters(
  language: string,
  timeZone = DEFAULT_TIME_ZONE,
  numerals: Numerals = "latn",
): Formatters {
  const locale = formatLocale(language, numerals);
  return {
    locale,
    money: (minor, currency) => formatMoney(minor, currency, language),
    number: (value, options) => numberFormat(locale, options).format(value),
    percent: (ratio, options) =>
      numberFormat(locale, {
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
    bytes: (bytes) => bytesIn(locale, bytes),
    bitrate: (bitsPerSecond) => bitrateIn(locale, bitsPerSecond),
    duration: (seconds, style = "clock") => durationIn(locale, seconds, style),
  };
}

/** Locale-aware number and date formatters for the current UI language. */
export function useFormatters(timeZone = DEFAULT_TIME_ZONE): Formatters {
  const { i18n } = useTranslation("ui");
  const language = i18n.language;
  return useMemo(() => createFormatters(language, timeZone), [language, timeZone]);
}
