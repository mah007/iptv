import type { PlayingType, TitleType } from "@smart-iptv/api-portal";

/** Router target of a title page. */
export function titlePath(type: TitleType, id: string) {
  return type === "movie"
    ? ({ to: "/movies/$titleId", params: { titleId: id } } as const)
    : ({ to: "/series/$titleId", params: { titleId: id } } as const);
}

/** Router target of the player for a movie, or a series (at an episode, else the next one). */
export function watchPath(
  type: TitleType | PlayingType,
  id: string,
  options: { episode?: string | undefined; restart?: boolean } = {},
) {
  const search = {
    ...(options.episode !== undefined ? { episode: options.episode } : {}),
    ...(options.restart === true ? { restart: true } : {}),
  };
  return type === "movie"
    ? ({ to: "/watch/movie/$titleId", params: { titleId: id }, search } as const)
    : ({ to: "/watch/series/$titleId", params: { titleId: id }, search } as const);
}
