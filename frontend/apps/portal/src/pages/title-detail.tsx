import {
  isApiError,
  useMoviesRetrieve,
  useSeriesRetrieve,
  useSeriesSeasonRetrieve,
  type Cast,
  type Episode,
  type MovieDetail,
  type PersonBrief,
  type SeriesDetail,
} from "@smart-iptv/api-portal";
import {
  Avatar,
  BackdropImage,
  Button,
  EmptyState,
  ErrorState,
  PosterImage,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  useFormatters,
} from "@smart-iptv/ui";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Play, RotateCcw, SearchX } from "lucide-react";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Rail } from "../components/rail";
import { TitleCard } from "../components/title-card";
import { TitleMeta } from "../components/title-meta";
import { useEpisodeLabel, WatchProgressBar } from "../components/watch-card";
import { FavoriteButton, RatingButtons, TrailerButton } from "../features/catalog/title-actions";
import { artworkSource, artworkUrl } from "../lib/artwork";
import { languageName } from "../lib/languages";
import { watchPath } from "../lib/links";
import { usePageTitle } from "../lib/page-title";

const movieApi = getRouteApi("/app/shell/movies/$titleId");
const seriesApi = getRouteApi("/app/shell/series/$titleId");

type Detail = MovieDetail | SeriesDetail;

function isSeries(detail: Detail): detail is SeriesDetail {
  return detail.type === "series";
}

/** Movies and episodes count as finished past 90 % (ADR-0013 §9), so they start over. */
function resumable(
  progress: { position_ms: number; completed: boolean } | null | undefined,
): boolean {
  return (
    progress !== null && progress !== undefined && !progress.completed && progress.position_ms > 0
  );
}

function DetailSkeleton() {
  const { t } = useTranslation();
  return (
    <div aria-busy="true">
      <span className="sr-only" role="status">
        {t("states.loading")}
      </span>
      <Skeleton className="h-[28rem] w-full rounded-none" />
      <div className="mx-auto mt-6 grid max-w-[1800px] gap-3 px-4 sm:px-6 lg:px-10">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  );
}

function NotFound() {
  const { t } = useTranslation();
  return (
    <div className="page-top px-4">
      <EmptyState
        icon={<SearchX />}
        title={t("title.notFoundTitle")}
        description={t("title.notFound")}
        action={
          <Button asChild>
            <Link to="/">{t("notFound.home")}</Link>
          </Button>
        }
      />
    </div>
  );
}

function PersonLink({ person, role }: { person: PersonBrief | Cast; role?: string }) {
  const photo = artworkUrl(person.profile, "w185");
  return (
    <Link
      to="/people/$personId"
      params={{ personId: person.id }}
      className="group/person grid justify-items-center gap-2 rounded-card p-1 text-center outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {photo ? (
        <img
          src={photo}
          alt=""
          loading="lazy"
          decoding="async"
          className="size-20 rounded-full object-cover ring-1 ring-border sm:size-24"
        />
      ) : (
        <Avatar name={person.name} className="size-20 text-lg sm:size-24" />
      )}
      <span className="line-clamp-2 text-ui font-medium text-foreground group-hover/person:underline">
        {person.name}
      </span>
      {role ? <span className="line-clamp-2 text-xs text-muted-foreground">{role}</span> : null}
    </Link>
  );
}

function Facts({ detail }: { detail: Detail }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const list = (items: readonly string[]) =>
    new Intl.ListFormat(format.locale, { type: "conjunction" }).format(items);
  const languages = (codes: readonly string[]) =>
    list(codes.map((code) => languageName(code, format.locale) ?? code.toUpperCase()));
  const regions = new Intl.DisplayNames([format.locale], { type: "region" });
  const released = isSeries(detail) ? detail.first_air_date : detail.release_date;
  const rows: { key: string; label: string; value: ReactNode }[] = [];
  if (detail.genres.length > 0) {
    rows.push({
      key: "genres",
      label: t("title.genres"),
      value: list(detail.genres.map((genre) => genre.name)),
    });
  }
  if (detail.directors.length > 0) {
    rows.push({
      key: "directors",
      label: isSeries(detail) ? t("title.creators") : t("title.directors"),
      value: list(detail.directors.map((person) => person.name)),
    });
  }
  if (detail.writers.length > 0) {
    rows.push({
      key: "writers",
      label: t("title.writers"),
      value: list(detail.writers.map((person) => person.name)),
    });
  }
  if (detail.tracks.audio.length > 0) {
    rows.push({ key: "audio", label: t("title.audio"), value: languages(detail.tracks.audio) });
  }
  if (detail.tracks.subtitles.length > 0) {
    rows.push({
      key: "subtitles",
      label: t("title.subtitles"),
      value: languages(detail.tracks.subtitles),
    });
  }
  if (detail.countries.length > 0) {
    rows.push({
      key: "countries",
      label: t("title.countries"),
      value: list(detail.countries.map((code) => regions.of(code) ?? code)),
    });
  }
  if (released) {
    rows.push({
      key: "released",
      label: t("title.released"),
      value: format.date(released, { timeZone: "UTC" }),
    });
  }
  if (rows.length === 0) return null;
  return (
    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[auto_1fr]" lang={i18n.language}>
      {rows.map((row) => (
        <div key={row.key} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="text-foreground">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EpisodeRow({ seriesId, episode }: { seriesId: string; episode: Episode }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const episodeLabel = useEpisodeLabel();
  const progress = episode.progress;
  const ratio =
    progress && progress.duration_ms > 0 ? progress.position_ms / progress.duration_ms : 0;
  const titleId = useId();
  return (
    <li className="group/episode grid grid-cols-[8rem_1fr] gap-3 rounded-card p-2 transition-colors hover:bg-accent/60 sm:grid-cols-[12rem_1fr] sm:gap-4">
      <Link
        {...watchPath("series", seriesId, { episode: episode.id })}
        aria-labelledby={titleId}
        className="relative block overflow-hidden rounded-input ring-1 ring-border/70 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <BackdropImage
          src={artworkSource(episode.still)}
          blurhash={episode.still?.blurhash}
          alt=""
          sizes="192px"
        />
        <span className="absolute inset-0 grid place-items-center bg-black/0 transition-colors group-hover/episode:bg-black/40">
          <Play
            aria-hidden="true"
            className="size-7 fill-white text-white opacity-0 drop-shadow transition-opacity group-hover/episode:opacity-100 rtl:-scale-x-100"
          />
        </span>
        {progress && progress.position_ms > 0 ? (
          <WatchProgressBar
            ratio={progress.completed ? 1 : ratio}
            className="absolute inset-x-0 bottom-0"
          />
        ) : null}
      </Link>
      <div className="grid min-w-0 content-start gap-1">
        <h3 id={titleId} className="text-sm font-semibold text-foreground">
          <span className="text-muted-foreground">
            {episodeLabel(episode.season_number, episode.number)}
          </span>{" "}
          {episode.title}
        </h3>
        <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
          {episode.air_date ? (
            <span>{format.date(episode.air_date, { timeZone: "UTC" })}</span>
          ) : null}
          {episode.runtime_min ? (
            <span>{format.duration(episode.runtime_min * 60, "short")}</span>
          ) : null}
          {progress?.completed ? <span className="text-primary">{t("title.watched")}</span> : null}
        </p>
        {episode.overview ? (
          <p className="line-clamp-2 text-ui text-muted-foreground sm:line-clamp-3">
            {episode.overview}
          </p>
        ) : null}
      </div>
    </li>
  );
}

function Seasons({ series, season }: { series: SeriesDetail; season: number | undefined }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const selectId = useId();
  const seasons = series.seasons;
  const fallback =
    series.next_episode?.season_number ??
    seasons.find((item) => item.number > 0)?.number ??
    seasons[0]?.number;
  const current = seasons.some((item) => item.number === season) ? season : fallback;
  const seasonLabel = (name: string | undefined, number: number) =>
    name !== undefined && name !== "" ? name : t("title.seasonNumber", { number });
  const detail = useSeriesSeasonRetrieve(series.id, current ?? 1, {
    query: { enabled: current !== undefined },
  });
  if (current === undefined) return null;

  return (
    <section aria-labelledby={`${selectId}-heading`} className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={`${selectId}-heading`} className="text-xl font-semibold text-foreground">
          {t("title.episodes")}
        </h2>
        {seasons.length > 1 ? (
          <div className="flex items-center gap-2">
            <label htmlFor={selectId} className="sr-only">
              {t("title.season")}
            </label>
            <Select
              value={String(current)}
              onValueChange={(value) => {
                void navigate({
                  to: "/series/$titleId",
                  params: { titleId: series.id },
                  search: { season: Number(value) },
                  replace: true,
                  resetScroll: false,
                });
              }}
            >
              <SelectTrigger id={selectId} className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {seasons.map((item) => (
                  <SelectItem key={item.number} value={String(item.number)}>
                    {seasonLabel(item.name, item.number)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">
            {seasonLabel(seasons[0]?.name, current)}
          </span>
        )}
      </div>
      {detail.isPending ? (
        <div className="grid gap-3" aria-busy="true">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      ) : detail.isError ? (
        <ErrorState
          title={t("title.seasonError")}
          error={detail.error}
          onRetry={() => {
            void detail.refetch();
          }}
        />
      ) : detail.data.episodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("title.noEpisodes")}</p>
      ) : (
        <ol className="grid gap-1">
          {detail.data.episodes.map((episode) => (
            <EpisodeRow key={episode.id} seriesId={series.id} episode={episode} />
          ))}
        </ol>
      )}
    </section>
  );
}

function PlayActions({ detail }: { detail: Detail }) {
  const { t } = useTranslation();
  const episodeLabel = useEpisodeLabel();
  const format = useFormatters();
  if (isSeries(detail)) {
    const next = detail.next_episode;
    if (next === null) return null;
    const resume = resumable(next.progress);
    return (
      <>
        <Button asChild size="lg">
          <Link {...watchPath("series", detail.id, { episode: next.id })}>
            <Play aria-hidden="true" className="fill-current" />
            {resume ? t("title.resume") : t("title.play")}
            <span className="font-normal opacity-80">
              {episodeLabel(next.season_number, next.number)}
            </span>
          </Link>
        </Button>
      </>
    );
  }
  const progress = detail.viewer.progress;
  const resume = resumable(progress);
  const left = progress
    ? Math.max(0, Math.round((progress.duration_ms - progress.position_ms) / 1000))
    : 0;
  return (
    <>
      <Button asChild size="lg">
        <Link {...watchPath("movie", detail.id)}>
          <Play aria-hidden="true" className="fill-current" />
          {resume ? t("title.resume") : t("title.play")}
        </Link>
      </Button>
      {resume ? (
        <Button asChild size="lg" variant="secondary" className="bg-background/60 backdrop-blur-sm">
          <Link {...watchPath("movie", detail.id, { restart: true })}>
            <RotateCcw aria-hidden="true" />
            {t("title.startOver")}
          </Link>
        </Button>
      ) : null}
      {resume ? (
        <span className="basis-full text-xs text-muted-foreground">
          {t("continue.left", { time: format.duration(left, "short") })}
        </span>
      ) : null}
    </>
  );
}

function TitlePage({ detail, season }: { detail: Detail; season?: number | undefined }) {
  const { t } = useTranslation();
  usePageTitle(detail.title);
  const logo = artworkUrl(detail.logo, "w500");
  const progress = isSeries(detail) ? null : detail.viewer.progress;
  const ratio =
    progress && progress.duration_ms > 0 && !progress.completed
      ? progress.position_ms / progress.duration_ms
      : 0;

  return (
    <article>
      <div className="relative isolate overflow-hidden">
        <div className="absolute inset-0 -z-10">
          <BackdropImage
            src={artworkSource(detail.backdrop)}
            blurhash={detail.backdrop?.blurhash}
            alt=""
            sizes="100vw"
            priority
            className="size-full aspect-auto"
          />
        </div>
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10 bg-linear-to-t from-background via-background/70 to-background/20"
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10 bg-linear-to-r from-background/90 via-background/40 to-transparent rtl:bg-linear-to-l"
        />
        <div className="mx-auto grid max-w-[1800px] gap-6 px-4 pt-28 pb-10 sm:px-6 md:grid-cols-[14rem_1fr] md:items-end lg:grid-cols-[16rem_1fr] lg:px-10 lg:pt-40">
          <div className="hidden overflow-hidden rounded-card shadow-elevation ring-1 ring-border/70 md:block">
            <PosterImage
              src={artworkSource(detail.poster)}
              blurhash={detail.poster?.blurhash}
              alt=""
              sizes="256px"
              priority
            />
          </div>
          <div className="grid max-w-3xl gap-4">
            {logo ? (
              <h1 className="m-0">
                <img
                  src={logo}
                  alt={detail.title}
                  className="max-h-24 w-auto max-w-[min(24rem,85%)] object-contain object-start drop-shadow-lg sm:max-h-32"
                />
              </h1>
            ) : (
              <h1 className="text-3xl font-bold text-foreground sm:text-5xl ltr:tracking-tight">
                {detail.title}
              </h1>
            )}
            {detail.original_title && detail.original_title !== detail.title ? (
              <p className="-mt-2 text-sm text-muted-foreground">
                <bdi>{detail.original_title}</bdi>
              </p>
            ) : null}
            <TitleMeta
              year={detail.year}
              rating={detail.rating}
              runtimeMin={detail.runtime_min}
              certification={detail.certification}
              badge={detail.quality?.badge ?? null}
              className="text-foreground/90"
            />
            {detail.tagline ? (
              <p className="text-base italic text-foreground/80">{detail.tagline}</p>
            ) : null}
            {detail.overview ? (
              <p className="max-w-2xl text-sm leading-6 text-foreground/90 sm:text-base">
                {detail.overview}
              </p>
            ) : null}
            {ratio > 0 ? (
              <WatchProgressBar ratio={ratio} className="max-w-sm rounded-full" />
            ) : null}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <PlayActions detail={detail} />
              <TrailerButton youtubeKey={detail.trailer_youtube_key} title={detail.title} />
              <FavoriteButton type={detail.type} id={detail.id} favorite={detail.viewer.favorite} />
              <RatingButtons type={detail.type} id={detail.id} rating={detail.viewer.rating} />
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto grid max-w-[1800px] gap-10 px-4 pb-6 sm:px-6 lg:px-10">
        {isSeries(detail) ? <Seasons series={detail} season={season} /> : null}
        <section aria-label={t("title.details")} className="max-w-3xl">
          <Facts detail={detail} />
        </section>
      </div>

      <div className="grid gap-10">
        {detail.cast.length > 0 ? (
          <Rail title={t("title.cast")} itemClassName="w-24 sm:w-28">
            {detail.cast.map((person) => (
              <PersonLink key={person.id} person={person} role={person.character} />
            ))}
          </Rail>
        ) : null}
        {detail.similar.length > 0 ? (
          <Rail title={t("title.similar")}>
            {detail.similar.map((item) => (
              <TitleCard key={`${item.type}:${item.id}`} title={item} />
            ))}
          </Rail>
        ) : null}
      </div>
    </article>
  );
}

function errorView(error: unknown, retry: () => void) {
  if (isApiError(error) && error.code === "NOT_FOUND") return <NotFound />;
  return (
    <div className="page-top px-4">
      <ErrorState error={error} onRetry={retry} />
    </div>
  );
}

export function MovieDetailPage() {
  const { titleId } = movieApi.useParams();
  const movie = useMoviesRetrieve(titleId);
  if (movie.isPending) return <DetailSkeleton />;
  if (movie.isError) return errorView(movie.error, () => void movie.refetch());
  return <TitlePage detail={movie.data} />;
}

export function SeriesDetailPage() {
  const { titleId } = seriesApi.useParams();
  const { season } = seriesApi.useSearch();
  const series = useSeriesRetrieve(titleId);
  if (series.isPending) return <DetailSkeleton />;
  if (series.isError) return errorView(series.error, () => void series.refetch());
  return <TitlePage detail={series.data} season={season} />;
}
