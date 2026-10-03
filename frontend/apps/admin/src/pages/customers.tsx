import {
  AccessStatus,
  UserStatus,
  useCustomersList,
  type CustomerSummary,
  type CustomersListParams,
} from "@smart-iptv/api";
import {
  Avatar,
  Button,
  DataTable,
  EmptyState,
  FacetFilter,
  FilterBar,
  PageHeader,
  RelativeTime,
  SearchInput,
  StatusBadge,
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
import { Plus, Users } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { CreateCustomerSheet } from "../features/customers/create-customer-sheet";
import { ExpiryText } from "../features/customers/expiry";
import { EXPIRING_WINDOWS, type CustomersSearch } from "../features/customers/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/customers");
const helper = createDataTableColumnHelper<CustomerSummary>();

function useColumns() {
  const { t } = useTranslation();
  const format = useFormatters();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("name", {
          id: "name",
          header: t("customers.columns.customer"),
          cell: ({ row }) => {
            const customer = row.original;
            return (
              <div className="flex min-w-0 items-center gap-2.5">
                <Avatar name={customer.name || customer.username} size="md" />
                <div className="grid min-w-0">
                  <Link
                    to="/customers/$customerId"
                    params={{ customerId: customer.id }}
                    className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
                  >
                    {customer.name || customer.username}
                  </Link>
                  <span className="truncate text-xs text-muted-foreground" dir="ltr">
                    {customer.email || customer.username}
                  </span>
                </div>
              </div>
            );
          },
          meta: { cellClassName: "max-w-72" },
        }),
        helper.accessor("phone", {
          id: "phone",
          header: t("customers.columns.phone"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="whitespace-nowrap tabular-nums" dir="ltr">
              {getValue()}
            </span>
          ),
        }),
        helper.accessor("access_status", {
          id: "access_status",
          header: t("customers.columns.access"),
          enableSorting: false,
          cell: ({ getValue }) => <StatusBadge status={getValue()} />,
        }),
        helper.accessor("expires_at", {
          id: "expires_at",
          header: t("customers.columns.expires"),
          cell: ({ getValue }) => <ExpiryText expiresAt={getValue()} />,
        }),
        helper.accessor("device_count", {
          id: "devices",
          header: t("customers.columns.devices"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="tabular-nums" dir="ltr">
              {row.original.max_devices === null
                ? format.number(row.original.device_count)
                : t("customers.deviceCount", {
                    count: format.number(row.original.device_count),
                    max: format.number(row.original.max_devices),
                  })}
            </span>
          ),
          meta: { align: "end" },
        }),
        helper.accessor("last_seen", {
          id: "last_seen",
          header: t("customers.columns.lastSeen"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const value = getValue();
            return value ? (
              <RelativeTime value={value} />
            ) : (
              <span className="text-muted-foreground">{t("customers.never")}</span>
            );
          },
        }),
        helper.accessor("created_at", {
          id: "created_at",
          header: t("customers.columns.created"),
          cell: ({ getValue }) => <RelativeTime value={getValue()} />,
        }),
      ]),
    [t, format],
  );
}

function apiParams(search: CustomersSearch): CustomersListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    access_status: search.access,
    status: search.status,
    expiring_within_days: search.expiring,
    ordering: search.ordering ? [search.ordering] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

function Customers() {
  const { t } = useTranslation();
  const can = useCan();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const query = useCustomersList(apiParams(search), {
    query: { placeholderData: keepPreviousData },
  });

  /** Change filters; any filter change starts again from the first page. */
  function update(patch: SearchPatch<CustomersSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const filtered =
    search.q !== undefined ||
    search.access !== undefined ||
    search.status !== undefined ||
    search.expiring !== undefined;
  const chips: FilterChip[] = [];
  if (search.access !== undefined) {
    chips.push({
      id: "access",
      label: t("customers.filters.chipAccess", { value: t(`ui:status.${search.access}`) }),
      onRemove: () => {
        update({ access: undefined });
      },
    });
  }
  if (search.status !== undefined) {
    chips.push({
      id: "status",
      label: t("customers.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
      onRemove: () => {
        update({ status: undefined });
      },
    });
  }
  if (search.expiring !== undefined) {
    chips.push({
      id: "expiring",
      label: t("customers.filters.expiringWithin", { count: search.expiring }),
      onRemove: () => {
        update({ expiring: undefined });
      },
    });
  }

  const newButton = can("customers.edit") ? (
    <Button asChild>
      <Link to="/customers" search={{ ...search, new: true }}>
        <Plus aria-hidden="true" />
        {t("customers.new")}
      </Link>
    </Button>
  ) : null;

  return (
    <>
      <PageHeader
        title={t("customers.title")}
        description={t("customers.description")}
        actions={newButton}
      />
      <DataTable
        label={t("customers.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(customer) => customer.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as CustomersSearch["ordering"] });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(customer) => {
          void navigate({ to: "/customers/$customerId", params: { customerId: customer.id } });
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("customers.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <>
                <FacetFilter
                  single
                  title={t("customers.filters.access")}
                  options={Object.values(AccessStatus).map((value) => ({
                    value,
                    label: t(`ui:status.${value}`),
                  }))}
                  selected={search.access ? [search.access] : []}
                  onSelectedChange={(values) => {
                    update({ access: values[0] as CustomersSearch["access"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("customers.filters.status")}
                  options={Object.values(UserStatus).map((value) => ({
                    value,
                    label: t(`ui:status.${value}`),
                  }))}
                  selected={search.status ? [search.status] : []}
                  onSelectedChange={(values) => {
                    update({ status: values[0] as CustomersSearch["status"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("customers.filters.expiring")}
                  options={EXPIRING_WINDOWS.map((days) => ({
                    value: String(days),
                    label: t("customers.filters.days", { count: days }),
                  }))}
                  selected={search.expiring ? [String(search.expiring)] : []}
                  onSelectedChange={(values) => {
                    update({ expiring: values[0] ? Number(values[0]) : undefined });
                  }}
                />
              </>
            }
            chips={chips}
            onClearAll={() => {
              update({ q: undefined, access: undefined, status: undefined, expiring: undefined });
            }}
          />
        }
        emptyState={
          filtered ? undefined : (
            <EmptyState
              icon={<Users />}
              title={t("customers.empty.title")}
              description={t("customers.empty.description")}
              action={newButton}
            />
          )
        }
      />
      <CreateCustomerSheet
        open={search.new === true && can("customers.edit")}
        onOpenChange={(open) => {
          if (!open) update({ new: undefined }, { keepPage: true });
        }}
      />
    </>
  );
}

export function CustomersPage() {
  const { t } = useTranslation();
  usePageTitle(t("customers.title"));
  return (
    <RequirePermission permission="customers.view">
      <Customers />
    </RequirePermission>
  );
}

