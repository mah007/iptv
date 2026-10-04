import {
  InvoiceStatus,
  getInvoicesDocumentUrl,
  useInvoicesDocument,
  useInvoicesList,
  useInvoicesRetrieve,
  type Invoice,
  type InvoicesListParams,
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
  SearchInput,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsList,
  TabsTrigger,
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
import { ExternalLink, FileText } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { planName } from "../features/billing/pickers";
import type { InvoicesSearch } from "../features/billing/search";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/invoices");
const helper = createDataTableColumnHelper<Invoice>();

interface InvoiceLine {
  description_en?: string;
  description_ar?: string;
  quantity?: number;
  unit_amount?: number;
  amount?: number;
}

function linesOf(invoice: Invoice): InvoiceLine[] {
  return Array.isArray(invoice.lines) ? (invoice.lines as InvoiceLine[]) : [];
}

function useColumns() {
  const { t } = useTranslation();
  const format = useFormatters();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("number", {
          id: "number",
          header: t("invoices.columns.number"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="font-mono text-xs" dir="ltr">
              {getValue() ?? t("invoices.draft")}
            </span>
          ),
        }),
        helper.accessor("user", {
          id: "customer",
          header: t("invoices.columns.customer"),
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
        helper.accessor("total", {
          id: "total",
          header: t("invoices.columns.total"),
          cell: ({ row }) => (
            <span className="font-medium tabular-nums">
              {format.money(row.original.total, row.original.currency)}
            </span>
          ),
          meta: { align: "end" },
        }),
        helper.accessor("status", {
          id: "status",
          header: t("invoices.columns.status"),
          enableSorting: false,
          cell: ({ getValue }) => <StatusBadge status={getValue()} />,
        }),
        helper.accessor("issued_at", {
          id: "issued_at",
          header: t("invoices.columns.issued"),
          cell: ({ getValue }) => {
            const value = getValue();
            return value ? <span className="whitespace-nowrap">{format.date(value)}</span> : null;
          },
        }),
      ]),
    [t, format],
  );
}

function InvoiceSheet({ id, onClose }: { id: string | undefined; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const [locale, setLocale] = useState<"en" | "ar">(i18n.language === "ar" ? "ar" : "en");
  const query = useInvoicesRetrieve(id ?? "", { query: { enabled: id !== undefined } });
  const preview = useInvoicesDocument(
    id ?? "",
    { locale },
    { query: { enabled: id !== undefined } },
  );
  const invoice = query.data;
  return (
    <Sheet
      open={id !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-3xl">
        <SheetHeader>
          <SheetTitle>
            {invoice?.number
              ? t("invoices.detail.title", { number: invoice.number })
              : t("invoices.detail.draftTitle")}
          </SheetTitle>
          <SheetDescription>
            {invoice ? format.money(invoice.total, invoice.currency) : t("layout.loading")}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-6 [&>*]:shrink-0">
          {query.isPending ? (
            <div className="grid gap-3" role="status" aria-live="polite">
              <span className="sr-only">{t("layout.loading")}</span>
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-64 w-full" />
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
              <DescriptionList>
                <DescriptionItem label={t("invoices.columns.status")}>
                  <StatusBadge status={query.data.status} />
                </DescriptionItem>
                <DescriptionItem label={t("invoices.columns.customer")}>
                  <Link
                    to="/customers/$customerId"
                    params={{ customerId: query.data.user.id }}
                    className="font-medium hover:underline"
                  >
                    <bdi>{query.data.user.name || query.data.user.username}</bdi>
                  </Link>
                </DescriptionItem>
                <DescriptionItem label={t("invoices.detail.plan")}>
                  <bdi>{planName(query.data.plan, i18n.language)}</bdi>
                </DescriptionItem>
                <DescriptionItem label={t("invoices.columns.issued")}>
                  {query.data.issued_at ? format.dateTime(query.data.issued_at) : null}
                </DescriptionItem>
              </DescriptionList>
              <div className="overflow-x-auto rounded-input border border-border">
                <Table aria-label={t("invoices.detail.lines")}>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("invoices.detail.description")}</TableHead>
                      <TableHead className="text-end">{t("invoices.detail.quantity")}</TableHead>
                      <TableHead className="text-end">{t("invoices.detail.amount")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {linesOf(query.data).map((line, index) => (
                      <TableRow key={`${String(index)}-${line.description_en ?? ""}`}>
                        <TableCell>
                          <bdi>
                            {(i18n.language === "ar" ? line.description_ar : undefined) ??
                              line.description_en}
                          </bdi>
                        </TableCell>
                        <TableCell className="text-end tabular-nums">
                          {format.number(line.quantity ?? 1)}
                        </TableCell>
                        <TableCell className="text-end tabular-nums">
                          {format.money(line.amount ?? 0, query.data.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                  <TableFooter>
                    <TableRow>
                      <TableCell colSpan={2}>{t("invoices.detail.subtotal")}</TableCell>
                      <TableCell className="text-end tabular-nums">
                        {format.money(query.data.subtotal, query.data.currency)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={2}>
                        {t("invoices.detail.vat", {
                          rate: format.percent(Number(query.data.vat_rate)),
                        })}
                      </TableCell>
                      <TableCell className="text-end tabular-nums">
                        {format.money(query.data.vat_amount, query.data.currency)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={2} className="font-semibold">
                        {t("invoices.columns.total")}
                      </TableCell>
                      <TableCell className="text-end font-semibold tabular-nums">
                        {format.money(query.data.total, query.data.currency)}
                      </TableCell>
                    </TableRow>
                    {query.data.refunded_amount > 0 ? (
                      <TableRow>
                        <TableCell colSpan={2}>{t("invoices.detail.refunded")}</TableCell>
                        <TableCell className="text-end tabular-nums">
                          {format.money(query.data.refunded_amount, query.data.currency)}
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableFooter>
                </Table>
              </div>
              <section className="grid gap-3" aria-labelledby="invoice-preview">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 id="invoice-preview" className="text-sm font-semibold text-foreground">
                    {t("invoices.detail.preview")}
                  </h3>
                  <div className="flex flex-wrap items-center gap-2">
                    <Tabs
                      value={locale}
                      onValueChange={(value) => {
                        setLocale(value === "ar" ? "ar" : "en");
                      }}
                    >
                      <TabsList>
                        <TabsTrigger value="en" aria-controls="invoice-preview-panel">
                          {t("invoices.detail.english")}
                        </TabsTrigger>
                        <TabsTrigger value="ar" aria-controls="invoice-preview-panel">
                          {t("invoices.detail.arabic")}
                        </TabsTrigger>
                      </TabsList>
                    </Tabs>
                    <Button asChild variant="secondary" size="sm">
                      <a
                        href={getInvoicesDocumentUrl(query.data.id, { locale })}
                        target="_blank"
                        rel="noopener"
                      >
                        <ExternalLink aria-hidden="true" className="rtl:-scale-x-100" />
                        {t("invoices.detail.open")}
                      </a>
                    </Button>
                  </div>
                </div>
                <div
                  id="invoice-preview-panel"
                  role="tabpanel"
                  aria-label={
                    locale === "ar" ? t("invoices.detail.arabic") : t("invoices.detail.english")
                  }
                >
                  {preview.isPending ? (
                    <Skeleton className="h-96 w-full" />
                  ) : preview.isError ? (
                    <QueryError error={preview.error} />
                  ) : (
                    // No scripts and no same-origin access: the document is only shown.
                    <iframe
                      title={t("invoices.detail.preview")}
                      sandbox=""
                      srcDoc={preview.data}
                      className="h-[32rem] w-full rounded-input border border-border bg-white"
                    />
                  )}
                </div>
              </section>
            </>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function apiParams(search: InvoicesSearch): InvoicesListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    status: search.status ? [search.status] : undefined,
    ordering: search.ordering ? [search.ordering] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

function Invoices() {
  const { t } = useTranslation();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const query = useInvoicesList(apiParams(search), {
    query: { placeholderData: keepPreviousData },
  });

  function update(patch: SearchPatch<InvoicesSearch>, { keepPage = false } = {}): void {
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
          label: t("invoices.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
          onRemove: () => {
            update({ status: undefined });
          },
        },
      ]
    : [];
  const filtered = search.q !== undefined || chips.length > 0;

  return (
    <>
      <PageHeader title={t("invoices.title")} description={t("invoices.description")} />
      <DataTable
        label={t("invoices.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(invoice) => invoice.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as InvoicesSearch["ordering"] });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(invoice) => {
          update({ invoice: invoice.id }, { keepPage: true });
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("invoices.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <FacetFilter
                single
                title={t("invoices.filters.status")}
                options={Object.values(InvoiceStatus).map((value) => ({
                  value,
                  label: t(`ui:status.${value}`),
                }))}
                selected={search.status ? [search.status] : []}
                onSelectedChange={(values) => {
                  update({ status: values[0] as InvoicesSearch["status"] });
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
          filtered ? undefined : (
            <EmptyState
              icon={<FileText />}
              title={t("invoices.empty.title")}
              description={t("invoices.empty.description")}
            />
          )
        }
      />
      <InvoiceSheet
        id={search.invoice}
        onClose={() => {
          update({ invoice: undefined }, { keepPage: true });
        }}
      />
    </>
  );
}

export function InvoicesPage() {
  const { t } = useTranslation();
  usePageTitle(t("invoices.title"));
  return (
    <RequirePermission permission="billing.view">
      <Invoices />
    </RequirePermission>
  );
}
