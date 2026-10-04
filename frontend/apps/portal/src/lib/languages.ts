/**
 * Track languages arrive as ISO 639-2 codes ("ara", "eng", "und"). Intl knows
 * the three-letter codes too, so names come out in the UI language.
 */
export function languageName(code: string, uiLocale: string): string | null {
  if (!code || code === "und" || code === "zxx" || code === "mul") return null;
  try {
    const canonical = new Intl.Locale(code).language;
    return new Intl.DisplayNames([uiLocale], { type: "language" }).of(canonical) ?? null;
  } catch {
    return null;
  }
}

/** The two-letter subtag when Intl knows one ("ara" → "ar"), else the code itself. */
export function languageTag(code: string): string {
  try {
    return new Intl.Locale(code).language;
  } catch {
    return code;
  }
}
