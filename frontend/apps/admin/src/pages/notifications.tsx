import {
  OutboxStatus,
  getNotificationsListQueryKey,
  useNotificationsList,
  useNotificationsRetrieve,
  useNotificationsRetry,
  type NotificationsListParams,
  type Outbox,
} from "@smart-iptv/api";
import {
  Button,
  DataTable,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  FacetFilter,
  FilterBar,
  PageHeader,
  RelativeTime,
  SearchInput,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  Skeleton,
  StatusBadge,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
  toast,
  useFormatters,
  type FilterChip,
} from "@smart-iptv/ui";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { Mail, RotateCcw } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { eventLabel } from "../features/billing/labels";
import type { NotificationsSearch } from "../features/billing/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/notifications");
const helper = createDataTableColumnHelper<Outbox>();

function RetryButton({ message }: { message: Outbox }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const retry = useNotificationsRetry();
  if (!can("notifications.manage") || message.status !== "failed") return null;
  return (
    <Button
      variant="ghost"
      size="xs"
      pending={retry.isPending}
      onClick={(event) => {
        event.stopPropagation();
        retry.mutate(
          { id: message.id },
          {
            onSuccess: () => {
              toast.success(t("notifications.retried"));
              void queryClient.invalidateQueries({ queryKey: getNotificationsListQueryKey() });
            },
            onError: (error) => {
              notifyError(t, error);
            },
          },
        );
      }}
    >
      <RotateCcw aria-hidden="true" />
      {t("notifications.retry")}
    </Button>
  );
}

function Recipient({ message }: { message: Outbox }) {
  const user = message.user;
  return (
    <span className="grid min-w-0">
      {user ? (
        user.is_staff ? (
          <bdi className="truncate font-medium text-foreground">{user.name || user.username}</bdi>
        ) : (
          <Link
            to="/customers/$customerId"
            params={{ customerId: user.id }}
            className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
            onClick={(event) => {
              event.stopPropagation();
            }}
          >
            <bdi>{user.name || user.username}</bdi>
          </Link>
        )
      ) : null}
      <span className="ltr-value truncate text-xs text-muted-foreground" dir="ltr">
        {message.to_address}
      </span>
    </span>
  );
}

function useColumns() {
  const { t } = useTranslation();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("created_at", {
          id: "created_at",
          header: t("notifications.columns.when"),
          enableSorting: false,
          cell: ({ getValue }) => <RelativeTime value={getValue()} />,
        }),
        helper.accessor("user", {
          id: "recipient",
          header: t("notifications.columns.recipient"),
          enableSorting: false,
          cell: ({ row }) => <Recipient message={row.original} />,
          meta: { cellClassName: "max-w-64" },
        }),
        helper.accessor("template_key", {
          id: "event",
          header: t("notifications.columns.event"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="grid">
              <span>{eventLabel(t, row.original.template_key)}</span>
              <span className="text-xs text-muted-foreground">
                {`${t(`notifications.channels.${row.original.channel}`)} · ${t(`customers.locales.${row.original.locale}`)}`}
              </span>
            </span>
          ),
        }),
        helper.accessor("status", {
          id: "status",
          header: t("notifications.columns.status"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="grid gap-0.5">
              <StatusBadge status={row.original.status} />
              {row.original.attempts > 1 ? (
                <span className="text-xs text-muted-foreground">
                  {t("notifications.attempts", { count: row.original.attempts })}
                </span>
              ) : null}
            </span>
          ),
        }),
        helper.accessor("error", {
          id: "error",
          header: t("notifications.columns.error"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="line-clamp-2 max-w-72 text-xs text-danger-text">{getValue()}</span>
          ),
        }),
        helper.display({
          id: "actions",
          enableHiding: false,
          header: () => <span className="sr-only">{t("notifications.columns.actions")}</span>,
          cell: ({ row }) => <RetryButton message={row.original} />,
          meta: { align: "end" },
        }),
      ]),
    [t],
  );
}

function MessageSheet({ id, onClose }: { id: string | undefined; onClose: () => void }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const query = useNotificationsRetrieve(id ?? "", { query: { enabled: id !== undefined } });
  return (
    <Sheet
      open={id !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-xl">
        <SheetHeader>
          <SheetTitle>
            {query.data ? eventLabel(t, query.data.template_key) : t("notifications.detail")}
          </SheetTitle>
          <SheetDescription>{query.data?.subject ?? ""}</SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-6 [&>*]:shrink-0">
          {query.isPending ? (
            <Skeleton className="h-48 w-full" />
          ) : query.isError ? (
            <QueryError
              error={query.error}
              onRetry={() => {
                void query.refetch();
              }}
            />
          ) : (
            <>
              <DescriptionList>
                <DescriptionItem label={t("notifications.columns.status")}>
                  <StatusBadge status={query.data.status} />
                </DescriptionItem>
                <DescriptionItem label={t("notifications.columns.recipient")}>
                  <Recipient message={query.data} />
                </DescriptionItem>
                <DescriptionItem label={t("notifications.columns.when")}>
                  {format.dateTime(query.data.created_at)}
                </DescriptionItem>
                <DescriptionItem label={t("notifications.sentAt")}>
                  {query.data.sent_at ? format.dateTime(query.data.sent_at) : null}
                </DescriptionItem>
                <DescriptionItem label={t("notifications.nextAttempt")}>
                  {query.data.next_attempt_at ? (
                    <RelativeTime value={query.data.next_attempt_at} />
                  ) : null}
                </DescriptionItem>
                {query.data.error ? (
                  <DescriptionItem label={t("notifications.columns.error")}>
                    <span className="text-danger-text">{query.data.error}</span>
                  </DescriptionItem>
                ) : null}
              </DescriptionList>
              <section className="grid gap-2" aria-labelledby="message-payload">
                <h3 id="message-payload" className="text-sm font-semibold text-foreground">
                  {t("notifications.payload")}
                </h3>
                <pre
                  className="max-h-96 overflow-auto rounded-input bg-muted p-3 font-mono text-xs"
                  dir="ltr"
                >
                  {JSON.stringify(query.data.payload, null, 2)}
                </pre>
              </section>
            </>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function apiParams(search: NotificationsSearch): NotificationsListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    status: search.status ? [search.status] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

function Notifications() {
  const { t } = useTranslation();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const query = useNotificationsList(apiParams(search), {
    query: { placeholderData: keepPreviousData, refetchInterval: 15_000 },
  });

  function update(patch: SearchPatch<NotificationsSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const chips: FilterChip[] = search.status
    ? [
        {
          id: "status",
          label: t("notifications.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
          onRemove: () => {
            update({ status: undefined });
          },
        },
      ]
    : [];

  return (
    <>
      <PageHeader title={t("notifications.title")} description={t("notifications.description")} />
      <DataTable
        label={t("notifications.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(message) => message.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={[]}
        onSortingChange={() => undefined}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(message) => {
          update({ message: message.id }, { keepPage: true });
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("notifications.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <FacetFilter
                single
                title={t("notifications.filters.status")}
                options={Object.values(OutboxStatus).map((value) => ({
                  value,
                  label: t(`ui:status.${value}`),
                }))}
                selected={search.status ? [search.status] : []}
                onSelectedChange={(values) => {
                  update({ status: values[0] as NotificationsSearch["status"] });
                }}
              />
            }
            chips={chips}
            onClearAll={() => {
              update({ q: undefined, status: undefined });
            }}
          />
        }
        emptyState={
          search.q !== undefined || chips.length > 0 ? undefined : (
            <EmptyState
              icon={<Mail />}
              title={t("notifications.empty.title")}
              description={t("notifications.empty.description")}
            />
          )
        }
      />
      <MessageSheet
        id={search.message}
        onClose={() => {
          update({ message: undefined }, { keepPage: true });
        }}
      />
    </>
  );
}

export function NotificationsPage() {
  const { t } = useTranslation();
  usePageTitle(t("notifications.title"));
  return (
    <RequirePermission permission="notifications.view">
      <Notifications />
    </RequirePermission>
  );
}
