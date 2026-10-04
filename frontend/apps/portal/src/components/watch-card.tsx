import type { WatchItem } from "@smart-iptv/api-portal";
import { BackdropImage, cn, useFormatters } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { Play } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { artworkSource } from "../lib/artwork";
import { watchPath } from "../lib/links";

/** 0–1 of the way through, clamped. */
export function watchedRatio(item: Pick<WatchItem, "position_ms" | "duration_ms">): number {
  if (item.duration_ms <= 0) return 0;
  return Math.min(1, Math.max(0, item.position_ms / item.duration_ms));
}

/** "S1 · E2": the season and episode of an episode, numbers in the UI language. */
export function useEpisodeLabel(): (season: number, episode: number) => string {
  const { t } = useTranslation();
  return (season, episode) => t("title.episodeCode", { season, episode });
}

/** A thin progress bar over the bottom of artwork. */
export function WatchProgressBar({ ratio, className }: { ratio: number; className?: string }) {
  return (
    <div aria-hidden="true" className={cn("h-1 w-full overflow-hidden bg-white/25", className)}>
      <div
        className="h-full bg-primary"
        style={{ width: `${String(Math.round(ratio * 1000) / 10)}%` }}
      />
    </div>
  );
}

/**
 * Continue watching: the still or backdrop with the progress bar, the title,
 * the episode and the time left. The whole card resumes playback.
 */
export function WatchCard({ item, actions }: { item: WatchItem; actions?: ReactNode }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const episodeLabel = useEpisodeLabel();
  const ratio = watchedRatio(item);
  const art = item.episode?.still ?? item.title.backdrop;
  const leftS = Math.max(0, Math.round((item.duration_ms - item.position_ms) / 1000));
  const subtitle = item.episode
    ? `${episodeLabel(item.episode.season_number, item.episode.number)} · ${item.episode.title}`
    : null;
  const target =
    item.type === "movie"
      ? watchPath("movie", item.title.id)
      : watchPath("series", item.title.id, { episode: item.episode?.id });
  const progressText = item.completed
    ? t("title.watched")
    : item.position_ms <= 0
      ? t("continue.upNext")
      : t("continue.left", { time: format.duration(leftS, "short") });

  return (
    <div className="group/watch relative grid gap-2">
      <Link
        {...target}
        aria-label={[t("continue.resumeTitle", { title: item.title.title }), subtitle]
          .filter(Boolean)
          .join(", ")}
        className="relative block overflow-hidden rounded-card ring-1 ring-border/70 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <BackdropImage
          src={artworkSource(art)}
          blurhash={art?.blurhash}
          alt=""
          sizes="(min-width: 640px) 288px, 256px"
        />
        <span className="absolute inset-0 grid place-items-center bg-black/0 transition-colors duration-150 group-hover/watch:bg-black/35 group-focus-within/watch:bg-black/35">
          <span className="grid size-11 place-items-center rounded-full bg-black/60 text-white opacity-0 ring-1 ring-white/40 transition-opacity duration-150 group-hover/watch:opacity-100 group-focus-within/watch:opacity-100">
            <Play aria-hidden="true" className="size-5 fill-current" />
          </span>
        </span>
        {item.position_ms > 0 ? (
          <WatchProgressBar ratio={ratio} className="absolute inset-x-0 bottom-0" />
        ) : null}
      </Link>
      <div className="flex min-w-0 items-start gap-2 px-0.5">
        <div className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate text-ui font-medium text-foreground">{item.title.title}</span>
          {subtitle ? (
            <span className="truncate text-xs text-muted-foreground">{subtitle}</span>
          ) : null}
          <span className="text-xs text-muted-foreground">{progressText}</span>
        </div>
        {actions}
      </div>
    </div>
  );
}
