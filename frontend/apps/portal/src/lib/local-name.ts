/** The Arabic name in Arabic when there is one, else the English one. */
export function localName(item: { name_en: string; name_ar: string }, language: string): string {
  return language.startsWith("ar") && item.name_ar ? item.name_ar : item.name_en;
}

/** The Arabic description in Arabic when there is one, else the English one. */
export function localDescription(
  item: { description_en: string; description_ar: string },
  language: string,
): string {
  return language.startsWith("ar") && item.description_ar
    ? item.description_ar
    : item.description_en;
}
