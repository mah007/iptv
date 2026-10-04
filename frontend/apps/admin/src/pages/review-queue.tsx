import {
  ReviewKind,
  getMoviesListQueryKey,
  getReviewQueueListQueryKey,
  getSeriesListQueryKey,
  useMetadataSearch,
  useReviewQueueList,
  useReviewQueueResolve,
  useReviewQueueSkip,
  type Candidate,
  type Review,
  type ReviewStatus,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  Input,
  Label,
  PageHeader,
  PosterImage,
  ProgressBar,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  StatusBadge,
  Tabs,
  TabsList,
  TabsTrigger,
  cn,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Check, CheckCheck, FileVideo, Search, SkipForward } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { REVIEW_REASONS, type ReviewSearch } from "../features/review/search";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/review");
const PAGE_SIZE = 25;
const STATUSES: readonly ReviewStatus[] = ["open", "resolved", "skipped"];

function reasonLabel(t: (key: string) => string, reason: string): string {
  return (REVIEW_REASONS as readonly string[]).includes(reason)
    ? t(`review.reasons.${reason}`)
    : reason;
}

/** One sub-score of a candidate (SPEC §7.2 step 4), from 0 to 1; null when not measurable. */
function ScoreRow({ label, value }: { label: string; value: number | null | undefined }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-[5.5rem_1fr] items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      {value === null || value === undefined ? (
        <span className="text-muted-foreground">{t("review.notMeasured")}</span>
      ) : (
        <ProgressBar variant="inline" value={value * 100} aria-label={label} />
      )}
    </div>
  );
}

function CandidateCard({
  candidate,
  chosen,
  onChoose,
  pending,
}: {
  candidate: Candidate;
  chosen: boolean;
  onChoose: (() => void) | null;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const poster = candidate.poster_url;
  const breakdown = candidate.breakdown;
  return (
    <Card className={cn("flex flex-col", chosen && "ring-2 ring-primary")}>
      <CardContent className="flex flex-1 flex-col gap-3 pt-(--density-card)">
        <div className="flex gap-3">
          <PosterImage
            src={poster ? () => poster : null}
            alt=""
            sizes="64px"
            className="w-16 shrink-0 rounded-sm"
          />
          <div className="grid min-w-0 content-start gap-0.5">
            <span className="font-medium text-foreground" dir="auto">
              {candidate.title}
              {candidate.year ? (
                <span className="ms-1.5 font-normal tabular-nums text-muted-foreground">
                  {candidate.year}
                </span>
              ) : null}
            </span>
            {candidate.original_title && candidate.original_title !== candidate.title ? (
              <span className="truncate text-xs text-muted-foreground" dir="auto">
                {candidate.original_title}
              </span>
            ) : null}
            <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span dir="ltr" className="font-mono">
                {t("review.tmdbId", { id: candidate.id })}
              </span>
              {candidate.runtime_min ? (
                <span>{format.duration(candidate.runtime_min * 60, "short")}</span>
              ) : null}
              {chosen ? <Badge tone="success">{t("review.chosen")}</Badge> : null}
            </span>
          </div>
        </div>
        {candidate.overview ? (
          <p className="line-clamp-3 text-xs text-muted-foreground" dir="auto">
            {candidate.overview}
          </p>
        ) : null}
        {candidate.score !== undefined ? (
          <div className="grid gap-1.5 rounded-input bg-muted/50 p-2">
            <div className="flex items-baseline justify-between text-xs">
              <span className="font-medium text-foreground">{t("review.score")}</span>
              <span className="font-semibold tabular-nums text-foreground">
                {format.percent(candidate.score, { maximumFractionDigits: 0 })}
              </span>
            </div>
            {breakdown ? (
              <>
                <ScoreRow label={t("review.breakdown.title")} value={breakdown.title} />
                <ScoreRow label={t("review.breakdown.year")} value={breakdown.year} />
                <ScoreRow label={t("review.breakdown.runtime")} value={breakdown.runtime} />
                <ScoreRow label={t("review.breakdown.popularity")} value={breakdown.popularity} />
              </>
            ) : null}
          </div>
        ) : null}
        {onChoose ? (
          <Button
            className="mt-auto"
            variant="secondary"
            size="sm"
            pending={pending}
            onClick={onChoose}
          >
            <Check aria-hidden="true" />
            {t("review.choose")}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function FileFacts({ review }: { review: Review }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const file = review.media_file;
  const parsed = review.parse_result;
  const episode =
    parsed && parsed.season !== null && parsed.episodes.length > 0
      ? `S${String(parsed.season).padStart(2, "0")}${parsed.episodes
          .map((number) => `E${String(number).padStart(2, "0")}`)
          .join("")}`
      : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("review.file.title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <DescriptionList columns={2}>
          <DescriptionItem label={t("review.file.parsedTitle")}>
            {parsed?.title ? <span dir="auto">{parsed.title}</span> : null}
          </DescriptionItem>
          <DescriptionItem label={t("review.file.year")}>
            {parsed?.year ? <span className="tabular-nums">{parsed.year}</span> : null}
          </DescriptionItem>
          {episode ? (
            <DescriptionItem label={t("review.file.episode")}>
              <span className="font-mono" dir="ltr">
                {episode}
              </span>
            </DescriptionItem>
          ) : null}
          <DescriptionItem label={t("review.file.duration")}>
            {file.duration_ms === null ? null : (
              <span className="tabular-nums">{format.duration(file.duration_ms / 1000)}</span>
            )}
          </DescriptionItem>
          <DescriptionItem label={t("review.file.video")}>
            <span dir="ltr" className="tabular-nums">
              {[
                file.width && file.height ? `${String(file.width)}×${String(file.height)}` : null,
                file.video_codec || null,
                file.hdr === "sdr" ? null : file.hdr.toUpperCase(),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("review.file.size")}>
            <span className="tabular-nums">{format.bytes(file.size)}</span>
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function ManualMatch({
  review,
  onResolve,
  pending,
}: {
  review: Review;
  onResolve: (tmdbId: number, kind: ReviewKind, label: string) => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const idField = useId();
  const kindField = useId();
  const queryField = useId();
  const [tmdbId, setTmdbId] = useState("");
  const [kind, setKind] = useState<ReviewKind>(review.kind);
  const [text, setText] = useState(review.parse_result?.title ?? "");
  const [query, setQuery] = useState<string | null>(null);
  const search = useMetadataSearch(
    { kind, query: query ?? "" },
    { query: { enabled: query !== null && query !== "" } },
  );
  const valid = /^\d+$/u.test(tmdbId.trim()) && Number(tmdbId) > 0;

  function submitId(event: { preventDefault: () => void }): void {
    event.preventDefault();
    if (!valid) return;
    onResolve(Number(tmdbId.trim()), kind, t("review.tmdbId", { id: tmdbId.trim() }));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("review.manual.title")}</CardTitle>
        <CardDescription>{t("review.manual.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5">
        <form className="flex flex-wrap items-end gap-3" onSubmit={submitId}>
          <div className="grid gap-1.5">
            <Label htmlFor={kindField}>{t("review.manual.kind")}</Label>
            <Select
              value={kind}
              onValueChange={(value) => {
                setKind(value as ReviewKind);
              }}
            >
              <SelectTrigger id={kindField} className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(ReviewKind).map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`review.kinds.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={idField}>{t("review.manual.tmdbId")}</Label>
            <Input
              id={idField}
              inputMode="numeric"
              autoComplete="off"
              dir="ltr"
              className="w-36 font-mono tabular-nums"
              value={tmdbId}
              onChange={(event) => {
                setTmdbId(event.target.value);
              }}
            />
          </div>
          <Button type="submit" disabled={!valid} pending={pending}>
            <Check aria-hidden="true" />
            {t("review.manual.match")}
          </Button>
        </form>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(text.trim());
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <div className="grid min-w-48 flex-1 gap-1.5">
              <Label htmlFor={queryField}>{t("review.manual.search")}</Label>
              <Input
                id={queryField}
                type="search"
                autoComplete="off"
                dir="auto"
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                }}
              />
            </div>
            <Button type="submit" variant="secondary" disabled={text.trim() === ""}>
              <Search aria-hidden="true" />
              {t("review.manual.searchButton")}
            </Button>
          </div>
          {query === null ? null : search.isFetching ? (
            <Skeleton className="h-24 w-full" />
          ) : search.isError ? (
            <QueryError
              error={search.error}
              onRetry={() => {
                void search.refetch();
              }}
            />
          ) : (search.data ?? []).length === 0 ? (
            <p className="text-ui text-muted-foreground">{t("review.manual.noResults")}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {(search.data ?? []).map((candidate) => (
                <CandidateCard
                  key={`${candidate.kind}-${String(candidate.id)}`}
                  candidate={candidate}
                  chosen={false}
                  pending={pending}
                  onChoose={() => {
                    onResolve(candidate.id, candidate.kind, candidate.title);
                  }}
                />
              ))}
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function ReviewDetail({
  review,
  next,
  onDone,
}: {
  review: Review;
  next: string | undefined;
  onDone: (next: string | undefined) => void;
}) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const decide = can("library.review") && review.status === "open";
  const resolve = useReviewQueueResolve();
  const skip = useReviewQueueSkip();
  const pending = resolve.isPending || skip.isPending;

  async function refresh(): Promise<void> {
    await Promise.all(
      [getReviewQueueListQueryKey(), getMoviesListQueryKey(), getSeriesListQueryKey()].map(
        (queryKey) => queryClient.invalidateQueries({ queryKey }),
      ),
    );
  }

  function choose(tmdbId: number, kind: ReviewKind, label: string): void {
    resolve.mutate(
      {
        id: review.id,
        data: kind === review.kind ? { tmdb_id: tmdbId } : { tmdb_id: tmdbId, kind },
      },
      {
        onSuccess: () => {
          toast.success(t("review.resolved", { title: label }));
          onDone(next);
          void refresh();
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  }

  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{t(`review.kinds.${review.kind}`)}</Badge>
          <StatusBadge status={review.status} />
          <Badge tone="warning">{reasonLabel(t, review.reason)}</Badge>
          <RelativeTime value={review.created_at} className="text-xs text-muted-foreground" />
        </div>
        <h2 className="break-all font-mono text-base font-medium text-foreground">
          <bdi dir="ltr">{review.media_file.relative_path}</bdi>
        </h2>
        <p className="text-ui text-muted-foreground">
          {t("review.inLibrary", { name: review.media_file.library.name })}
        </p>
        {review.status !== "open" && review.decided_at ? (
          <p className="text-ui text-muted-foreground">
            {t(`review.decided.${review.status}`, { name: review.decided_by || "—" })}{" "}
            <RelativeTime value={review.decided_at} />
          </p>
        ) : null}
        {decide ? (
          <div>
            <Button
              variant="secondary"
              size="sm"
              pending={skip.isPending}
              disabled={pending}
              onClick={() => {
                skip.mutate(
                  { id: review.id },
                  {
                    onSuccess: () => {
                      toast.success(t("review.skippedToast"));
                      onDone(next);
                      void refresh();
                    },
                    onError: (error) => {
                      notifyError(t, error);
                    },
                  },
                );
              }}
            >
              <SkipForward aria-hidden="true" className="rtl:-scale-x-100" />
              {t("review.skip")}
            </Button>
          </div>
        ) : null}
      </div>
      <FileFacts review={review} />
      <section className="grid gap-3" aria-labelledby={`${review.id}-candidates`}>
        <h3 id={`${review.id}-candidates`} className="text-sm font-semibold text-foreground">
          {t("review.candidates", { number: review.candidates.length })}
        </h3>
        {review.candidates.length === 0 ? (
          <p className="text-ui text-muted-foreground">{t("review.noCandidates")}</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {review.candidates.slice(0, 5).map((candidate) => (
              <CandidateCard
                key={`${candidate.kind}-${String(candidate.id)}`}
                candidate={candidate}
                chosen={review.chosen_provider_id === candidate.id}
                pending={resolve.isPending && resolve.variables.data.tmdb_id === candidate.id}
                onChoose={
                  decide
                    ? () => {
                        choose(candidate.id, candidate.kind, candidate.title);
                      }
                    : null
                }
              />
            ))}
          </div>
        )}
      </section>
      {decide ? (
        <ManualMatch
          key={review.id}
          review={review}
          onResolve={choose}
          pending={resolve.isPending}
        />
      ) : null}
    </div>
  );
}

function ReviewQueue() {
  const { t } = useTranslation();
  const format = useFormatters();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const status = search.status ?? "open";
  const query = useReviewQueueList(compact({ status, page: search.page, page_size: PAGE_SIZE }));

  function update(patch: SearchPatch<ReviewSearch>): void {
    void navigate({ search: (previous) => compact({ ...previous, ...patch }), replace: true });
  }

  const items = query.data?.results ?? [];
  const index = items.findIndex((item) => item.id === search.item);
  const selected = index >= 0 ? items[index] : items[0];
  const nextItem =
    selected === undefined
      ? undefined
      : (items[items.indexOf(selected) + 1] ?? items[items.indexOf(selected) - 1])?.id;
  const pageCount = query.data ? Math.max(1, Math.ceil(query.data.count / PAGE_SIZE)) : 1;
  const page = search.page ?? 1;

  return (
    <>
      <PageHeader title={t("review.title")} description={t("review.description")} />
      <Tabs
        value={status}
        onValueChange={(value) => {
          const next = STATUSES.find((candidate) => candidate === value);
          update({ status: next === "open" ? undefined : next, page: undefined, item: undefined });
        }}
        className="mb-4"
      >
        <TabsList>
          {STATUSES.map((value) => (
            <TabsTrigger key={value} value={value}>
              {t(`ui:status.${value}`)}
              {value === status && query.data ? (
                <Badge className="tabular-nums">{format.number(query.data.count)}</Badge>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {query.isPending ? (
        <div className="grid gap-4 lg:grid-cols-[20rem_1fr]" role="status" aria-live="polite">
          <span className="sr-only">{t("layout.loading")}</span>
          <Skeleton className="h-72 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : query.isError ? (
        <QueryError
          className="min-h-[40vh]"
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : items.length === 0 || selected === undefined ? (
        <EmptyState
          icon={status === "open" ? <CheckCheck /> : <FileVideo />}
          title={t(`review.empty.${status}.title`)}
          description={t(`review.empty.${status}.description`)}
        />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[20rem_1fr]">
          <nav aria-label={t("review.listLabel")} className="grid gap-2">
            <ul className="grid gap-1.5">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-current={item.id === selected.id ? "true" : undefined}
                    onClick={() => {
                      update({ item: item.id });
                    }}
                    className={cn(
                      "grid w-full gap-1 rounded-input border border-border bg-card px-3 py-2 text-start outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                      item.id === selected.id && "border-primary bg-primary/5",
                    )}
                  >
                    <span className="truncate font-mono text-xs text-foreground">
                      <bdi dir="ltr">{item.media_file.relative_path}</bdi>
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                      <span>{item.media_file.library.name}</span>
                      <span aria-hidden="true">·</span>
                      <span>{reasonLabel(t, item.reason)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {pageCount > 1 ? (
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={page <= 1}
                  onClick={() => {
                    update({ page: page > 2 ? page - 1 : undefined, item: undefined });
                  }}
                >
                  {t("review.previous")}
                </Button>
                <span className="tabular-nums">
                  {t("titles.pageOf", {
                    page: format.number(page),
                    pages: format.number(pageCount),
                  })}
                </span>
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={page >= pageCount}
                  onClick={() => {
                    update({ page: page + 1, item: undefined });
                  }}
                >
                  {t("review.next")}
                </Button>
              </div>
            ) : null}
          </nav>
          <ReviewDetail
            key={selected.id}
            review={selected}
            next={nextItem}
            onDone={(next) => {
              update({ item: next });
            }}
          />
        </div>
      )}
    </>
  );
}

export function ReviewQueuePage() {
  const { t } = useTranslation();
  usePageTitle(t("review.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <ReviewQueue />
    </RequirePermission>
  );
}
