import { currencyDigits } from "@smart-iptv/ui";

/*
 * Money crosses the API in integer minor units with a currency (SPEC §6:
 * 2900 = 29.00 SAR). Admins type major units; these convert both ways using
 * the currency's own number of decimals (SAR 2, KWD 3, JPY 0).
 */

/** "29.5" → 2950 for SAR; null for anything that isn't a non-negative amount. */
export function majorToMinor(text: string, currency: string): number | null {
  const digits = currencyDigits(currency);
  const normalized = text.trim().replace(",", ".");
  const pattern =
    digits === 0 ? /^\d+$/u : new RegExp(`^\\d+(?:\\.\\d{1,${String(digits)}})?$`, "u");
  if (!pattern.test(normalized)) return null;
  const [whole = "0", fraction = ""] = normalized.split(".");
  return Number(whole) * 10 ** digits + Number(fraction.padEnd(digits, "0") || "0");
}

/** 2950 → "29.50" for SAR: what a money input shows. */
export function minorToMajor(minor: number, currency: string): string {
  const digits = currencyDigits(currency);
  return (minor / 10 ** digits).toFixed(digits);
}
