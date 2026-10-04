import type { Artwork } from "@smart-iptv/api-portal";
import type { ImageUrlResolver } from "@smart-iptv/ui";

/**
 * PosterImage/BackdropImage source for an artwork. The API lists a URL per size
 * and format (`{"w500": {"webp": url, "avif": url}}`, names carry a content
 * hash), so the resolver looks each one up; a size it lacks falls back to the
 * artwork's default URL.
 */
export function artworkSource(artwork: Artwork | null | undefined): ImageUrlResolver | null {
  if (!artwork) return null;
  const { sizes, url } = artwork;
  return (size, format) => sizes[size]?.[format] ?? sizes[size]?.webp ?? url;
}

/** One URL of an artwork at a size (for `<img>` without a picture element), else its default. */
export function artworkUrl(artwork: Artwork | null | undefined, size: string): string | null {
  if (!artwork) return null;
  return artwork.sizes[size]?.webp ?? artwork.url;
}
