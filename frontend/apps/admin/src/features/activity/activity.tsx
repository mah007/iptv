import { useDashboardActivity, type Activity } from "@smart-iptv/api";
import {
  Button,
  DiffViewer,
  EmptyState,
  Skeleton,
  Timeline,
  TimelineItem,
  type TimelineTone,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import type { TFunction } from "i18next";
import { ChevronLeft, ChevronRight, History } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";

/** i18next reads "." as nesting; action names use it too, so they are stored with "_". */
export function actionKey(action: string): string {
  return `audit.actions.${action.replace(/\./gu, "_")}`;
}

export function targetKey(type: string): string {
  return `audit.targets.${type.replace(/\./gu, "_")}`;
}

/** "Device blocked", or the raw action for one the admin has no name for yet. */
export function actionLabel(t: TFunction, action: string): string {
  return t(actionKey(action), { defaultValue: action });
}

/** Warnings and removals stand out; creations read as good news. */
function toneOf(action: string): TimelineTone {
  if (/\.(kill|block|suspend|delete|revoke|expire)$/u.test(action)) return "danger";
  if (/\.(create|approve|reactivate|resolve|unblock)$/u.test(action)) return "success";
  if (action.startsWith("setting.") || action.startsWith("role.")) return "warning";
  return "neutral";
}

function Target({ entry, linkCustomer }: { entry: Activity; linkCustomer: boolean }) {
  const { t } = useTranslation();
  const kind = entry.target_type
    ? t(targetKey(entry.target_type), { defaultValue: entry.target_type })
    : "";
  const label = entry.target_label || kind;
  const customer = linkCustomer ? entry.customer : null;
  const link = customer ? (
    <Link
      to="/customers/$customerId"
      params={{ customerId: customer.id }}
      className="rounded-badge text-foreground underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
    >
      <bdi>{customer.name}</bdi>
    </Link>
  ) : null;
  // The customer is the target itself (their account): the name is the link.
  if (customer?.name === label) return link;
  return (
    <span>
      <bdi className="text-foreground">{label}</bdi>
      {link ? (
        <>
          {" · "}
          {link}
        </>
      ) : null}
    </span>
  );
}

/** Audit entries as a timeline: what happened, by whom, when, and to what. */
export function ActivityTimeline({
  entries,
  linkCustomer = true,
  showChanges = false,
}: {
  entries: readonly Activity[];
  /** Link entries to their customer (the dashboard); off on the customer's own page. */
  linkCustomer?: boolean;
  /** Each entry expands into the before/after diff (needs audit.view server-side). */
  showChanges?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Timeline aria-label={t("activity.label")}>
      {entries.map((entry) => (
        <TimelineItem
          key={entry.id}
          title={actionLabel(t, entry.action)}
          tone={toneOf(entry.action)}
          actor={entry.actor ? <bdi>{entry.actor.name}</bdi> : null}
          at={entry.at}
          details={
            showChanges && (entry.before !== null || entry.after !== null) ? (
              <DiffViewer before={entry.before} after={entry.after} />
            ) : undefined
          }
        >
          {entry.target_label || entry.customer ? (
            <Target entry={entry} linkCustomer={linkCustomer} />
          ) : null}
        </TimelineItem>
      ))}
    </Timeline>
  );
}

function TimelineSkeleton({ rows = 4 }: { rows?: number }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4" role="status" aria-live="polite">
      <span className="sr-only">{t("layout.loading")}</span>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex gap-3">
          <Skeleton className="size-7 rounded-full" />
          <div className="grid flex-1 gap-1.5">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** The dashboard's recent activity: the latest operational changes, refreshed every 30 s. */
export function RecentActivity({ pageSize = 8 }: { pageSize?: number }) {
  const { t } = useTranslation();
  const query = useDashboardActivity(
    { page_size: pageSize },
    { query: { refetchInterval: 30_000 } },
  );
  if (query.isPending) return <TimelineSkeleton />;
  if (query.isError) {
    return (
      <QueryError
        className="py-6"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.results.length === 0) {
    return <EmptyState className="py-6" icon={<History />} title={t("activity.empty")} />;
  }
  return <ActivityTimeline entries={query.data.results} />;
}

const PAGE_SIZE = 20;

/** Everything done to one customer, their devices, access rules and sessions, with diffs. */
export function CustomerActivity({
  customerId,
  showChanges,
}: {
  customerId: string;
  showChanges: boolean;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const query = useDashboardActivity(
    { customer: customerId, page, page_size: PAGE_SIZE },
    { query: { placeholderData: (previous) => previous } },
  );
  if (query.isPending) return <TimelineSkeleton rows={6} />;
  if (query.isError) {
    return (
      <QueryError
        className="py-10"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  const { results, count } = query.data;
  if (results.length === 0) {
    return (
      <EmptyState
        className="py-10"
        icon={<History />}
        title={t("activity.customerEmpty.title")}
        description={t("activity.customerEmpty.description")}
      />
    );
  }
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  return (
    <div className="grid gap-4">
      <ActivityTimeline entries={results} linkCustomer={false} showChanges={showChanges} />
      {pages > 1 ? (
        <nav
          aria-label={t("activity.pagination")}
          className="flex items-center justify-between gap-3 border-t border-border pt-3"
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
    </div>
  );
}
