import type { CategoryBrief, Image, ImageKind } from "@smart-iptv/api";
import type { ImageUrlResolver } from "@smart-iptv/ui";

/**
 * PosterImage/BackdropImage source for a stored image. The API lists a URL
 * per size and format (`{"w500": {"webp": url, "avif": url}}`, names carry a
 * content hash), so the resolver looks each one up; a size it lacks falls
 * back to the image's default URL.
 */
export function imageSource(image: Image | null | undefined): ImageUrlResolver | null {
  if (!image) return null;
  const { sizes, url } = image;
  if (url === null && Object.keys(sizes).length === 0) return null;
  return (size, format) => sizes[size]?.[format] ?? sizes[size]?.webp ?? url ?? "";
}

/**
 * The image of a kind to show in a UI language: the primary one in that
 * language, then any in that language, then the primary one, then any.
 */
export function pickImage(
  images: readonly Image[],
  kind: ImageKind,
  language: string,
): Image | null {
  const ofKind = images.filter((image) => image.kind === kind);
  const lang = language.slice(0, 2);
  return (
    ofKind.find((image) => image.language === lang && image.is_primary) ??
    ofKind.find((image) => image.language === lang) ??
    ofKind.find((image) => image.is_primary) ??
    ofKind[0] ??
    null
  );
}

/** The Arabic name in Arabic when there is one, else the English one. */
export function localName(item: { name_en: string; name_ar: string }, language: string): string {
  return language.startsWith("ar") && item.name_ar ? item.name_ar : item.name_en;
}

/** A title in the UI language: the Arabic title in Arabic when there is one. */
export function localTitle(item: { title: string; title_ar: string }, language: string): string {
  return language.startsWith("ar") && item.title_ar ? item.title_ar : item.title;
}

export function categoryNames(categories: readonly CategoryBrief[], language: string): string[] {
  return categories.map((category) => localName(category, language));
}
