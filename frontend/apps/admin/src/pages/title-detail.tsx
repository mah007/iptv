import {
  getMoviesListQueryKey,
  getMoviesRetrieveQueryKey,
  getSeriesListQueryKey,
  getSeriesRetrieveQueryKey,
  useMoviesRefreshMetadata,
  useMoviesRetrieve,
  useMoviesUpdate,
  useSeriesRefreshMetadata,
  useSeriesRetrieve,
  useSeriesUpdate,
  type File,
  type PatchedMovieUpdateRequest,
  type Season,
  type SeriesDetail,
} from "@smart-iptv/api";
import {
  BackdropImage,
  Badge,
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  PosterImage,
  QualityBadges,
  RelativeTime,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { Eye, EyeOff, FileVideo, Pencil, RefreshCw, Star } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { imageSource, localName, localTitle, pickImage } from "../features/catalog/artwork";
import { SyntheticBadge, type TitleKind } from "../features/titles/synthetic-badge";
import { TitleEditorSheet, type TitleDetail } from "../features/titles/title-editor";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

const movieRoute = getRouteApi("/app/movies/$titleId");
const seriesRoute = getRouteApi("/app/series/$titleId");

/** A refresh runs in the background; look again after this long. */
const REFRESH_RECHECK_MS = 4_000;

function FilesTable({ files }: { files: readonly File[] }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (files.length === 0) {
    return <p className="text-ui text-muted-foreground">{t("titles.files.none")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("titles.files.path")}</TableHead>
            <TableHead>{t("titles.files.quality")}</TableHead>
            <TableHead className="text-end">{t("titles.files.duration")}</TableHead>
            <TableHead className="text-end">{t("titles.files.size")}</TableHead>
            <TableHead>{t("titles.files.state")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {files.map((file) => {
            const channels = Math.max(0, ...file.audio.map((track) => track.channels));
            return (
              <TableRow key={file.id}>
                <TableCell className="max-w-96">
                  <span
                    className="block truncate font-mono text-xs"
                    dir="ltr"
                    title={file.relative_path}
                  >
                    {file.relative_path}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {file.library.name}
                    {file.removed_at ? ` · ${t("titles.files.removed")}` : ""}
                  </span>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-1">
                    <QualityBadges
                      media={{
                        width: file.width,
                        height: file.height,
                        video_codec: file.video_codec,
                        hdr: file.hdr,
                        audio_channels: channels,
                      }}
                    />
                    <span dir="ltr" className="text-xs tabular-nums text-muted-foreground">
                      {[
                        file.width && file.height
                          ? `${String(file.width)}×${String(file.height)}`
                          : null,
                        file.video_codec || null,
                        file.container || null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-end tabular-nums">
                  {file.duration_ms === null ? "" : format.duration(file.duration_ms / 1000)}
                </TableCell>
                <TableCell className="text-end tabular-nums">{format.bytes(file.size)}</TableCell>
                <TableCell>
                  <span className="flex flex-wrap items-center gap-1">
                    <StatusBadge status={file.state} />
                    {file.direct_play ? (
                      <Badge tone="success">{t("titles.files.directPlay")}</Badge>
                    ) : null}
                  </span>
                  {file.error ? (
                    <span className="mt-1 block text-xs text-danger-text" dir="auto">
                      {file.error}
                    </span>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function SeasonsCard({ series }: { series: SeriesDetail }) {
  const { t, i18n } = useTranslation();
  if (series.seasons.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("titles.seasons.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-ui text-muted-foreground">{t("titles.seasons.none")}</p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("titles.seasons.title")}</CardTitle>
        <CardDescription>{t("titles.seasons.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {series.seasons.map((season: Season, index) => (
          <details
            key={season.id}
            open={index === 0}
            className="group rounded-input border border-border"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-ui font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <span>
                {season.number === 0
                  ? t("titles.seasons.specials")
                  : t("titles.seasons.season", { number: season.number })}
              </span>
              <span className="text-xs font-normal text-muted-foreground">
                {t("titles.seasons.episodes", { count: season.episodes.length })}
              </span>
            </summary>
            <div className="grid gap-4 border-t border-border p-3">
              {season.episodes.map((episode) => (
                <div key={episode.id} className="grid gap-2">
                  <div className="flex flex-wrap items-baseline gap-2 text-ui">
                    <span className="font-mono text-xs text-muted-foreground" dir="ltr">
                      {`S${String(season.number).padStart(2, "0")}E${String(episode.number).padStart(2, "0")}`}
                    </span>
                    <span className="font-medium text-foreground" dir="auto">
                      {localTitle(episode, i18n.language) || t("titles.seasons.untitled")}
                    </span>
                  </div>
                  <FilesTable files={episode.files} />
                </div>
              ))}
            </div>
          </details>
        ))}
      </CardContent>
    </Card>
  );
}

function OverviewCard({ title }: { title: TitleDetail }) {
  const { t } = useTranslation();
  const blocks = [
    {
      lang: "en",
      dir: "ltr",
      label: t("titles.detail.english"),
      text: title.overview,
      tagline: title.tagline,
    },
    {
      lang: "ar",
      dir: "rtl",
      label: t("titles.detail.arabic"),
      text: title.overview_ar,
      tagline: title.tagline_ar,
    },
  ] as const;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("titles.detail.overview")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-5 md:grid-cols-2">
        {blocks.map((block) => (
          <section key={block.lang} className="grid content-start gap-1.5">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {block.label}
            </h3>
            {block.tagline ? (
              <p lang={block.lang} dir={block.dir} className="text-ui italic text-muted-foreground">
                {block.tagline}
              </p>
            ) : null}
            {block.text ? (
              <p
                lang={block.lang}
                dir={block.dir}
                className="text-ui leading-relaxed text-foreground"
              >
                {block.text}
              </p>
            ) : (
              <p className="text-ui text-muted-foreground">{t("titles.detail.noOverview")}</p>
            )}
          </section>
        ))}
      </CardContent>
    </Card>
  );
}

function DetailsCard({ title, kind }: { title: TitleDetail; kind: TitleKind }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const released = "release_date" in title ? title.release_date : title.first_air_date;
  const runtime = "runtime_min" in title ? title.runtime_min : title.episode_run_time;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("titles.detail.details")}</CardTitle>
      </CardHeader>
      <CardContent>
        <DescriptionList>
          <DescriptionItem label={t("titles.detail.source")}>
            <span className="flex flex-wrap items-center gap-1.5">
              {t(`titles.sources.${title.metadata_source}`)}
              {title.synthetic ? <SyntheticBadge /> : null}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.tmdb")}>
            {title.tmdb_id === null ? null : (
              <span className="font-mono text-ui tabular-nums" dir="ltr">
                {title.tmdb_id}
              </span>
            )}
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.xtreamId")}>
            <span className="font-mono text-ui tabular-nums" dir="ltr">
              {title.xc_id}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.originalTitle")}>
            {title.original_title ? <span dir="auto">{title.original_title}</span> : null}
          </DescriptionItem>
          <DescriptionItem
            label={kind === "movie" ? t("titles.detail.released") : t("titles.detail.firstAired")}
          >
            {released ? format.date(released) : null}
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.runtime")}>
            {runtime ? format.duration(runtime * 60, "short") : null}
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.refreshed")}>
            {title.metadata_refreshed_at ? (
              <RelativeTime value={title.metadata_refreshed_at} />
            ) : null}
          </DescriptionItem>
          <DescriptionItem label={t("titles.detail.locked")}>
            {title.metadata_locked_fields.length > 0 ? (
              <span className="flex flex-wrap gap-1">
                {title.metadata_locked_fields.map((field) => (
                  <Badge key={field} className="font-mono">
                    {field}
                  </Badge>
                ))}
              </span>
            ) : null}
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function Hero({ title }: { title: TitleDetail }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const poster = pickImage(title.images, "poster", i18n.language);
  const backdrop = pickImage(title.images, "backdrop", i18n.language);
  const name = localTitle(title, i18n.language);
  const other = i18n.language.startsWith("ar") ? title.title : title.title_ar;
  return (
    <div className="relative isolate overflow-hidden rounded-card border border-border bg-card">
      {backdrop ? (
        <div aria-hidden="true" className="absolute inset-0 -z-10">
          <BackdropImage
            src={imageSource(backdrop)}
            blurhash={backdrop.blurhash}
            alt=""
            priority
            className="size-full opacity-30 dark:opacity-25"
          />
          <div className="absolute inset-0 bg-linear-to-t from-card via-card/80 to-card/30" />
        </div>
      ) : null}
      <div className="flex flex-col gap-5 p-(--density-card) sm:flex-row sm:items-end">
        <PosterImage
          src={imageSource(poster)}
          blurhash={poster?.blurhash}
          alt={t("titles.detail.posterOf", { title: name })}
          sizes="160px"
          priority
          className="w-32 shrink-0 rounded-card shadow-elevation ring-1 ring-border sm:w-40"
        />
        <div className="grid min-w-0 gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground" dir="auto">
            {name}
            {title.year ? (
              <span className="ms-2 font-normal tabular-nums text-muted-foreground">
                {title.year}
              </span>
            ) : null}
          </h1>
          {other ? (
            <p className="text-base text-muted-foreground" dir="auto">
              {other}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={title.status} />
            {title.synthetic ? <SyntheticBadge /> : null}
            {title.rating ? (
              <Badge>
                <Star aria-hidden="true" className="size-3 fill-warning text-warning" />
                <span className="tabular-nums">
                  {format.number(title.rating, {
                    minimumFractionDigits: 1,
                    maximumFractionDigits: 1,
                  })}
                </span>
              </Badge>
            ) : null}
            {title.certification ? <Badge>{title.certification}</Badge> : null}
          </div>
          {title.genres.length > 0 ? (
            <p className="text-ui text-muted-foreground">
              {title.genres
                .map((genre) => localName(genre, i18n.language))
                .join(t("titles.listSeparator"))}
            </p>
          ) : null}
          {title.categories.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {title.categories.map((category) => (
                <Badge key={category.id} tone="primary">
                  {localName(category, i18n.language)}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-live="polite" className="grid gap-6">
      <span className="sr-only">{t("layout.loading")}</span>
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-64 w-full" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    </div>
  );
}

function TitleView({
  title,
  kind,
  onSave,
  onRefresh,
  refreshing,
  saving,
}: {
  title: TitleDetail;
  kind: TitleKind;
  onSave: (patch: PatchedMovieUpdateRequest) => Promise<unknown>;
  onRefresh: () => void;
  refreshing: boolean;
  saving: boolean;
}) {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  const [editing, setEditing] = useState(false);
  const hidden = title.status === "hidden";
  const name = localTitle(title, i18n.language);

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                {kind === "movie" ? (
                  <Link to="/movies">{t("titles.movie.title")}</Link>
                ) : (
                  <Link to="/series">{t("titles.series.title")}</Link>
                )}
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>{name}</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        {manage ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setEditing(true);
              }}
            >
              <Pencil aria-hidden="true" />
              {t("titles.detail.edit")}
            </Button>
            <Button variant="secondary" pending={refreshing} onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t("titles.detail.refresh")}
            </Button>
            <Button
              variant="secondary"
              pending={saving}
              onClick={() => {
                void onSave({ status: hidden ? "ready" : "hidden" }).then(
                  () => {
                    toast.success(hidden ? t("titles.detail.shown") : t("titles.detail.hidden"));
                  },
                  (error: unknown) => {
                    notifyError(t, error);
                  },
                );
              }}
            >
              {hidden ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
              {hidden ? t("titles.detail.show") : t("titles.detail.hide")}
            </Button>
          </div>
        ) : null}
      </div>
      <Hero title={title} />
      <div className="grid items-start gap-4 lg:grid-cols-[2fr_1fr]">
        <OverviewCard title={title} />
        <DetailsCard title={title} kind={kind} />
      </div>
      {"seasons" in title ? (
        <SeasonsCard series={title} />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{t("titles.files.title")}</CardTitle>
            <CardDescription>{t("titles.files.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {title.files.length === 0 ? (
              <EmptyState
                icon={<FileVideo />}
                title={t("titles.files.none")}
                description={t("titles.files.noneHelp")}
              />
            ) : (
              <FilesTable files={title.files} />
            )}
          </CardContent>
        </Card>
      )}
      {manage ? (
        <TitleEditorSheet
          title={title}
          kind={kind}
          open={editing}
          onOpenChange={setEditing}
          onSave={async (patch) => {
            await onSave(patch);
            toast.success(t("titles.editor.saved"));
          }}
        />
      ) : null}
    </div>
  );
}

function MovieDetail() {
  const { t } = useTranslation();
  const { titleId } = movieRoute.useParams();
  const queryClient = useQueryClient();
  const query = useMoviesRetrieve(titleId);
  const update = useMoviesUpdate();
  const refresh = useMoviesRefreshMetadata();
  usePageTitle(query.data ? query.data.title : t("titles.movie.title"));
  if (query.isPending) return <DetailSkeleton />;
  if (query.isError) {
    return (
      <QueryError
        className="min-h-[50vh]"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return (
    <TitleView
      title={query.data}
      kind="movie"
      saving={update.isPending}
      refreshing={refresh.isPending}
      onSave={async (patch) => {
        const updated = await update.mutateAsync({ id: titleId, data: patch });
        queryClient.setQueryData(getMoviesRetrieveQueryKey(titleId), updated);
        await queryClient.invalidateQueries({ queryKey: getMoviesListQueryKey() });
      }}
      onRefresh={() => {
        refresh.mutate(
          { id: titleId },
          {
            onSuccess: () => {
              toast.success(t("titles.detail.refreshQueued"));
              setTimeout(() => {
                void query.refetch();
              }, REFRESH_RECHECK_MS);
            },
            onError: (error) => {
              notifyError(t, error);
            },
          },
        );
      }}
    />
  );
}

function SeriesDetailView() {
  const { t } = useTranslation();
  const { titleId } = seriesRoute.useParams();
  const queryClient = useQueryClient();
  const query = useSeriesRetrieve(titleId);
  const update = useSeriesUpdate();
  const refresh = useSeriesRefreshMetadata();
  usePageTitle(query.data ? query.data.title : t("titles.series.title"));
  if (query.isPending) return <DetailSkeleton />;
  if (query.isError) {
    return (
      <QueryError
        className="min-h-[50vh]"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return (
    <TitleView
      title={query.data}
      kind="series"
      saving={update.isPending}
      refreshing={refresh.isPending}
      onSave={async (patch) => {
        const updated = await update.mutateAsync({ id: titleId, data: patch });
        queryClient.setQueryData(getSeriesRetrieveQueryKey(titleId), updated);
        await queryClient.invalidateQueries({ queryKey: getSeriesListQueryKey() });
      }}
      onRefresh={() => {
        refresh.mutate(
          { id: titleId },
          {
            onSuccess: () => {
              toast.success(t("titles.detail.refreshQueued"));
              setTimeout(() => {
                void query.refetch();
              }, REFRESH_RECHECK_MS);
            },
            onError: (error) => {
              notifyError(t, error);
            },
          },
        );
      }}
    />
  );
}

export function MovieDetailPage() {
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <MovieDetail />
    </RequirePermission>
  );
}

export function SeriesDetailPage() {
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <SeriesDetailView />
    </RequirePermission>
  );
}
