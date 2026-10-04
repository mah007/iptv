import {
  getRenditionsCleanupReportQueryKey,
  getStorageUsageQueryKey,
  isApiError,
  useRenditionsCleanupReport,
  useRenditionsCleanupRun,
  useStorageUsage,
  type CleanupReport,
  type LibraryUsage,
  type StorageUsage,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ChartLegend,
  ConfirmDialog,
  EmptyState,
  PageHeader,
  RelativeTime,
  Skeleton,
  StatTile,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TimeSeriesChart,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database, Eraser, FileVideo, HardDrive, Layers, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { localTitle } from "../features/catalog/artwork";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

/** How often the cleanup report is re-read while a run is on the worker. */
const CLEANUP_POLL_MS = 3_000;
/** Give up waiting for a run after this long; the report can still be refreshed by hand. */
const CLEANUP_POLL_LIMIT_MS = 120_000;
/** Removals listed under the report; the rest are summed in its totals. */
const REMOVALS_SHOWN = 50;

/** One bar split into sources (muted) and renditions (accent), as a share of `max`. */
function SplitBar({
  sources,
  renditions,
  max,
}: {
  sources: number;
  renditions: number;
  max: number;
}) {
  const scale = max > 0 ? 100 / max : 0;
  return (
    <div aria-hidden="true" className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
      <span
        className="h-full bg-(--chart-muted)"
        style={{ inlineSize: `${String(sources * scale)}%` }}
      />
      <span
        className="h-full bg-(--chart-accent)"
        style={{ inlineSize: `${String(renditions * scale)}%` }}
      />
    </div>
  );
}

function Totals({ data }: { data: StorageUsage | undefined }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const loading = data === undefined;
  const growth = data?.growth ?? [];
  const total = (data?.sources ?? 0) + (data?.renditions ?? 0);
  // Change over the 90 days, as a share of where they started (none from an empty disk).
  const start = growth[0] ? growth[0].sources + growth[0].renditions : 0;
  const delta = start > 0 ? (total - start) / start : undefined;
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        loading={loading}
        icon={<Database />}
        label={t("storage.total")}
        value={format.bytes(total)}
        delta={delta}
        deltaLabel={delta === undefined ? undefined : t("storage.since90")}
        sparkline={growth.map((point) => point.sources + point.renditions)}
      />
      <StatTile
        loading={loading}
        icon={<HardDrive />}
        label={t("storage.sources")}
        value={format.bytes(data?.sources ?? 0)}
      />
      <StatTile
        loading={loading}
        icon={<Layers />}
        label={t("storage.renditions")}
        value={format.bytes(data?.renditions ?? 0)}
      />
      <StatTile
        loading={loading}
        icon={<FileVideo />}
        label={t("storage.files")}
        value={format.number(data?.files ?? 0)}
      />
    </div>
  );
}

function LibrariesCard({ libraries }: { libraries: readonly LibraryUsage[] }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const max = Math.max(0, ...libraries.map((item) => item.sources + item.renditions));
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{t("storage.byLibrary.title")}</CardTitle>
          <CardDescription>{t("storage.byLibrary.description")}</CardDescription>
        </div>
        <ChartLegend
          series={[
            { key: "sources", label: t("storage.sources"), tone: "muted" },
            { key: "renditions", label: t("storage.renditions"), tone: "accent" },
          ]}
        />
      </CardHeader>
      <CardContent>
        {libraries.length === 0 ? (
          <EmptyState className="py-8" icon={<HardDrive />} title={t("storage.byLibrary.empty")} />
        ) : (
          <ul className="grid gap-4">
            {libraries.map((item) => (
              <li key={item.id} className="grid gap-1.5">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-ui">
                  <bdi className="font-medium text-foreground">{item.name}</bdi>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {t("storage.byLibrary.split", {
                      sources: format.bytes(item.sources),
                      renditions: format.bytes(item.renditions),
                      files: format.number(item.files),
                    })}
                  </span>
                </div>
                <SplitBar sources={item.sources} renditions={item.renditions} max={max} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function GrowthCard({ data }: { data: StorageUsage | undefined }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const day = useMemo(
    () =>
      new Intl.DateTimeFormat(format.locale, { day: "numeric", month: "short", timeZone: "UTC" }),
    [format.locale],
  );
  const longDay = useMemo(
    () => new Intl.DateTimeFormat(format.locale, { dateStyle: "full", timeZone: "UTC" }),
    [format.locale],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("storage.growth.title")}</CardTitle>
        <CardDescription>{t("storage.growth.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <TimeSeriesChart
          label={t("storage.growth.title")}
          valueLabel={t("storage.total")}
          loading={data === undefined}
          data={(data?.growth ?? []).map((point) => ({
            at: point.date,
            value: point.sources + point.renditions,
          }))}
          formatTick={(at) => day.format(new Date(at))}
          formatTooltip={(at) => longDay.format(new Date(at))}
          formatValue={(value) => format.bytes(value)}
          yAxisWidth={64}
        />
      </CardContent>
    </Card>
  );
}

function LargestCard({ data }: { data: StorageUsage }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("storage.largest.title")}</CardTitle>
        <CardDescription>{t("storage.largest.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {data.largest.length === 0 ? (
          <EmptyState className="py-8" icon={<FileVideo />} title={t("storage.largest.empty")} />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("storage.largest.columns.title")}</TableHead>
                  <TableHead className="text-end">{t("storage.sources")}</TableHead>
                  <TableHead className="text-end">{t("storage.renditions")}</TableHead>
                  <TableHead className="text-end">{t("storage.largest.columns.total")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.largest.map((item) => {
                  const name = <bdi>{localTitle(item, i18n.language)}</bdi>;
                  return (
                    <TableRow key={`${item.kind}:${item.id}`}>
                      <TableCell className="max-w-72">
                        <span className="flex min-w-0 items-center gap-2">
                          {item.kind === "movie" ? (
                            <Link
                              to="/movies/$titleId"
                              params={{ titleId: item.id }}
                              className="truncate font-medium text-foreground hover:underline"
                            >
                              {name}
                            </Link>
                          ) : (
                            <Link
                              to="/series/$titleId"
                              params={{ titleId: item.id }}
                              className="truncate font-medium text-foreground hover:underline"
                            >
                              {name}
                            </Link>
                          )}
                          <Badge className="shrink-0">
                            {item.kind === "movie"
                              ? t("storage.largest.movie")
                              : t("storage.largest.series")}
                          </Badge>
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {t("storage.largest.files", { count: item.files })}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-end tabular-nums">
                        {format.bytes(item.sources)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-end tabular-nums">
                        {format.bytes(item.renditions)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-end font-medium tabular-nums">
                        {format.bytes(item.sources + item.renditions)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ReportView({ report }: { report: CleanupReport }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <div className="grid gap-3">
      <p className="flex flex-wrap items-center gap-2 text-ui">
        <Badge tone={report.dry_run ? "info" : "success"}>
          {report.dry_run ? t("storage.cleanup.dryRun") : t("storage.cleanup.deletedRun")}
        </Badge>
        <span className="text-muted-foreground">
          <RelativeTime value={report.finished_at} />
        </span>
        <span className="font-medium tabular-nums">
          {report.dry_run
            ? t("storage.cleanup.wouldFree", {
                count: report.entries,
                size: format.bytes(report.bytes),
              })
            : t("storage.cleanup.freed", {
                count: report.entries,
                size: format.bytes(report.bytes),
              })}
        </span>
      </p>
      {report.removals.length > 0 ? (
        <div className="max-h-96 overflow-auto rounded-input border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("storage.cleanup.columns.entry")}</TableHead>
                <TableHead>{t("storage.cleanup.columns.reason")}</TableHead>
                <TableHead className="text-end">{t("storage.cleanup.columns.size")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.removals.slice(0, REMOVALS_SHOWN).map((removal) => (
                <TableRow key={removal.path}>
                  <TableCell className="max-w-80">
                    <bdi
                      dir="ltr"
                      className="block truncate font-mono text-xs"
                      title={removal.path}
                    >
                      {removal.path}
                    </bdi>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {t(`storage.cleanup.reasons.${removal.reason}`)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-end tabular-nums">
                    {format.bytes(removal.bytes)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
      {report.removals.length > REMOVALS_SHOWN ? (
        <p className="text-xs text-muted-foreground">
          {t("storage.cleanup.more", { count: report.removals.length - REMOVALS_SHOWN })}
        </p>
      ) : null}
    </div>
  );
}

/** Orphaned renditions cleanup (SPEC §8.3 Storage): a dry run first, then the deletion. */
function CleanupCard() {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const manage = can("library.manage");
  const run = useRenditionsCleanupRun();
  // While a run is on the worker: the finish time of the report it will replace.
  const [waitingFor, setWaitingFor] = useState<{ after: string | null } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const report = useRenditionsCleanupReport({
    query: {
      retry: (count, error) => !(isApiError(error) && error.status === 404) && count < 2,
      refetchInterval: (query) =>
        waitingFor !== null && (query.state.data?.finished_at ?? null) === waitingFor.after
          ? CLEANUP_POLL_MS
          : false,
    },
  });
  const missing = report.isError && isApiError(report.error) && report.error.status === 404;
  const latest = report.data;
  const waiting = waitingFor !== null && (latest?.finished_at ?? null) === waitingFor.after;

  // A new report means files may have gone: refresh the usage above.
  const finishedAt = latest?.finished_at;
  const seen = useRef(finishedAt);
  useEffect(() => {
    if (finishedAt !== undefined && seen.current !== undefined && finishedAt !== seen.current) {
      void queryClient.invalidateQueries({ queryKey: getStorageUsageQueryKey() });
    }
    seen.current = finishedAt;
  }, [finishedAt, queryClient]);

  function start(dryRun: boolean): Promise<void> {
    return run.mutateAsync({ data: { dry_run: dryRun } }).then(
      () => {
        setWaitingFor({ after: latest?.finished_at ?? null });
        // Stop polling after a while; the report can still be refreshed by reloading.
        setTimeout(() => {
          setWaitingFor(null);
        }, CLEANUP_POLL_LIMIT_MS);
        toast.success(dryRun ? t("storage.cleanup.dryRunQueued") : t("storage.cleanup.runQueued"));
        void queryClient.invalidateQueries({ queryKey: getRenditionsCleanupReportQueryKey() });
      },
      (error: unknown) => {
        notifyError(t, error);
        throw error;
      },
    );
  }

  const deletable = latest?.dry_run === true && latest.entries > 0;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{t("storage.cleanup.title")}</CardTitle>
          <CardDescription>{t("storage.cleanup.description")}</CardDescription>
        </div>
        {manage ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              pending={(run.isPending && !confirming) || waiting}
              onClick={() => {
                void start(true).catch(() => undefined);
              }}
            >
              <ScanSearch aria-hidden="true" />
              {t("storage.cleanup.runDry")}
            </Button>
            <Button
              variant="danger"
              disabled={!deletable || waiting}
              onClick={() => {
                setConfirming(true);
              }}
            >
              <Eraser aria-hidden="true" />
              {t("storage.cleanup.run")}
            </Button>
          </div>
        ) : null}
      </CardHeader>
      <CardContent>
        {report.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : missing ? (
          <p className="text-ui text-muted-foreground">{t("storage.cleanup.none")}</p>
        ) : report.isError ? (
          <QueryError
            error={report.error}
            onRetry={() => {
              void report.refetch();
            }}
          />
        ) : (
          <ReportView report={report.data} />
        )}
      </CardContent>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        tone="danger"
        title={t("storage.cleanup.confirmTitle")}
        description={t("storage.cleanup.confirmDescription", { count: latest?.entries ?? 0 })}
        confirmLabel={t("storage.cleanup.run")}
        onConfirm={() => start(false)}
      />
    </Card>
  );
}

function Storage() {
  const { t } = useTranslation();
  const query = useStorageUsage();
  const data = query.data;
  return (
    <div className="grid grid-cols-1 gap-6">
      <PageHeader
        className="pb-0"
        title={t("storage.title")}
        description={
          data ? (
            <span>
              {t("storage.description")} {t("dashboard.asOf")} <RelativeTime value={data.as_of} />
            </span>
          ) : (
            t("storage.description")
          )
        }
      />
      {query.isError ? (
        <Card>
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        </Card>
      ) : (
        <>
          <Totals data={data} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <GrowthCard data={data} />
            {data ? (
              <LibrariesCard libraries={data.libraries} />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </div>
          {data ? <LargestCard data={data} /> : <Skeleton className="h-64 w-full" />}
        </>
      )}
      <CleanupCard />
    </div>
  );
}

export function StoragePage() {
  const { t } = useTranslation();
  usePageTitle(t("storage.title"));
  return (
    <RequirePermission permission={["library.view", "library.manage"]}>
      <Storage />
    </RequirePermission>
  );
}
