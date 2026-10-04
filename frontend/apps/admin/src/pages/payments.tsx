import {
  PaymentProviderCode,
  PaymentStatus,
  getInvoicesDocumentUrl,
  usePaymentsList,
  usePaymentsRetrieve,
  type Payment,
  type PaymentDetail,
  type PaymentsListParams,
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
  Timeline,
  TimelineItem,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
  sortingFromOrdering,
  sortingToOrdering,
  useFormatters,
  type FilterChip,
} from "@smart-iptv/ui";
import { keepPreviousData } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { FileText, Plus, Receipt, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { RecordPaymentDialog, RefundDialog } from "../features/billing/payment-dialogs";
import { planName } from "../features/billing/pickers";
import type { PaymentsSearch } from "../features/billing/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/payments");
const helper = createDataTableColumnHelper<Payment>();

/** "29.00 SAR", with the refunded part when there is one. */
export function PaymentAmount({
  payment,
  align = "end",
}: {
  payment: Payment;
  /** `end` in table columns of numbers, `start` in a description list. */
  align?: "start" | "end";
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <span className={align === "end" ? "grid justify-items-end" : "grid justify-items-start"}>
      <span className="font-medium text-foreground tabular-nums">
        {format.money(payment.amount, payment.currency)}
      </span>
      {payment.refunded_amount > 0 ? (
        <span className="text-xs text-muted-foreground tabular-nums">
          {t("payments.refunded", {
            amount: format.money(payment.refunded_amount, payment.currency),
          })}
        </span>
      ) : null}
    </span>
  );
}

function useColumns() {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("user", {
          id: "customer",
          header: t("payments.columns.customer"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const user = getValue();
            return (
              <Link
                to="/customers/$customerId"
                params={{ customerId: user.id }}
                search={{ tab: "billing" }}
                className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
              >
                <bdi>{user.name || user.username}</bdi>
              </Link>
            );
          },
          meta: { cellClassName: "max-w-56" },
        }),
        helper.accessor("amount", {
          id: "amount",
          header: t("payments.columns.amount"),
          cell: ({ row }) => <PaymentAmount payment={row.original} />,
          meta: { align: "end" },
        }),
        helper.accessor("status", {
          id: "status",
          header: t("payments.columns.status"),
          enableSorting: false,
          cell: ({ getValue }) => <StatusBadge status={getValue()} />,
        }),
        helper.accessor("provider", {
          id: "provider",
          header: t("payments.columns.method"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="grid">
              <span>{t(`payments.providers.${row.original.provider}`)}</span>
              <span className="text-xs text-muted-foreground">
                {t(`payments.methods.${row.original.method}`)}
              </span>
            </span>
          ),
        }),
        helper.accessor("plan", {
          id: "plan",
          header: t("payments.columns.plan"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const plan = getValue();
            return plan ? <bdi>{planName(plan, i18n.language)}</bdi> : null;
          },
        }),
        helper.accessor("invoice_number", {
          id: "invoice",
          header: t("payments.columns.invoice"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="font-mono text-xs" dir="ltr">
              {getValue()}
            </span>
          ),
        }),
        helper.accessor("paid_at", {
          id: "paid_at",
          header: t("payments.columns.paid"),
          cell: ({ row }) =>
            row.original.paid_at ? (
              <span className="whitespace-nowrap">{format.dateTime(row.original.paid_at)}</span>
            ) : (
              <RelativeTime value={row.original.created_at} className="text-muted-foreground" />
            ),
        }),
      ]),
    [t, i18n.language, format],
  );
}

function PaymentSheet({ id, onClose }: { id: string | undefined; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const query = usePaymentsRetrieve(id ?? "", { query: { enabled: id !== undefined } });
  const [refunding, setRefunding] = useState(false);
  const payment: PaymentDetail | undefined = query.data;
  const refundable =
    payment !== undefined &&
    (payment.status === "succeeded" || payment.status === "partially_refunded") &&
    payment.amount > payment.refunded_amount;
  return (
    <Sheet
      open={id !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        <SheetHeader>
          <SheetTitle>{t("payments.detail.title")}</SheetTitle>
          <SheetDescription>
            {payment ? format.money(payment.amount, payment.currency) : t("layout.loading")}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-6 [&>*]:shrink-0">
          {query.isPending ? (
            <div className="grid gap-3" role="status" aria-live="polite">
              <span className="sr-only">{t("layout.loading")}</span>
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : query.isError ? (
            <QueryError
              error={query.error}
              onRetry={() => {
                void query.refetch();
              }}
            />
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                {can("billing.refund") && refundable ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setRefunding(true);
                    }}
                  >
                    <Undo2 aria-hidden="true" className="rtl:-scale-x-100" />
                    {t("payments.refund.action")}
                  </Button>
                ) : null}
                {query.data.invoice_id ? (
                  <Button asChild variant="secondary">
                    <a
                      href={getInvoicesDocumentUrl(query.data.invoice_id, {
                        locale: i18n.language === "ar" ? "ar" : "en",
                      })}
                      target="_blank"
                      rel="noopener"
                    >
                      <FileText aria-hidden="true" />
                      {t("payments.detail.invoice", { number: query.data.invoice_number })}
                    </a>
                  </Button>
                ) : null}
              </div>
              <DescriptionList>
                <DescriptionItem label={t("payments.columns.status")}>
                  <StatusBadge status={query.data.status} />
                </DescriptionItem>
                <DescriptionItem label={t("payments.columns.customer")}>
                  <Link
                    to="/customers/$customerId"
                    params={{ customerId: query.data.user.id }}
                    className="font-medium hover:underline"
                  >
                    <bdi>{query.data.user.name || query.data.user.username}</bdi>
                  </Link>
                </DescriptionItem>
                <DescriptionItem label={t("payments.columns.amount")}>
                  <PaymentAmount payment={query.data} align="start" />
                </DescriptionItem>
                <DescriptionItem label={t("payments.columns.method")}>
                  {`${t(`payments.providers.${query.data.provider}`)} · ${t(`payments.methods.${query.data.method}`)}`}
                </DescriptionItem>
                <DescriptionItem label={t("payments.columns.plan")}>
                  {query.data.plan ? <bdi>{planName(query.data.plan, i18n.language)}</bdi> : null}
                </DescriptionItem>
                <DescriptionItem label={t("payments.detail.reference")}>
                  {query.data.reference ? <bdi>{query.data.reference}</bdi> : null}
                </DescriptionItem>
                <DescriptionItem label={t("payments.detail.providerRef")}>
                  {query.data.provider_ref ? (
                    <code className="font-mono text-xs" dir="ltr">
                      {query.data.provider_ref}
                    </code>
                  ) : null}
                </DescriptionItem>
                <DescriptionItem label={t("payments.columns.paid")}>
                  {query.data.paid_at ? format.dateTime(query.data.paid_at) : null}
                </DescriptionItem>
                <DescriptionItem label={t("payments.detail.recordedBy")}>
                  {query.data.recorded_by ? <bdi>{query.data.recorded_by}</bdi> : null}
                </DescriptionItem>
                {query.data.failure_reason ? (
                  <DescriptionItem label={t("payments.detail.failure")}>
                    <span className="text-danger-text">{query.data.failure_reason}</span>
                  </DescriptionItem>
                ) : null}
              </DescriptionList>
              <section className="grid gap-3" aria-labelledby="payment-events">
                <h3 id="payment-events" className="text-sm font-semibold text-foreground">
                  {t("payments.detail.events")}
                </h3>
                {query.data.webhook_events.length === 0 ? (
                  <p className="text-ui text-muted-foreground">{t("payments.detail.noEvents")}</p>
                ) : (
                  <Timeline>
                    {query.data.webhook_events.map((event) => (
                      <TimelineItem
                        key={event.id}
                        title={
                          <code className="font-mono text-xs" dir="ltr">
                            {event.type}
                          </code>
                        }
                        tone={event.error ? "danger" : event.processed_at ? "success" : "warning"}
                        at={event.created_at}
                        details={
                          <pre
                            className="max-h-64 overflow-auto rounded-input bg-muted p-3 font-mono text-xs"
                            dir="ltr"
                          >
                            {JSON.stringify(event.payload, null, 2)}
                          </pre>
                        }
                      >
                        {event.error ? (
                          <span className="text-danger-text">{event.error}</span>
                        ) : event.processed_at ? (
                          t("payments.detail.processed")
                        ) : (
                          t("payments.detail.unprocessed")
                        )}
                      </TimelineItem>
                    ))}
                  </Timeline>
                )}
              </section>
              <details className="group rounded-input border border-border">
                <summary className="cursor-pointer px-3 py-2 text-ui font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {t("payments.detail.raw")}
                </summary>
                <pre
                  className="max-h-96 overflow-auto border-t border-border bg-muted p-3 font-mono text-xs"
                  dir="ltr"
                >
                  {JSON.stringify(query.data.raw, null, 2)}
                </pre>
              </details>
              {refunding ? (
                <RefundDialog payment={query.data} open={refunding} onOpenChange={setRefunding} />
              ) : null}
            </>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function apiParams(search: PaymentsSearch): PaymentsListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    status: search.status ? [search.status] : undefined,
    provider: search.provider,
    ordering: search.ordering ? [search.ordering] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

function Payments() {
  const { t } = useTranslation();
  const can = useCan();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const query = usePaymentsList(apiParams(search), {
    query: { placeholderData: keepPreviousData },
  });

  function update(patch: SearchPatch<PaymentsSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const chips: FilterChip[] = [];
  if (search.status) {
    chips.push({
      id: "status",
      label: t("payments.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
      onRemove: () => {
        update({ status: undefined });
      },
    });
  }
  if (search.provider) {
    chips.push({
      id: "provider",
      label: t("payments.filters.chipProvider", {
        value: t(`payments.providers.${search.provider}`),
      }),
      onRemove: () => {
        update({ provider: undefined });
      },
    });
  }
  const filtered = search.q !== undefined || chips.length > 0;
  const recordButton = can("billing.manage") ? (
    <Button asChild>
      <Link to="/payments" search={{ ...search, record: true }}>
        <Plus aria-hidden="true" />
        {t("payments.record.button")}
      </Link>
    </Button>
  ) : null;

  return (
    <>
      <PageHeader
        title={t("payments.title")}
        description={t("payments.description")}
        actions={recordButton}
      />
      <DataTable
        label={t("payments.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(payment) => payment.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as PaymentsSearch["ordering"] });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(payment) => {
          update({ payment: payment.id }, { keepPage: true });
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("payments.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <>
                <FacetFilter
                  single
                  title={t("payments.filters.status")}
                  options={Object.values(PaymentStatus).map((value) => ({
                    value,
                    label: t(`ui:status.${value}`),
                  }))}
                  selected={search.status ? [search.status] : []}
                  onSelectedChange={(values) => {
                    update({ status: values[0] as PaymentsSearch["status"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("payments.filters.provider")}
                  options={Object.values(PaymentProviderCode).map((value) => ({
                    value,
                    label: t(`payments.providers.${value}`),
                  }))}
                  selected={search.provider ? [search.provider] : []}
                  onSelectedChange={(values) => {
                    update({ provider: values[0] as PaymentsSearch["provider"] });
                  }}
                />
              </>
            }
            chips={chips}
            onClearAll={() => {
              update({ q: undefined, status: undefined, provider: undefined });
            }}
          />
        }
        emptyState={
          filtered ? undefined : (
            <EmptyState
              icon={<Receipt />}
              title={t("payments.empty.title")}
              description={t("payments.empty.description")}
              action={recordButton}
            />
          )
        }
      />
      <PaymentSheet
        id={search.payment}
        onClose={() => {
          update({ payment: undefined }, { keepPage: true });
        }}
      />
      <RecordPaymentDialog
        open={search.record === true && can("billing.manage")}
        onOpenChange={(open) => {
          if (!open) update({ record: undefined }, { keepPage: true });
        }}
      />
    </>
  );
}

export function PaymentsPage() {
  const { t } = useTranslation();
  usePageTitle(t("payments.title"));
  return (
    <RequirePermission permission="billing.view">
      <Payments />
    </RequirePermission>
  );
}
