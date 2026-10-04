/**
 * The `cursor` parameter of a DRF cursor-paginated `next` link
 * ("…/movies?cursor=cD0y…&page_size=24"), or undefined at the last page.
 */
export function cursorOf(link: string | null | undefined): string | undefined {
  if (!link) return undefined;
  try {
    return new URL(link, "http://portal.invalid").searchParams.get("cursor") ?? undefined;
  } catch {
    return undefined;
  }
}
