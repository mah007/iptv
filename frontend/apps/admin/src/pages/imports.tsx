import {
  getImportJobsListQueryKey,
  getImportSourcesListQueryKey,
  useImportJobsCancel,
  useImportJobsList,
  useImportJobsRetrieve,
  useImportSourcesDelete,
  useImportSourcesList,
  useImportSourcesTest,
  useImportsStatus,
  type ImportJob,
  type ImportSource,
} from "@smart-iptv/api";
import {
  Alert,
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
  ConfirmDialog,
  DescriptionItem,
  DescriptionList,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  PageHeader,
  ProgressBar,
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
import { Link } from "@tanstack/react-router";
import {
  CloudDownload,
  FolderSearch,
  Link2,
  Pencil,
  Plug,
  Plus,
  ScrollText,
  Trash2,
  Wifi,
  X,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { BrowserDialog } from "../features/imports/browser-dialog";
import { LinkDialog } from "../features/imports/link-dialog";
import {
  jobOrigin,
  jobWhat,
  remoteErrorText,
  useJobErrorText,
  useRemoteAnswer,
} from "../features/imports/remote";
import { SourceSheet } from "../features/imports/source-sheet";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

/** The queue refreshes every two seconds while something copies, else every ten. */
const ACTIVE_REFRESH_MS = 2000;
const IDLE_REFRESH_MS = 10_000;
const HISTORY_PAGE = 20;

function Limits() {
  const { t } = useTranslation();
  const format = useFormatters();
  const status = useImportsStatus({ query: { refetchInterval: 30_000 } });
  const data = status.data;
  if (!data) return null;
  return (
    <div className="grid gap-3">
      {data.importer_online ? null : (
        <Alert tone="warning" title={t("imports.offline.title")}>
          {t("imports.offline.description")}
        </Alert>
      )}
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
        <span>{t("imports.limits.concurrent", { count: data.max_concurrent })}</span>
        <span>{t("imports.limits.reserve", { size: format.bytes(data.reserve_bytes) })}</span>
        <span>
          {data.bandwidth_mbit > 0
            ? t("imports.limits.bandwidth", { rate: format.bitrate(data.bandwidth_mbit * 1e6) })
            : t("imports.limits.unlimited")}
        </span>
      </p>
    </div>
  );
}

function JobRow({
  job,
  manage,
  onCancel,
}: {
  job: ImportJob;
  manage: boolean;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const errorText = useJobErrorText();
  const running = job.status === "running";
  const where = job.subfolder
    ? t("imports.queue.intoFolder", { library: job.library_name, folder: job.subfolder })
    : t("imports.queue.into", { library: job.library_name });
  return (
    <li className="grid gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start gap-2">
        <div className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate font-medium">
            <bdi>{jobWhat(t, job)}</bdi>
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {job.origin === "link" ? (
              <Link2 aria-hidden="true" className="me-1 inline size-3.5" />
            ) : (
              <CloudDownload aria-hidden="true" className="me-1 inline size-3.5" />
            )}
            <bdi>{jobOrigin(job)}</bdi> · {where}
          </span>
        </div>
        <StatusBadge status={job.cancel_requested && running ? "cancelled" : job.status} />
        {manage && !job.cancel_requested ? (
          <Button
            variant="ghost"
            size="xs"
            onClick={onCancel}
            aria-label={t("imports.queue.cancelLabel", { name: jobWhat(t, job) })}
          >
            <X aria-hidden="true" />
            {t("imports.queue.cancel")}
          </Button>
        ) : null}
      </div>
      {running ? (
        <ProgressBar
          label={t(`imports.queue.phases.${job.phase}`)}
          value={job.bytes_total > 0 ? job.bytes_done : null}
          max={job.bytes_total > 0 ? job.bytes_total : 100}
          etaSeconds={job.eta_s}
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          {job.retry_at ? (
            <>
              {t("imports.queue.retrying")} <RelativeTime value={job.retry_at} />
              {job.error_code ? ` · ${errorText(job)}` : null}
            </>
          ) : (
            t("imports.queue.waiting")
          )}
        </p>
      )}
      {running && job.bytes_total > 0 ? (
        <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
          <span>
            {t("imports.queue.progress", {
              done: format.bytes(job.bytes_done),
              total: format.bytes(job.bytes_total),
            })}
          </span>
          <span>{t("imports.queue.files", { done: job.files_done, total: job.files_total })}</span>
          {job.speed_bps ? (
            <span>{t("imports.queue.speed", { rate: format.bitrate(job.speed_bps * 8) })}</span>
          ) : null}
        </p>
      ) : null}
    </li>
  );
}

function Queue({ manage }: { manage: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useImportJobsList(
    { state: "active", page_size: 100 },
    {
      query: {
        refetchInterval: (state) =>
          (state.state.data?.results.length ?? 0) > 0 ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS,
      },
    },
  );
  const cancel = useImportJobsCancel();
  const [cancelling, setCancelling] = useState<ImportJob | null>(null);
  const jobs = query.data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("imports.queue.title")}</CardTitle>
        <CardDescription>{t("imports.queue.description")}</CardDescription>
      </CardHeader>
      <CardContent aria-live="polite">
        {query.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("imports.queue.empty")}</p>
        ) : (
          <ul className="divide-y divide-border">
            {jobs.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                manage={manage}
                onCancel={() => {
                  setCancelling(job);
                }}
              />
            ))}
          </ul>
        )}
      </CardContent>
      <ConfirmDialog
        open={cancelling !== null}
        onOpenChange={(open) => {
          if (!open) setCancelling(null);
        }}
        tone="danger"
        title={t("imports.queue.cancelTitle")}
        description={t("imports.queue.cancelDescription")}
        confirmLabel={t("imports.queue.cancelConfirm")}
        onConfirm={async () => {
          if (cancelling === null) return;
          try {
            await cancel.mutateAsync({ id: cancelling.id });
            toast.success(t("imports.queue.cancelled"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getImportJobsListQueryKey() });
          }
        }}
      />
    </Card>
  );
}

function TestResult({ requestId, startedAt }: { requestId: string; startedAt: number }) {
  const { t } = useTranslation();
  const answer = useRemoteAnswer(requestId, startedAt);
  if (answer.timedOut) {
    return (
      <Alert tone="danger" title={t("imports.sources.testFailed")}>
        {t("imports.browser.noAnswer")}
      </Alert>
    );
  }
  if (answer.result === null) {
    return (
      <p role="status" className="text-xs text-muted-foreground">
        {t("imports.sources.testing")}
      </p>
    );
  }
  const result = answer.result;
  return result.ok ? (
    <Alert tone="success" title={t("imports.sources.testOk")}>
      {result.host_keys ? (
        <span className="block text-xs">
          {t("imports.sources.hostKeyPinned")}{" "}
          <bdi dir="ltr" className="block truncate font-mono">
            {result.host_keys.split(",")[0]}
          </bdi>
        </span>
      ) : (
        t("imports.sources.testOkDescription")
      )}
    </Alert>
  ) : (
    <Alert tone="danger" title={t("imports.sources.testFailed")}>
      {remoteErrorText(t, result.code)}
      {result.message ? (
        <span className="mt-1 block font-mono text-xs break-words" dir="ltr">
          {result.message}
        </span>
      ) : null}
    </Alert>
  );
}

function SourceCard({
  source,
  manage,
  onEdit,
  onBrowse,
  onDelete,
}: {
  source: ImportSource;
  manage: boolean;
  onEdit: () => void;
  onBrowse: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const test = useImportSourcesTest();
  const [request, setRequest] = useState<{ id: string; at: number } | null>(null);
  return (
    <Card className="grid content-start gap-3 p-4">
      <div className="flex items-start gap-2">
        <div className="grid min-w-0 gap-0.5">
          <span className="truncate font-medium">{source.name}</span>
          <span className="truncate text-xs text-muted-foreground">
            {t(`imports.kinds.${source.kind}`)} ·{" "}
            <bdi dir="ltr" className="font-mono">
              {source.summary}
            </bdi>
          </span>
        </div>
        {source.last_test_at ? (
          <Badge
            tone={source.last_test_status === "ok" ? "success" : "danger"}
            className="ms-auto shrink-0"
          >
            {t(`imports.testStatus.${source.last_test_status}`)}
          </Badge>
        ) : (
          <Badge tone="neutral" className="ms-auto shrink-0">
            {t("imports.sources.neverTested")}
          </Badge>
        )}
      </div>
      {source.last_test_at ? (
        <p className="text-xs text-muted-foreground">
          {t("imports.sources.lastTest")} <RelativeTime value={source.last_test_at} />
        </p>
      ) : null}
      {request ? <TestResult requestId={request.id} startedAt={request.at} /> : null}
      {manage ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onBrowse}>
            <FolderSearch aria-hidden="true" />
            {t("imports.sources.browse")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            pending={test.isPending}
            onClick={() => {
              test.mutate(
                { id: source.id },
                {
                  onSuccess: (queued) => {
                    setRequest({ id: queued.request_id, at: Date.now() });
                  },
                  onError: (error) => {
                    notifyError(t, error);
                  },
                },
              );
            }}
          >
            <Wifi aria-hidden="true" />
            {t("imports.sources.test")}
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            <Pencil aria-hidden="true" />
            {t("imports.sources.edit")}
          </Button>
          <Button size="sm" variant="ghost" className="text-danger-text" onClick={onDelete}>
            <Trash2 aria-hidden="true" />
            {t("imports.sources.delete")}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function Sources({
  manage,
  onBrowse,
}: {
  manage: boolean;
  onBrowse: (source: ImportSource) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useImportSourcesList({ query: { refetchInterval: 30_000 } });
  const remove = useImportSourcesDelete();
  const [sheet, setSheet] = useState<{ open: boolean; source: ImportSource | null }>({
    open: false,
    source: null,
  });
  const [deleting, setDeleting] = useState<ImportSource | null>(null);
  const newButton = manage ? (
    <Button
      variant="secondary"
      onClick={() => {
        setSheet({ open: true, source: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("imports.sources.new")}
    </Button>
  ) : null;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{t("imports.sources.title")}</CardTitle>
          <CardDescription>{t("imports.sources.description")}</CardDescription>
        </div>
        {(query.data?.length ?? 0) > 0 ? newButton : null}
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.length === 0 ? (
          <EmptyState
            icon={<Plug />}
            title={t("imports.sources.empty")}
            description={t("imports.sources.emptyDescription")}
            action={newButton}
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {query.data.map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                manage={manage}
                onBrowse={() => {
                  onBrowse(source);
                }}
                onEdit={() => {
                  setSheet({ open: true, source });
                }}
                onDelete={() => {
                  setDeleting(source);
                }}
              />
            ))}
          </div>
        )}
      </CardContent>
      {manage ? (
        <SourceSheet
          source={sheet.source}
          open={sheet.open}
          onOpenChange={(open) => {
            setSheet((previous) => ({ ...previous, open }));
          }}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        tone="danger"
        title={t("imports.sources.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("imports.sources.deleteDescription")}
        confirmLabel={t("imports.sources.deleteConfirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
            toast.success(t("imports.sources.deleted", { name: deleting.name }));
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getImportSourcesListQueryKey() });
          }
        }}
      />
    </Card>
  );
}

function JobDetails({ jobId, onClose }: { jobId: string | null; onClose: () => void }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const errorText = useJobErrorText();
  const query = useImportJobsRetrieve(jobId ?? "", { query: { enabled: jobId !== null } });
  const job = query.data;
  return (
    <Dialog
      open={jobId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("imports.history.detailsTitle")}</DialogTitle>
          <DialogDescription>
            {job ? format.dateTime(job.started_at ?? job.created_at) : null}
          </DialogDescription>
        </DialogHeader>
        {job === undefined ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <div className="grid gap-4">
            {job.error_code ? (
              <Alert tone={job.status === "failed" ? "danger" : "warning"} title={errorText(job)}>
                {job.error ? (
                  <span className="block font-mono text-xs break-words" dir="ltr">
                    {job.error}
                  </span>
                ) : null}
              </Alert>
            ) : null}
            <DescriptionList columns={2}>
              <DescriptionItem label={t("imports.history.from")}>
                <bdi>{job.origin === "link" ? job.url : job.source_name}</bdi>
              </DescriptionItem>
              <DescriptionItem label={t("imports.history.into")}>
                <bdi>
                  {job.subfolder ? `${job.library_name} / ${job.subfolder}` : job.library_name}
                </bdi>
              </DescriptionItem>
              <DescriptionItem label={t("imports.form.rightsHolder")}>
                <bdi>{job.rights_holder}</bdi>
              </DescriptionItem>
              <DescriptionItem label={t("imports.form.licenseRef")}>
                <bdi className="break-all">{job.license_ref}</bdi>
              </DescriptionItem>
              <DescriptionItem label={t("imports.form.licenseExpires")}>
                {job.license_expires_at
                  ? format.date(job.license_expires_at)
                  : t("imports.history.noExpiry")}
              </DescriptionItem>
              <DescriptionItem label={t("imports.history.by")}>
                {job.created_by_name || t("imports.history.system")}
              </DescriptionItem>
            </DescriptionList>
            {job.skipped.count ? (
              <p className="text-xs text-muted-foreground">
                {t("imports.history.skipped", { count: job.skipped.count })}
              </p>
            ) : null}
            <div className="grid gap-2">
              <h3 className="text-sm font-medium">{t("imports.history.filesPlaced")}</h3>
              {job.files.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("imports.history.noFiles")}</p>
              ) : (
                <ul className="grid gap-1 text-sm">
                  {job.files.map((file) => (
                    <li key={file.id} className="flex min-w-0 items-baseline gap-2">
                      <bdi dir="ltr" className="min-w-0 truncate font-mono text-xs">
                        {file.relative_path}
                      </bdi>
                      <span className="ms-auto shrink-0 text-xs text-muted-foreground tabular-nums">
                        {format.bytes(file.size)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function History() {
  const { t } = useTranslation();
  const format = useFormatters();
  const errorText = useJobErrorText();
  const query = useImportJobsList(
    { state: "finished", page_size: HISTORY_PAGE },
    { query: { refetchInterval: IDLE_REFRESH_MS } },
  );
  const [details, setDetails] = useState<string | null>(null);
  const jobs = query.data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("imports.history.title")}</CardTitle>
        <CardDescription>{t("imports.history.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("imports.history.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("imports.history.what")}</TableHead>
                  <TableHead>{t("imports.history.status")}</TableHead>
                  <TableHead>{t("imports.history.rights")}</TableHead>
                  <TableHead className="text-end">{t("imports.history.size")}</TableHead>
                  <TableHead>{t("imports.history.when")}</TableHead>
                  <TableHead>
                    <span className="sr-only">{t("imports.history.details")}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="max-w-64">
                      <span className="block truncate font-medium">
                        <bdi>{jobWhat(t, job)}</bdi>
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {job.created_by_name
                          ? t("imports.history.fromToBy", {
                              from: jobOrigin(job),
                              to: job.library_name,
                              name: job.created_by_name,
                            })
                          : t("imports.history.fromTo", {
                              from: jobOrigin(job),
                              to: job.library_name,
                            })}
                      </span>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} />
                      {job.status === "failed" ? (
                        <span className="block max-w-56 truncate text-xs text-danger-text">
                          {errorText(job)}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-56">
                      <span className="block truncate">
                        <bdi>{job.rights_holder}</bdi>
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        <bdi>{job.license_ref}</bdi>
                      </span>
                    </TableCell>
                    <TableCell className="text-end tabular-nums whitespace-nowrap">
                      {job.status === "done"
                        ? t("imports.history.filesSize", {
                            count: job.files_done,
                            size: format.bytes(job.bytes_done),
                          })
                        : "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <RelativeTime value={job.finished_at ?? job.created_at} />
                    </TableCell>
                    <TableCell className="text-end">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          setDetails(job.id);
                        }}
                      >
                        <ScrollText aria-hidden="true" />
                        {t("imports.history.details")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
      <JobDetails
        jobId={details}
        onClose={() => {
          setDetails(null);
        }}
      />
    </Card>
  );
}

function Imports() {
  const { t } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  const status = useImportsStatus();
  const [browsing, setBrowsing] = useState<{ open: boolean; source: ImportSource | null }>({
    open: false,
    source: null,
  });
  const [linkOpen, setLinkOpen] = useState(false);
  return (
    <>
      <PageHeader
        breadcrumbs={
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink asChild>
                  <Link to="/libraries">{t("libraries.title")}</Link>
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage>{t("imports.breadcrumb")}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        }
        title={t("imports.title")}
        description={t("imports.description")}
        actions={
          manage ? (
            <Button
              onClick={() => {
                setLinkOpen(true);
              }}
            >
              <Link2 aria-hidden="true" />
              {t("imports.link.button")}
            </Button>
          ) : null
        }
      />
      <div className="grid grid-cols-1 gap-6">
        <Limits />
        <Queue manage={manage} />
        <Sources
          manage={manage}
          onBrowse={(source) => {
            setBrowsing({ open: true, source });
          }}
        />
        <History />
      </div>
      {manage ? (
        <>
          <BrowserDialog
            source={browsing.source}
            status={status.data}
            open={browsing.open}
            onOpenChange={(open) => {
              setBrowsing((previous) => ({ ...previous, open }));
            }}
          />
          <LinkDialog status={status.data} open={linkOpen} onOpenChange={setLinkOpen} />
        </>
      ) : null}
    </>
  );
}

export function ImportsPage() {
  const { t } = useTranslation();
  usePageTitle(t("imports.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Imports />
    </RequirePermission>
  );
}
