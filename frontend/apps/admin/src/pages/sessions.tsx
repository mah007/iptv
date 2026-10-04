import { isApiError, useSessionsKill } from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  CountryFlag,
  EmptyState,
  LiveDuration,
  LiveIndicator,
  PageHeader,
  Skeleton,
  StatTile,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  cn,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { Activity, Clapperboard, HardDriveDownload, OctagonX, Tv, Users } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { useLiveSessions, type LiveSession } from "../features/sessions/live-sessions";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

function ConnectionBadge({ state }: { state: "connecting" | "open" | "offline" }) {
  const { t } = useTranslation();
  const tone = state === "open" ? "success" : state === "connecting" ? "info" : "warning";
  return (
    <Badge tone={tone} dot>
      {t(`sessions.connection.${state}`)}
    </Badge>
  );
}

function SessionRow({
  session,
  stopping,
  onKill,
}: {
  session: LiveSession;
  stopping: boolean;
  onKill: (() => void) | null;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const TitleIcon = session.title.kind === "episode" ? Tv : Clapperboard;
  return (
    <TableRow
      data-state={stopping ? "stopping" : undefined}
      className={cn("transition-opacity duration-200", stopping && "opacity-50")}
    >
      <TableCell className="min-w-40">
        <div className="grid min-w-0">
          <Link
            to="/customers/$customerId"
            params={{ customerId: session.user.id }}
            className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
          >
            {session.user.name || t("sessions.unknownCustomer")}
          </Link>
          <span className="truncate text-xs text-muted-foreground" dir="auto">
            {session.device.name || t("sessions.unknownDevice")}
          </span>
        </div>
      </TableCell>
      <TableCell className="min-w-40 max-w-72">
        <span className="flex min-w-0 items-center gap-2">
          <TitleIcon
            aria-label={t(
              `sessions.kinds.${session.title.kind === "episode" ? "episode" : "movie"}`,
            )}
            role="img"
            className="size-4 shrink-0 text-muted-foreground"
          />
          <span className="truncate" dir="auto">
            {session.title.name || t("sessions.unknownTitle")}
          </span>
        </span>
      </TableCell>
      <TableCell>
        {session.rendition ? (
          <Badge className="font-mono" dir="ltr">
            {session.rendition}
          </Badge>
        ) : null}
      </TableCell>
      <TableCell>
        <span className="flex items-center gap-2">
          <CountryFlag code={session.country} />
          <span dir="ltr" className="font-mono text-xs tabular-nums">
            {session.ip ?? ""}
          </span>
        </span>
      </TableCell>
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <LiveDuration since={session.started_at} />
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {t("sessions.startedAt", { time: format.dateTime(session.started_at) })}
          </TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell className="text-end tabular-nums">{format.bytes(session.bytes_sent)}</TableCell>
      <TableCell className="text-end">
        {stopping ? (
          <span className="text-xs text-muted-foreground">{t("sessions.stopping")}</span>
        ) : onKill ? (
          <Button variant="ghost" size="xs" className="text-danger-text" onClick={onKill}>
            <OctagonX aria-hidden="true" />
            {t("sessions.kill.action")}
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function Sessions() {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const { sessions, state, received } = useLiveSessions();
  const kill = useSessionsKill();
  const [target, setTarget] = useState<LiveSession | null>(null);
  const [confirming, setConfirming] = useState(false);
  /** Kills the API accepted: the row stays, faded, until the feed drops it. */
  const [stopping, setStopping] = useState<ReadonlySet<string>>(new Set());
  /** Sessions the API says already ended: hidden even if the feed still lists them. */
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());

  const rows = [...sessions.values()]
    .filter((session) => !gone.has(session.id))
    .sort((a, b) => b.started_at.localeCompare(a.started_at));
  const users = new Set(rows.map((session) => session.user.id)).size;
  const bytes = rows.reduce((total, session) => total + session.bytes_sent, 0);

  async function confirmKill(session: LiveSession): Promise<void> {
    try {
      await kill.mutateAsync({ id: session.id });
      setStopping((previous) => new Set(previous).add(session.id));
      toast.success(t("sessions.kill.done", { name: session.user.name }));
    } catch (error) {
      if (isApiError(error) && error.status === 409) {
        setGone((previous) => new Set(previous).add(session.id));
        toast.info(t("sessions.kill.alreadyEnded"));
        return;
      }
      notifyError(t, error);
      throw error;
    }
  }

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            {t("sessions.title")}
            <LiveIndicator count={rows.length} />
          </span>
        }
        description={t("sessions.description")}
        actions={<ConnectionBadge state={state} />}
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <StatTile
          label={t("sessions.stats.streams")}
          value={format.number(rows.length)}
          icon={<Activity />}
        />
        <StatTile label={t("sessions.stats.users")} value={format.number(users)} icon={<Users />} />
        <StatTile
          label={t("sessions.stats.bytes")}
          value={format.bytes(bytes)}
          icon={<HardDriveDownload />}
        />
      </div>
      <Card className="p-0">
        {!received ? (
          <div className="grid gap-2 p-4" role="status" aria-live="polite">
            <span className="sr-only">{t("layout.loading")}</span>
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Activity />}
            title={t("sessions.empty.title")}
            description={t("sessions.empty.description")}
          />
        ) : (
          <div className="overflow-x-auto">
            <Table aria-label={t("sessions.title")}>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("sessions.columns.customer")}</TableHead>
                  <TableHead>{t("sessions.columns.title")}</TableHead>
                  <TableHead>{t("sessions.columns.rendition")}</TableHead>
                  <TableHead>{t("sessions.columns.ip")}</TableHead>
                  <TableHead>{t("sessions.columns.duration")}</TableHead>
                  <TableHead className="text-end">{t("sessions.columns.bytes")}</TableHead>
                  <TableHead>
                    <span className="sr-only">{t("sessions.columns.actions")}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    stopping={stopping.has(session.id)}
                    onKill={
                      can("sessions.kill")
                        ? () => {
                            setTarget(session);
                            setConfirming(true);
                          }
                        : null
                    }
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        tone="danger"
        title={t("sessions.kill.title")}
        description={t("sessions.kill.description", {
          name: target?.user.name ?? "",
          title: target?.title.name ?? "",
        })}
        confirmLabel={t("sessions.kill.confirm")}
        onConfirm={async () => {
          if (target !== null) await confirmKill(target);
        }}
      />
    </>
  );
}

export function SessionsPage() {
  const { t } = useTranslation();
  usePageTitle(t("sessions.title"));
  return (
    <RequirePermission permission="customers.view">
      <Sessions />
    </RequirePermission>
  );
}
