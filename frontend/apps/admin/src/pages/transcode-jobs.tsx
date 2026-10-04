import {
  BackendEnum,
  TranscodeJobStatusEnum,
  getTranscodeJobsListQueryKey,
  isApiError,
  useTranscodeJobsCancel,
  useTranscodeJobsList,
  useTranscodeJobsPriority,
  useTranscodeJobsRetry,
  type TranscodeJob,
  type TranscodeJobsListParams,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  PageHeader,
  ProgressBar,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsList,
  TabsTrigger,
  cn,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Ban, Cpu, FileWarning, RotateCcw } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { useLiveJobs, type JobUpdate } from "../features/transcode/live-jobs";
import type { TranscodeSearch } from "../features/transcode/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/transcode");
const PAGE_SIZE = 25;
/** Read rule of the transcode API: library.view or library.manage. */
const TRANSCODE_VIEW = ["library.view", "library.manage"];
const STATUS_TABS = ["all", ...Object.values(TranscodeJobStatusEnum)] as const;
const PRIORITIES = Array.from({ length: 10 }, (_, index) => 9 - index);
const ACTIVE = new Set<string>(["queued", "running"]);

type Row = TranscodeJob;

/** The REST row with the feed's newer live fields laid over it. */
function withLive(job: TranscodeJob, update: JobUpdate | undefined): Row {
  return update ? { ...job, ...update } : job;
}

function EncoderBadge({ job }: { job: Row }) {
  const { t } = useTranslation();
  const tone = job.backend === "cpu" ? "neutral" : "violet";
  return (
    <Badge tone={tone} title={job.encoder || undefined}>
      <Cpu aria-hidden="true" className="size-3" />
      {t(`transcode.backends.${job.backend}`)}
    </Badge>
  );
}

function JobProgress({ job }: { job: Row }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (job.status === "queued") {
    return <span className="text-xs text-muted-foreground">{t("transcode.waiting")}</span>;
  }
  if (job.status !== "running") {
    return job.status === "done" ? (
      <span className="text-xs text-muted-foreground">
        {job.finished_at ? <RelativeTime value={job.finished_at} /> : null}
      </span>
    ) : null;
  }
  return (
    <div className="grid min-w-44 gap-1">
      <ProgressBar
        variant="inline"
        value={job.progress}
        etaSeconds={job.eta_s}
        aria-label={t("transcode.progressOf", { file: job.file.relative_path })}
      />
      <span className="flex flex-wrap gap-x-3 text-xs tabular-nums text-muted-foreground">
        {job.fps !== null ? (
          <span>
            {t("transcode.fps", { value: format.number(job.fps, { maximumFractionDigits: 0 }) })}
          </span>
        ) : null}
        {job.speed !== null ? (
          <span>
            {t("transcode.speed", {
              value: format.number(job.speed, { maximumFractionDigits: 1 }),
            })}
          </span>
        ) : null}
      </span>
    </div>
  );
}

function PriorityControl({
  job,
  editable,
  onChange,
}: {
  job: Row;
  editable: boolean;
  onChange: (priority: number) => void;
}) {
  const { t } = useTranslation();
  if (!editable || !ACTIVE.has(job.status)) {
    return <span className="tabular-nums">{job.priority}</span>;
  }
  return (
    <Select
      value={String(job.priority)}
      onValueChange={(value) => {
        onChange(Number(value));
      }}
    >
      <SelectTrigger
        className="h-8 w-16 tabular-nums"
        aria-label={t("transcode.priorityOf", { file: job.file.relative_path })}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {PRIORITIES.map((priority) => (
          <SelectItem key={priority} value={String(priority)}>
            {priority}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function JobsTable({
  rows,
  manage,
  onCancel,
  onRetry,
  onPriority,
  onShowError,
  retrying,
}: {
  rows: readonly Row[];
  manage: boolean;
  onCancel: (job: Row) => void;
  onRetry: (job: Row) => void;
  onPriority: (job: Row, priority: number) => void;
  onShowError: (job: Row) => void;
  retrying: string | null;
}) {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto">
      <Table aria-label={t("transcode.title")}>
        <TableHeader>
          <TableRow>
            <TableHead>{t("transcode.columns.file")}</TableHead>
            <TableHead>{t("transcode.columns.status")}</TableHead>
            <TableHead>{t("transcode.columns.progress")}</TableHead>
            <TableHead>{t("transcode.columns.encoder")}</TableHead>
            <TableHead>{t("transcode.columns.priority")}</TableHead>
            <TableHead>{t("transcode.columns.worker")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("transcode.columns.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((job) => (
            <TableRow key={job.id} data-status={job.status}>
              <TableCell className="min-w-48 max-w-64 sm:max-w-96">
                <span className="block truncate font-mono text-xs" title={job.file.relative_path}>
                  <bdi dir="ltr">{job.file.relative_path}</bdi>
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {job.title ? (
                    <>
                      <bdi>{job.title.name}</bdi>
                      {" · "}
                    </>
                  ) : null}
                  {job.file.library}
                  {job.remux ? ` · ${t("transcode.remux")}` : ""}
                </span>
              </TableCell>
              <TableCell>
                <span className="flex flex-wrap items-center gap-1">
                  <StatusBadge status={job.status} />
                  {job.attempts > 1 ? (
                    <Badge className="tabular-nums">
                      {t("transcode.attempts", { count: job.attempts })}
                    </Badge>
                  ) : null}
                </span>
                {job.status === "failed" && job.error ? (
                  <Button
                    variant="link"
                    size="xs"
                    className="mt-1 max-w-56 justify-start text-danger-text"
                    onClick={() => {
                      onShowError(job);
                    }}
                  >
                    <FileWarning aria-hidden="true" />
                    <span className="truncate">
                      <bdi>{job.error}</bdi>
                    </span>
                  </Button>
                ) : null}
              </TableCell>
              <TableCell>
                <JobProgress job={job} />
              </TableCell>
              <TableCell>
                <EncoderBadge job={job} />
              </TableCell>
              <TableCell>
                <PriorityControl
                  job={job}
                  editable={manage}
                  onChange={(priority) => {
                    onPriority(job, priority);
                  }}
                />
              </TableCell>
              <TableCell>
                <span className="font-mono text-xs text-muted-foreground">
                  <bdi dir="ltr">{job.worker_host}</bdi>
                </span>
              </TableCell>
              <TableCell className="text-end">
                {manage && ACTIVE.has(job.status) ? (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="text-danger-text"
                    onClick={() => {
                      onCancel(job);
                    }}
                  >
                    <Ban aria-hidden="true" />
                    {t("transcode.cancel.action")}
                  </Button>
                ) : null}
                {manage && (job.status === "failed" || job.status === "cancelled") ? (
                  <Button
                    variant="ghost"
                    size="xs"
                    pending={retrying === job.id}
                    onClick={() => {
                      onRetry(job);
                    }}
                  >
                    <RotateCcw aria-hidden="true" />
                    {t("transcode.retry")}
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function TranscodeJobs() {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const manage = can("library.manage");
  const queryClient = useQueryClient();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const params: TranscodeJobsListParams = compact({
    status: search.status ? [search.status] : undefined,
    backend: search.backend,
    ordering: search.status === "queued" ? (["-priority", "created_at"] as const) : undefined,
    page: search.page,
    page_size: PAGE_SIZE,
  });
  const query = useTranscodeJobsList(params, { query: { placeholderData: keepPreviousData } });
  const { updates, state } = useLiveJobs();
  const retry = useTranscodeJobsRetry();
  const cancel = useTranscodeJobsCancel();
  const priority = useTranscodeJobsPriority();
  const [cancelling, setCancelling] = useState<Row | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [errorJob, setErrorJob] = useState<Row | null>(null);

  function update(patch: SearchPatch<TranscodeSearch>): void {
    void navigate({
      search: (previous) => compact({ ...previous, page: undefined, ...patch }),
      replace: true,
    });
  }

  function refresh(): void {
    void queryClient.invalidateQueries({ queryKey: getTranscodeJobsListQueryKey() });
  }

  const rows = (query.data?.results ?? []).map((job) => withLive(job, updates.get(job.id)));
  const running = [...updates.values()].filter((job) => job.status === "running").length;
  const queued = [...updates.values()].filter((job) => job.status === "queued").length;
  const pageCount = query.data ? Math.max(1, Math.ceil(query.data.count / PAGE_SIZE)) : 1;
  const page = search.page ?? 1;
  const tab = search.status ?? "all";

  return (
    <>
      <PageHeader
        title={t("transcode.title")}
        description={t("transcode.description")}
        actions={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="info" className="tabular-nums">
              {t("transcode.runningCount", { count: running, value: format.number(running) })}
            </Badge>
            <Badge className="tabular-nums">
              {t("transcode.queuedCount", { count: queued, value: format.number(queued) })}
            </Badge>
            <Badge
              tone={state === "open" ? "success" : state === "connecting" ? "info" : "warning"}
              dot
            >
              {t(`sessions.connection.${state}`)}
            </Badge>
          </span>
        }
      />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={tab}
          onValueChange={(value) => {
            const next = Object.values(TranscodeJobStatusEnum).find((item) => item === value);
            update({ status: next });
          }}
        >
          <TabsList className="max-w-full overflow-x-auto">
            {STATUS_TABS.map((value) => (
              <TabsTrigger key={value} value={value}>
                {value === "all" ? t("transcode.all") : t(`ui:status.${value}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <Select
          value={search.backend ?? "all"}
          onValueChange={(value) => {
            update({ backend: Object.values(BackendEnum).find((item) => item === value) });
          }}
        >
          <SelectTrigger className="w-44" aria-label={t("transcode.backendFilter")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("transcode.allBackends")}</SelectItem>
            {Object.values(BackendEnum).map((backend) => (
              <SelectItem key={backend} value={backend}>
                {t(`transcode.backends.${backend}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Card className="p-0">
        {query.isPending ? (
          <div className="grid gap-2 p-4" role="status" aria-live="polite">
            <span className="sr-only">{t("layout.loading")}</span>
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Cpu />}
            title={t("transcode.empty.title")}
            description={t("transcode.empty.description")}
          />
        ) : (
          <JobsTable
            rows={rows}
            manage={manage}
            retrying={retry.isPending ? retry.variables.id : null}
            onShowError={setErrorJob}
            onCancel={(job) => {
              setCancelling(job);
              setConfirmOpen(true);
            }}
            onRetry={(job) => {
              retry.mutate(
                { id: job.id },
                {
                  onSuccess: () => {
                    toast.success(t("transcode.retried"));
                  },
                  onError: (error) => {
                    notifyError(t, error);
                  },
                  onSettled: refresh,
                },
              );
            }}
            onPriority={(job, value) => {
              priority.mutate(
                { id: job.id, data: { priority: value } },
                {
                  onSuccess: () => {
                    toast.success(t("transcode.prioritySaved", { value }));
                  },
                  onError: (error) => {
                    notifyError(t, error);
                  },
                  onSettled: refresh,
                },
              );
            }}
          />
        )}
      </Card>
      {pageCount > 1 ? (
        <div className="mt-3 flex items-center justify-end gap-2 text-ui text-muted-foreground">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1}
            onClick={() => {
              update({ page: page > 2 ? page - 1 : undefined });
            }}
          >
            {t("review.previous")}
          </Button>
          <span className="tabular-nums">
            {t("titles.pageOf", { page: format.number(page), pages: format.number(pageCount) })}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= pageCount}
            onClick={() => {
              update({ page: page + 1 });
            }}
          >
            {t("review.next")}
          </Button>
        </div>
      ) : null}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        tone="danger"
        title={t("transcode.cancel.title")}
        description={t("transcode.cancel.description", {
          file: cancelling?.file.relative_path ?? "",
        })}
        confirmLabel={t("transcode.cancel.confirm")}
        onConfirm={async () => {
          if (cancelling === null) return;
          try {
            await cancel.mutateAsync({ id: cancelling.id });
            toast.success(t("transcode.cancel.done"));
          } catch (error) {
            if (isApiError(error) && error.status === 409) {
              toast.info(t("transcode.cancel.finished"));
              return;
            }
            notifyError(t, error);
            throw error;
          } finally {
            refresh();
          }
        }}
      />
      <Dialog
        open={errorJob !== null}
        onOpenChange={(open) => {
          if (!open) setErrorJob(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("transcode.errorTitle")}</DialogTitle>
            <DialogDescription>
              <bdi dir="ltr" className="font-mono">
                {errorJob?.file.relative_path}
              </bdi>
            </DialogDescription>
          </DialogHeader>
          <p className="text-ui text-danger-text" dir="auto">
            {errorJob?.error}
          </p>
          {errorJob?.error_tail ? (
            <pre
              dir="ltr"
              className={cn(
                "max-h-[50vh] overflow-auto rounded-input bg-muted p-3 font-mono text-xs whitespace-pre-wrap text-foreground",
              )}
            >
              {errorJob.error_tail}
            </pre>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function TranscodeJobsPage() {
  const { t } = useTranslation();
  usePageTitle(t("transcode.title"));
  return (
    <RequirePermission permission={TRANSCODE_VIEW}>
      <TranscodeJobs />
    </RequirePermission>
  );
}
