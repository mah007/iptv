import { Star, Subtitles } from "lucide-react";
import { Slot } from "radix-ui";
import { Children, isValidElement, useId, type ComponentProps, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";
import { PosterImage, type PosterImageProps } from "./poster-image";
import { QualityBadges, qualityKeys, type MediaSummary } from "./quality-badges";
import { Skeleton } from "./skeleton";

/** ISO 639-1 or 639-2 ("ar", "ara") to a canonical language subtag, or null. */
function languageOf(code: string): string | null {
  try {
    return new Intl.Locale(code).language;
  } catch {
    return null;
  }
}

export interface PosterCardProps extends Omit<ComponentProps<"a">, "title" | "children" | "media"> {
  title: string;
  year?: number | null | undefined;
  /** Poster image folder or resolver (see PosterImage). */
  poster?: PosterImageProps["src"];
  blurhash?: string | null | undefined;
  /** 0–10, as from TMDB. */
  rating?: number | null | undefined;
  /** Probe summary of the primary file, for the quality badges. */
  media?: MediaSummary | null | undefined;
  /** Subtitle languages (ISO 639-1 or 639-2), e.g. ["ar", "en"]. */
  subtitles?: readonly string[] | undefined;
  /** Small badges under the title, e.g. a StatusBadge. */
  badges?: ReactNode;
  /** Render the child element (e.g. a router Link) as the card's link. */
  asChild?: boolean;
  children?: ReactNode;
}

/**
 * Poster tile for library grids: the whole card is one link named by the
 * title. Year, rating, quality and subtitles show in an overlay on hover or
 * keyboard focus, and are read out as the link's description.
 */
export function PosterCard({
  title,
  year,
  poster,
  blurhash,
  rating,
  media,
  subtitles = [],
  badges,
  asChild = false,
  className,
  children,
  ...props
}: PosterCardProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const titleId = useId();
  const detailsId = useId();
  const badgesId = useId();

  const languages = subtitles
    .map(languageOf)
    .filter((language): language is string => language !== null)
    .filter((language, index, all) => all.indexOf(language) === index);
  const languageNames = new Intl.DisplayNames([format.locale], { type: "language" });
  const ratingText =
    typeof rating === "number" && rating > 0
      ? format.number(rating, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
      : null;
  const quality = media ? qualityKeys(media) : [];
  const description = [
    year ? String(year) : null,
    ratingText ? t("posterCard.rating", { value: ratingText }) : null,
    quality.length > 0
      ? new Intl.ListFormat(format.locale, { type: "unit" }).format(
          quality.map((key) => t(`quality.long.${key}`)),
        )
      : null,
    languages.length > 0
      ? t("posterCard.subtitles", {
          languages: new Intl.ListFormat(format.locale, { type: "conjunction" }).format(
            languages.map((language) => languageNames.of(language) ?? language),
          ),
        })
      : null,
  ].filter((part): part is string => part !== null);
  const hasOverlay = ratingText !== null || quality.length > 0 || languages.length > 0;

  const content = (
    <>
      <div className="relative overflow-hidden rounded-card shadow-xs ring-1 ring-border/70 transition-shadow duration-200 ease-out group-hover/poster:shadow-elevation">
        <PosterImage src={poster} blurhash={blurhash} alt="" />
        {hasOverlay ? (
          <div
            aria-hidden="true"
            data-slot="poster-overlay"
            className="pointer-events-none absolute inset-0 flex flex-col justify-end gap-1.5 bg-linear-to-t from-black/85 via-black/35 to-transparent p-2 text-white opacity-0 transition-opacity duration-150 ease-out group-hover/poster:opacity-100 group-focus-visible/poster:opacity-100"
          >
            {ratingText ? (
              <span className="inline-flex items-center gap-1 text-xs font-semibold tabular-nums">
                <Star className="size-3.5 fill-warning text-warning" />
                {ratingText}
              </span>
            ) : null}
            {media ? <QualityBadges media={media} overlay /> : null}
            {languages.length > 0 ? (
              <span className="inline-flex items-center gap-1 text-[0.6875rem] font-medium">
                <Subtitles className="size-3.5 shrink-0" />
                {languages.map((language) => language.toUpperCase()).join(" · ")}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 px-0.5">
        <span id={titleId} className="line-clamp-2 text-ui font-medium text-foreground">
          {title}
        </span>
        {year ? <span className="text-xs tabular-nums text-muted-foreground">{year}</span> : null}
        {badges ? (
          <span id={badgesId} className="mt-1 flex flex-wrap items-center gap-1">
            {badges}
          </span>
        ) : null}
        {description.length > 0 ? (
          <span id={detailsId} className="sr-only">
            {description.join(t("separator"))}
          </span>
        ) : null}
      </div>
    </>
  );

  const shared = {
    "data-slot": "poster-card",
    "aria-labelledby": titleId,
    "aria-describedby":
      [description.length > 0 ? detailsId : null, badges ? badgesId : null]
        .filter((id) => id !== null)
        .join(" ") || undefined,
    className: cn(
      "group/poster flex min-w-0 flex-col gap-2 rounded-card outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background",
      className,
    ),
  };

  if (asChild) {
    return (
      <Slot.Root {...shared} {...props}>
        <Slot.Slottable>{children}</Slot.Slottable>
        {content}
      </Slot.Root>
    );
  }
  return (
    <a {...shared} {...props}>
      {content}
    </a>
  );
}

/** Placeholder with the card's shape while a grid loads. */
export function PosterCardSkeleton({ className }: { className?: string }) {
  return (
    <div data-slot="poster-card-skeleton" className={cn("flex flex-col gap-2", className)}>
      <Skeleton className="aspect-[2/3] w-full rounded-card" />
      <Skeleton className="h-4 w-4/5" />
      <Skeleton className="h-3 w-1/3" />
    </div>
  );
}

export interface PosterGridProps extends ComponentProps<"ul"> {
  loading?: boolean;
  /** Placeholder cards shown while loading. */
  skeletonCount?: number;
}

/** Responsive poster grid: two columns on phones, as many as fit from `sm` up. */
export function PosterGrid({
  loading = false,
  skeletonCount = 12,
  className,
  children,
  ...props
}: PosterGridProps) {
  const items = loading
    ? Array.from({ length: skeletonCount }, (_, index) => (
        <PosterCardSkeleton key={`skeleton-${String(index)}`} />
      ))
    : Children.toArray(children);
  return (
    <ul
      role="list"
      data-slot="poster-grid"
      aria-busy={loading || undefined}
      className={cn(
        "grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] gap-x-3 gap-y-5 sm:grid-cols-[repeat(auto-fill,minmax(var(--density-poster),1fr))] sm:gap-x-(--density-card)",
        className,
      )}
      {...props}
    >
      {items.map((item, index) => (
        <li key={isValidElement(item) && item.key !== null ? item.key : index} className="min-w-0">
          {item}
        </li>
      ))}
    </ul>
  );
}
