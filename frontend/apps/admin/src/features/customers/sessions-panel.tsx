import {
  getSessionsListQueryKey,
  isApiError,
  useSessionsKill,
  useSessionsList,
  type Session,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  CountryFlag,
  DeviceIcon,
  EmptyState,
  LiveDuration,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  toast,
  useFormatters,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Clapperboard, History, OctagonX, Tv } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { notifyError } from "../../lib/problems";

const PAGE_SIZE = 20;
/** Live sessions refresh this often on a customer's page (the live page streams them). */
const LIVE_REFRESH_MS = 5_000;

const END_TONE: Record<string, BadgeTone> = {
  stopped: "neutral",
  kicked: "danger",
  expired: "warning",
  limit: "warning",
  idle: "neutral",
  error: "danger",
};

function durationSeconds(session: Session): number {
  const end = session.ended_at ?? session.last_heartbeat_at;
  return Math.max(0, (Date.parse(end) - Date.parse(session.started_at)) / 1000);
}

function SessionRow({ session, onKill }: { session: Session; onKill: (() => void) | null }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const TitleIcon = session.title_kind === "episode" ? Tv : Clapperboard;
  return (
    <TableRow data-state={session.is_active ? "active" : undefined}>
      <TableCell className="min-w-44 max-w-72">
        <span className="flex min-w-0 items-center gap-2">
          <TitleIcon
            role="img"
            aria-label={t(
              `sessions.kinds.${session.title_kind === "episode" ? "episode" : "movie"}`,
            )}
            className="size-4 shrink-0 text-muted-foreground"
          />
          <span className="truncate">
            <bdi>{session.title_name || t("sessions.unknownTitle")}</bdi>
          </span>
        </span>
      </TableCell>
      <TableCell className="min-w-32">
        {session.device ? (
          <span className="flex min-w-0 items-center gap-2">
            <DeviceIcon hint={session.device.app_hint} decorative />
            <span className="truncate">
              <bdi>{session.device.name || t("sessions.unknownDevice")}</bdi>
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground">{t("sessions.unknownDevice")}</span>
        )}
      </TableCell>
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              tabIndex={0}
              className="rounded-badge outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {format.dateTime(session.started_at)}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {session.ended_at
              ? t("customerSessions.endedAt", { time: format.dateTime(session.ended_at) })
              : t("customerSessions.lastSeen", {
                  time: format.dateTime(session.last_heartbeat_at),
                })}
          </TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell className="tabular-nums">
        {session.is_active ? (
          <LiveDuration since={session.started_at} />
        ) : (
          format.duration(durationSeconds(session))
        )}
      </TableCell>
      <TableCell>
        {session.is_active ? (
          <Badge tone="success" dot>
            {t("customerSessions.live")}
          </Badge>
        ) : (
          <Badge tone={END_TONE[session.end_reason] ?? "neutral"}>
            {t(`customerSessions.endReasons.${session.end_reason}`, {
              defaultValue: session.end_reason,
            })}
          </Badge>
        )}
      </TableCell>
      <TableCell>
        <span className="flex items-center gap-2">
          <CountryFlag code={session.country} />
          <span dir="ltr" className="font-mono text-xs tabular-nums">
            {session.ip ?? ""}
          </span>
        </span>
      </TableCell>
      <TableCell className="text-end">
        {onKill ? (
          <Button variant="ghost" size="xs" className="text-danger-text" onClick={onKill}>
            <OctagonX aria-hidden="true" />
            {t("sessions.kill.action")}
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

/** A customer's sessions (SPEC §8.3.3 Sessions tab): live ones first, then history with end reasons. */
export function CustomerSessions({
  customerId,
  customerName,
}: {
  customerId: string;
  customerName: string;
}) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const live = useSessionsList(
    { user: customerId, active: true, page_size: 50 },
    { query: { refetchInterval: LIVE_REFRESH_MS } },
  );
  const history = useSessionsList(
    { user: customerId, active: false, page, page_size: PAGE_SIZE },
    { query: { placeholderData: (previous) => previous } },
  );
  const kill = useSessionsKill();
  const [target, setTarget] = useState<Session | null>(null);

  async function confirmKill(session: Session): Promise<void> {
    try {
      await kill.mutateAsync({ id: session.id });
      toast.success(t("sessions.kill.done", { name: customerName }));
    } catch (error) {
      if (isApiError(error) && error.status === 409) {
        toast.info(t("sessions.kill.alreadyEnded"));
      } else {
        notifyError(t, error);
        throw error;
      }
    } finally {
      await queryClient.invalidateQueries({ queryKey: getSessionsListQueryKey() });
    }
  }

  function table(rows: readonly Session[], label: string) {
    return (
      <div className="overflow-x-auto">
        <Table aria-label={label}>
          <TableHeader>
            <TableRow>
              <TableHead>{t("customerSessions.columns.title")}</TableHead>
              <TableHead>{t("customerSessions.columns.device")}</TableHead>
              <TableHead>{t("customerSessions.columns.started")}</TableHead>
              <TableHead>{t("customerSessions.columns.duration")}</TableHead>
              <TableHead>{t("customerSessions.columns.status")}</TableHead>
              <TableHead>{t("customerSessions.columns.ip")}</TableHead>
              <TableHead>
                <span className="sr-only">{t("customerSessions.columns.actions")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                onKill={
                  session.is_active && can("sessions.kill")
                    ? () => {
                        setTarget(session);
                      }
                    : null
                }
              />
            ))}
          </TableBody>
        </Table>
      </div>
    );
  }

  const loading = (
    <div className="grid gap-2 p-4" role="status" aria-live="polite">
      <span className="sr-only">{t("layout.loading")}</span>
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
    </div>
  );

  const pages = history.data ? Math.max(1, Math.ceil(history.data.count / PAGE_SIZE)) : 1;

  return (
    <div className="grid gap-4">
      <Card className="p-0">
        <CardHeader className="p-(--density-card)">
          <CardTitle>{t("customerSessions.liveTitle")}</CardTitle>
          <CardDescription>{t("customerSessions.liveDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {live.isPending ? (
            loading
          ) : live.isError ? (
            <QueryError
              className="py-6"
              error={live.error}
              onRetry={() => {
                void live.refetch();
              }}
            />
          ) : live.data.results.length === 0 ? (
            <p className="px-(--density-card) pb-(--density-card) text-ui text-muted-foreground">
              {t("customerSessions.noneLive")}
            </p>
          ) : (
            table(live.data.results, t("customerSessions.liveTitle"))
          )}
        </CardContent>
      </Card>
      <Card className="p-0">
        <CardHeader className="p-(--density-card)">
          <CardTitle>{t("customerSessions.historyTitle")}</CardTitle>
          <CardDescription>{t("customerSessions.historyDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {history.isPending ? (
            loading
          ) : history.isError ? (
            <QueryError
              className="py-6"
              error={history.error}
              onRetry={() => {
                void history.refetch();
              }}
            />
          ) : history.data.results.length === 0 ? (
            <EmptyState
              className="py-8"
              icon={<History />}
              title={t("customerSessions.emptyHistory")}
            />
          ) : (
            <>
              {table(history.data.results, t("customerSessions.historyTitle"))}
              {pages > 1 ? (
                <nav
                  aria-label={t("customerSessions.pagination")}
                  className="flex items-center justify-between gap-3 border-t border-border p-3"
                >
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t("titles.pageOf", { page, pages })}
                  </span>
                  <span className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={page <= 1}
                      onClick={() => {
                        setPage((current) => current - 1);
                      }}
                    >
                      <ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />
                      {t("titles.previousPage")}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={page >= pages}
                      onClick={() => {
                        setPage((current) => current + 1);
                      }}
                    >
                      {t("titles.nextPage")}
                      <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
                    </Button>
                  </span>
                </nav>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        tone="danger"
        title={t("sessions.kill.title")}
        description={t("sessions.kill.description", {
          name: customerName,
          title: target?.title_name ?? "",
        })}
        confirmLabel={t("sessions.kill.confirm")}
        onConfirm={async () => {
          if (target !== null) await confirmKill(target);
        }}
      />
    </div>
  );
}
