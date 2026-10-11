import {
  getBackupsRunsQueryKey,
  getBackupsSummaryQueryKey,
  useBackupsRun,
  useBackupsRuns,
  useBackupsSummary,
  type BackupRun,
  type BackupSummary,
  type WhatEnum,
} from "@smart-iptv/api";
import {
  Alert,
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
  toast,
  useFormatters,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArchiveRestore, CloudUpload, DatabaseBackup, HardDrive, PlugZap } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

/** While a job is queued or running, the page refreshes itself. */
const ACTIVE_REFRESH_MS = 5_000;

interface DrillDetail {
  restore_s?: number;
  ready_s?: number;
  recovered_to?: string | null;
  migrations?: unknown[];
  counts?: Record<string, number>;
  verify_ok?: boolean;
  verdict?: { problems?: string[]; live_counts?: Record<string, number> };
}

function runTone(status: BackupRun["status"]): BadgeTone {
  if (status === "succeeded") return "success";
  if (status === "failed") return "danger";
  return "info";
}

function RunStatus({ run }: { run: BackupRun | null }) {
  const { t } = useTranslation();
  if (run === null) return <Badge tone="neutral">{t("backups.never")}</Badge>;
  return (
    <Badge tone={runTone(run.status)} dot>
      {t(`backups.status.${run.status}`)}
    </Badge>
  );
}

function When({ value }: { value: string | null }) {
  const { t } = useTranslation();
  return value ? <RelativeTime value={value} /> : <span>{t("backups.never")}</span>;
}

function Actions({ summary }: { summary: BackupSummary }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const run = useBackupsRun();
  if (!can("backups.run")) return null;
  const start = (what: WhatEnum) => {
    run.mutate(
      { data: { what } },
      {
        onSuccess: () => {
          toast.success(t(`backups.started.${what}`));
          void queryClient.invalidateQueries({ queryKey: getBackupsSummaryQueryKey() });
          void queryClient.invalidateQueries({ queryKey: getBackupsRunsQueryKey() });
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  };
  const disabled = !summary.agent.healthy || run.isPending;
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={() => {
          start("check");
        }}
      >
        <PlugZap aria-hidden="true" />
        {t("backups.actions.check")}
      </Button>
      <Button
        variant="secondary"
        disabled={disabled || !summary.enabled || summary.last_success_db_at === null}
        onClick={() => {
          start("drill");
        }}
      >
        <ArchiveRestore aria-hidden="true" />
        {t("backups.actions.drill")}
      </Button>
      <Button
        disabled={disabled || !summary.enabled}
        pending={run.isPending}
        onClick={() => {
          start("backup");
        }}
      >
        <DatabaseBackup aria-hidden="true" />
        {t("backups.actions.backup")}
      </Button>
    </div>
  );
}

function Warnings({ summary }: { summary: BackupSummary }) {
  const { t } = useTranslation();
  const notices: ReactNode[] = [];
  if (!summary.enabled) {
    notices.push(
      <Alert key="off" tone="warning" title={t("backups.warnings.offTitle")}>
        {t("backups.warnings.off")}
      </Alert>,
    );
  }
  if (summary.agent.seen_at === null) {
    notices.push(
      <Alert key="missing" tone="warning" title={t("backups.warnings.missingTitle")}>
        {t("backups.warnings.missing")}
      </Alert>,
    );
  } else if (!summary.agent.healthy) {
    notices.push(
      <Alert key="down" tone="danger" title={t("backups.warnings.downTitle")}>
        {t("backups.warnings.down")}
      </Alert>,
    );
  } else if (!summary.agent.passphrase) {
    notices.push(
      <Alert key="passphrase" tone="danger" title={t("backups.warnings.passphraseTitle")}>
        {t("backups.warnings.passphrase")}
      </Alert>,
    );
  }
  return notices.length > 0 ? <div className="grid gap-3">{notices}</div> : null;
}

function Overview({ summary }: { summary: BackupSummary }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const drill = summary.latest.drill;
  const drillDetail = (drill?.detail ?? {}) as DrillDetail;
  const free = summary.agent.disk_free_bytes;
  const total = summary.agent.disk_total_bytes;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        label={t("backups.tiles.database")}
        icon={<DatabaseBackup aria-hidden="true" />}
        value={<When value={summary.last_success_db_at} />}
      />
      <StatTile
        label={t("backups.tiles.drill")}
        icon={<ArchiveRestore aria-hidden="true" />}
        value={
          drill === null ? (
            t("backups.never")
          ) : drill.status === "succeeded" && drillDetail.ready_s !== undefined ? (
            t("backups.drillPassed", { time: format.duration(drillDetail.ready_s, "short") })
          ) : (
            <RunStatus run={drill} />
          )
        }
      />
      <StatTile
        label={t("backups.tiles.service")}
        icon={<CloudUpload aria-hidden="true" />}
        value={
          <Badge tone={summary.agent.healthy ? "success" : "danger"} dot>
            {summary.agent.healthy ? t("backups.service.running") : t("backups.service.stopped")}
          </Badge>
        }
      />
      <StatTile
        label={t("backups.tiles.space")}
        icon={<HardDrive aria-hidden="true" />}
        value={
          free != null && total != null
            ? t("backups.freeOf", { free: format.bytes(free), total: format.bytes(total) })
            : t("backups.unknown")
        }
      />
    </div>
  );
}

function Setup({ summary }: { summary: BackupSummary }) {
  const { t } = useTranslation();
  const can = useCan();
  const format = useFormatters();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("backups.setup.title")}</CardTitle>
        <CardDescription>{t("backups.setup.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <DescriptionList>
          <DescriptionItem label={t("backups.setup.local")}>
            <code className="font-mono text-xs" dir="ltr">
              {summary.local_dir}
            </code>
          </DescriptionItem>
          <DescriptionItem label={t("backups.setup.offsite")}>
            {summary.s3.configured ? (
              <span dir="ltr">
                {summary.s3.bucket} · {summary.s3.endpoint}
              </span>
            ) : summary.s3.enabled ? (
              t("backups.setup.offsiteIncomplete")
            ) : (
              t("backups.setup.offsiteOff")
            )}
          </DescriptionItem>
          <DescriptionItem label={t("backups.setup.archiving")}>
            <Badge tone={summary.agent.archiving ? "success" : "neutral"} dot>
              {summary.agent.archiving ? t("backups.on") : t("backups.off")}
            </Badge>
          </DescriptionItem>
          <DescriptionItem label={t("backups.setup.nextBackup")}>
            {summary.next_backup_at ? format.dateTime(summary.next_backup_at) : t("backups.off")}
          </DescriptionItem>
          <DescriptionItem label={t("backups.setup.nextDrill")}>
            {summary.next_drill_at ? format.dateTime(summary.next_drill_at) : t("backups.off")}
          </DescriptionItem>
        </DescriptionList>
        {can("settings.view") ? (
          <div>
            <Button asChild variant="secondary">
              <Link to="/settings">{t("backups.setup.edit")}</Link>
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Drill({ run }: { run: BackupRun | null }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (run === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("backups.drill.title")}</CardTitle>
          <CardDescription>{t("backups.drill.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={<ArchiveRestore />}
            title={t("backups.drill.noneTitle")}
            description={t("backups.drill.none")}
          />
        </CardContent>
      </Card>
    );
  }
  const detail = (run.detail ?? {}) as DrillDetail;
  const problems = detail.verdict?.problems ?? [];
  const live = detail.verdict?.live_counts ?? {};
  const counts = Object.entries(detail.counts ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("backups.drill.title")}</CardTitle>
        <CardDescription>{t("backups.drill.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <DescriptionList>
          <DescriptionItem label={t("backups.drill.result")}>
            <RunStatus run={run} />
          </DescriptionItem>
          <DescriptionItem label={t("backups.drill.when")}>
            <When value={run.finished_at ?? run.created_at} />
          </DescriptionItem>
          {detail.restore_s !== undefined ? (
            <DescriptionItem label={t("backups.drill.restore")}>
              {t("backups.drill.times", {
                restore: format.duration(detail.restore_s, "short"),
                ready: format.duration(detail.ready_s ?? detail.restore_s, "short"),
              })}
            </DescriptionItem>
          ) : null}
          {detail.recovered_to ? (
            <DescriptionItem label={t("backups.drill.reaches")}>
              {format.dateTime(detail.recovered_to)}
            </DescriptionItem>
          ) : null}
          {detail.verify_ok !== undefined ? (
            <DescriptionItem label={t("backups.drill.verify")}>
              <Badge tone={detail.verify_ok ? "success" : "danger"} dot>
                {detail.verify_ok ? t("backups.drill.verifyOk") : t("backups.drill.verifyBad")}
              </Badge>
            </DescriptionItem>
          ) : null}
        </DescriptionList>
        {problems.length > 0 || run.error ? (
          <Alert tone="danger" title={t("backups.drill.problems")}>
            <ul className="list-disc ps-5">
              {problems.length > 0 ? (
                problems.map((problem) => <li key={problem}>{problem}</li>)
              ) : (
                <li>{run.error.split("\n")[0]}</li>
              )}
            </ul>
          </Alert>
        ) : null}
        {counts.length > 0 ? (
          <Table aria-label={t("backups.drill.counts")}>
            <TableHeader>
              <TableRow>
                <TableHead>{t("backups.columns.table")}</TableHead>
                <TableHead className="text-end">{t("backups.columns.restored")}</TableHead>
                <TableHead className="text-end">{t("backups.columns.live")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {counts.map(([table, count]) => (
                <TableRow key={table}>
                  <TableCell>
                    <code className="font-mono text-xs" dir="ltr">
                      {table}
                    </code>
                  </TableCell>
                  <TableCell className="text-end tabular-nums">{format.number(count)}</TableCell>
                  <TableCell className="text-end tabular-nums">
                    {live[table] === undefined ? "–" : format.number(live[table])}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}

function History() {
  const { t } = useTranslation();
  const format = useFormatters();
  const runs = useBackupsRuns({ page_size: 25 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("backups.history.title")}</CardTitle>
        <CardDescription>{t("backups.history.description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {runs.isPending ? (
          <div className="grid gap-2 p-(--density-card)">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </div>
        ) : runs.isError ? (
          <QueryError
            error={runs.error}
            onRetry={() => {
              void runs.refetch();
            }}
          />
        ) : runs.data.results.length === 0 ? (
          <EmptyState
            icon={<DatabaseBackup />}
            title={t("backups.history.emptyTitle")}
            description={t("backups.history.empty")}
          />
        ) : (
          <div className="overflow-x-auto">
            <Table aria-label={t("backups.history.title")}>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("backups.columns.kind")}</TableHead>
                  <TableHead>{t("backups.columns.status")}</TableHead>
                  <TableHead>{t("backups.columns.started")}</TableHead>
                  <TableHead className="text-end">{t("backups.columns.duration")}</TableHead>
                  <TableHead className="text-end">{t("backups.columns.size")}</TableHead>
                  <TableHead>{t("backups.columns.by")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data.results.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>{t(`backups.kind.${run.kind}`)}</TableCell>
                    <TableCell>
                      <div className="grid gap-1">
                        <RunStatus run={run} />
                        {run.status === "failed" && run.error ? (
                          <span className="max-w-xs truncate text-xs text-danger-text">
                            {run.error.split("\n")[0]}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <When value={run.started_at ?? run.created_at} />
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {run.duration_s === null ? "–" : format.duration(run.duration_s, "short")}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {run.size_bytes === null ? "–" : format.bytes(run.size_bytes)}
                    </TableCell>
                    <TableCell>{run.requested_by ?? t(`backups.trigger.${run.trigger}`)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Backups() {
  const { t } = useTranslation();
  const summary = useBackupsSummary({
    query: {
      refetchInterval: (query) =>
        (query.state.data?.pending ?? 0) > 0 ? ACTIVE_REFRESH_MS : false,
    },
  });
  if (summary.isPending) {
    return (
      <div className="grid grid-cols-1 gap-(--density-page)">
        <PageHeader title={t("backups.title")} description={t("backups.description")} />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile label={t("backups.tiles.database")} value="" loading />
          <StatTile label={t("backups.tiles.drill")} value="" loading />
          <StatTile label={t("backups.tiles.service")} value="" loading />
          <StatTile label={t("backups.tiles.space")} value="" loading />
        </div>
      </div>
    );
  }
  if (summary.isError) {
    return (
      <QueryError
        error={summary.error}
        onRetry={() => {
          void summary.refetch();
        }}
        className="min-h-[50vh]"
      />
    );
  }
  const data = summary.data;
  return (
    <div className="grid grid-cols-1 gap-(--density-page)">
      <PageHeader
        title={t("backups.title")}
        description={t("backups.description")}
        actions={<Actions summary={data} />}
      />
      <Warnings summary={data} />
      {data.pending > 0 ? (
        <Alert tone="info" title={t("backups.pendingTitle")}>
          {t("backups.pending", { count: data.pending })}
        </Alert>
      ) : null}
      <Overview summary={data} />
      <div className="grid grid-cols-1 gap-(--density-page) lg:grid-cols-2">
        <Setup summary={data} />
        <Drill run={data.latest.drill} />
      </div>
      <History />
    </div>
  );
}

export function BackupsPage() {
  const { t } = useTranslation();
  usePageTitle(t("backups.title"));
  return (
    <RequirePermission permission="backups.view">
      <Backups />
    </RequirePermission>
  );
}
