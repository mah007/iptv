import {
  useLibrariesList,
  useLibrariesScan,
  useScansList,
  type Library,
  type ScanJob,
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
import { FolderOpen, HardDrive, Pencil, Plus, RefreshCw, ScrollText } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { LibrarySheet } from "../features/libraries/library-sheet";
import { isActiveScan, useScanStream, type ScanProgress } from "../features/libraries/scan-stream";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

/** Libraries refresh in the background too: scans also start on a schedule and from the watcher. */
const REFRESH_MS = 15_000;

function ScanCounters({ scan }: { scan: ScanProgress }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const counters = [
    ["found", scan.found],
    ["new", scan.new],
    ["changed", scan.changed],
    ["removed", scan.removed],
    ["errors", scan.errors],
  ] as const;
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {counters.map(([key, value]) => (
        <div key={key} className="flex items-baseline gap-1">
          <dt className="text-muted-foreground">{t(`libraries.scan.${key}`)}</dt>
          <dd
            className={
              key === "errors" && value > 0
                ? "font-medium tabular-nums text-danger-text"
                : "font-medium tabular-nums text-foreground"
            }
          >
            {format.number(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ScanPanel({ scan, live }: { scan: ScanProgress | null; live: boolean }) {
  const { t } = useTranslation();
  if (scan === null) {
    return <p className="text-ui text-muted-foreground">{t("libraries.scan.never")}</p>;
  }
  const active = isActiveScan(scan);
  const when = scan.finished_at ?? scan.started_at;
  return (
    <div className="grid gap-2" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-ui">
        <span className="font-medium text-foreground">{t("libraries.scan.last")}</span>
        <StatusBadge status={scan.status} />
        {when ? <RelativeTime value={when} className="text-muted-foreground" /> : null}
        {live ? <Badge tone="info">{t("libraries.scan.live")}</Badge> : null}
      </div>
      {active ? <ProgressBar label={t("libraries.scan.inProgress")} hideValue /> : null}
      <ScanCounters scan={scan} />
    </div>
  );
}

function LibraryCard({ library, onEdit }: { library: Library; onEdit: (() => void) | null }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const [watching, setWatching] = useState(false);
  const watch = watching || isActiveScan(library.last_scan);
  const { event, state } = useScanStream(library.id, watch, () => {
    setWatching(false);
  });
  const scanMutation = useLibrariesScan();
  const scan: ScanProgress | null =
    event !== null && (watch || event.id === library.last_scan?.id) ? event : library.last_scan;
  const stats = library.stats;
  const counts =
    library.kind === "series"
      ? [
          ["series", stats.series ?? 0],
          ["episodes", stats.episodes ?? 0],
        ]
      : library.kind === "movies"
        ? [["movies", stats.movies ?? 0]]
        : [
            ["movies", stats.movies ?? 0],
            ["series", stats.series ?? 0],
            ["episodes", stats.episodes ?? 0],
          ];

  async function startScan(): Promise<void> {
    try {
      const started = await scanMutation.mutateAsync({ id: library.id });
      setWatching(true);
      toast.success(
        started.created
          ? t("libraries.scan.started", { name: library.name })
          : t("libraries.scan.alreadyRunning", { name: library.name }),
      );
    } catch (error) {
      notifyError(t, error);
    }
  }

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="truncate">{library.name}</span>
            <Badge>{t(`libraries.kinds.${library.kind}`)}</Badge>
            {library.enabled ? null : <StatusBadge status="disabled" />}
          </CardTitle>
          <CardDescription className="flex items-center gap-1.5">
            <FolderOpen aria-hidden="true" className="size-3.5 shrink-0" />
            <span dir="ltr" className="truncate font-mono text-xs">
              {library.path}
            </span>
          </CardDescription>
        </div>
        {can("library.manage") ? (
          <div className="flex items-center gap-1.5">
            {onEdit ? (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onEdit}
                aria-label={t("libraries.edit", { name: library.name })}
              >
                <Pencil aria-hidden="true" />
              </Button>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              pending={scanMutation.isPending}
              disabled={isActiveScan(scan) || !library.enabled}
              onClick={() => {
                void startScan();
              }}
            >
              <RefreshCw aria-hidden="true" />
              {t("libraries.scanNow")}
            </Button>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="grid flex-1 content-start gap-4">
        <DescriptionList columns={2}>
          <DescriptionItem label={t("libraries.fields.policy")}>
            {t(`libraries.policies.${library.processing_policy}.title`)}
          </DescriptionItem>
          <DescriptionItem label={t("libraries.fields.interval")}>
            {t("libraries.everyMinutes", { count: library.scan_interval_min })}
          </DescriptionItem>
          <DescriptionItem label={t("libraries.fields.files")}>
            <span className="tabular-nums">
              {t("libraries.filesSize", {
                files: format.number(stats.files ?? 0),
                size: format.bytes(stats.bytes ?? 0),
              })}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("libraries.fields.titles")}>
            <span className="flex flex-wrap gap-x-3 tabular-nums">
              {counts.map(([key, value]) => (
                <span key={key}>{t(`libraries.counts.${String(key)}`, { count: value })}</span>
              ))}
            </span>
          </DescriptionItem>
          {(stats.review ?? 0) > 0 || (stats.errors ?? 0) > 0 || (stats.pending ?? 0) > 0 ? (
            <DescriptionItem label={t("libraries.fields.attention")}>
              <span className="flex flex-wrap gap-1.5">
                {(stats.review ?? 0) > 0 ? (
                  <StatusBadge
                    status="review"
                    label={t("libraries.counts.review", { count: stats.review ?? 0 })}
                  />
                ) : null}
                {(stats.pending ?? 0) > 0 ? (
                  <StatusBadge
                    status="pending"
                    label={t("libraries.counts.pending", { count: stats.pending ?? 0 })}
                  />
                ) : null}
                {(stats.errors ?? 0) > 0 ? (
                  <StatusBadge
                    status="error"
                    label={t("libraries.counts.errors", { count: stats.errors ?? 0 })}
                  />
                ) : null}
              </span>
            </DescriptionItem>
          ) : null}
        </DescriptionList>
        <div className="border-t border-border pt-3">
          <ScanPanel scan={scan} live={watch && state === "open"} />
        </div>
      </CardContent>
    </Card>
  );
}

function ScanHistory() {
  const { t } = useTranslation();
  const format = useFormatters();
  const query = useScansList({ page_size: 10 });
  const [log, setLog] = useState<ScanJob | null>(null);
  const scans = query.data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("libraries.history.title")}</CardTitle>
        <CardDescription>{t("libraries.history.description")}</CardDescription>
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
        ) : scans.length === 0 ? (
          <p className="text-ui text-muted-foreground">{t("libraries.history.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("libraries.history.library")}</TableHead>
                  <TableHead>{t("libraries.history.status")}</TableHead>
                  <TableHead>{t("libraries.history.trigger")}</TableHead>
                  <TableHead className="text-end">{t("libraries.scan.found")}</TableHead>
                  <TableHead className="text-end">{t("libraries.scan.new")}</TableHead>
                  <TableHead className="text-end">{t("libraries.scan.errors")}</TableHead>
                  <TableHead>{t("libraries.history.started")}</TableHead>
                  <TableHead>
                    <span className="sr-only">{t("libraries.history.log")}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scans.map((scan) => (
                  <TableRow key={scan.id}>
                    <TableCell className="font-medium">
                      {scan.library.name}
                      {scan.path ? (
                        <span
                          dir="ltr"
                          className="block truncate font-mono text-xs text-muted-foreground"
                        >
                          {scan.path}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={scan.status} />
                    </TableCell>
                    <TableCell>{t(`libraries.triggers.${scan.trigger}`)}</TableCell>
                    <TableCell className="text-end tabular-nums">
                      {format.number(scan.found)}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {format.number(scan.new)}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {format.number(scan.errors)}
                    </TableCell>
                    <TableCell>
                      <RelativeTime value={scan.started_at ?? scan.created_at} />
                    </TableCell>
                    <TableCell className="text-end">
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={scan.log === ""}
                        onClick={() => {
                          setLog(scan);
                        }}
                      >
                        <ScrollText aria-hidden="true" />
                        {t("libraries.history.log")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
      <Dialog
        open={log !== null}
        onOpenChange={(open) => {
          if (!open) setLog(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {t("libraries.history.logTitle", { name: log?.library.name ?? "" })}
            </DialogTitle>
            <DialogDescription>
              {log ? format.dateTime(log.started_at ?? log.created_at) : null}
            </DialogDescription>
          </DialogHeader>
          <pre
            dir="ltr"
            className="max-h-[60vh] overflow-auto rounded-input bg-muted p-3 font-mono text-xs whitespace-pre-wrap text-foreground"
          >
            {log?.log}
          </pre>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Libraries() {
  const { t } = useTranslation();
  const can = useCan();
  const query = useLibrariesList({ page_size: 100 }, { query: { refetchInterval: REFRESH_MS } });
  // The sheet keeps showing its library while it animates closed.
  const [editing, setEditing] = useState<{ open: boolean; library: Library | null }>({
    open: false,
    library: null,
  });
  const manage = can("library.manage");
  const newButton = manage ? (
    <Button
      onClick={() => {
        setEditing({ open: true, library: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("libraries.new")}
    </Button>
  ) : null;

  let body;
  if (query.isPending) {
    body = (
      <div className="grid gap-4 lg:grid-cols-2" role="status" aria-live="polite">
        <span className="sr-only">{t("layout.loading")}</span>
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  } else if (query.isError) {
    body = (
      <QueryError
        className="min-h-[40vh]"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  } else if (query.data.results.length === 0) {
    body = (
      <EmptyState
        icon={<HardDrive />}
        title={t("libraries.empty.title")}
        description={t("libraries.empty.description")}
        action={newButton}
      />
    );
  } else {
    body = (
      <div className="grid gap-4 lg:grid-cols-2">
        {query.data.results.map((library) => (
          <LibraryCard
            key={library.id}
            library={library}
            onEdit={
              manage
                ? () => {
                    setEditing({ open: true, library });
                  }
                : null
            }
          />
        ))}
      </div>
    );
  }

  return (
    <>
      <PageHeader
        title={t("libraries.title")}
        description={t("libraries.description")}
        actions={newButton}
      />
      <div className="grid gap-6">
        {body}
        <ScanHistory />
      </div>
      {manage ? (
        <LibrarySheet
          library={editing.library}
          open={editing.open}
          onOpenChange={(open) => {
            setEditing((previous) => ({ ...previous, open }));
          }}
        />
      ) : null}
    </>
  );
}

export function LibrariesPage() {
  const { t } = useTranslation();
  usePageTitle(t("libraries.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Libraries />
    </RequirePermission>
  );
}
