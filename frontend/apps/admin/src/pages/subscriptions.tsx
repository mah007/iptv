import {
  SubscriptionSource,
  SubscriptionStatus,
  usePlansList,
  useSubscriptionsList,
  type Subscription,
  type SubscriptionsListParams,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  FacetFilter,
  FilterBar,
  PageHeader,
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
import { CreditCard, Plus } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { NewSubscriptionDialog } from "../features/billing/new-subscription";
import { planName } from "../features/billing/pickers";
import { EXPIRING_DAYS, type SubscriptionsSearch } from "../features/billing/search";
import { SubscriptionActions } from "../features/billing/subscription-actions";
import { ExpiryText } from "../features/customers/expiry";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/subscriptions");
const helper = createDataTableColumnHelper<Subscription>();

/** The subscription's live end: the grace deadline while in grace, else its end. */
export function SubscriptionEnd({ subscription }: { subscription: Subscription }) {
  const format = useFormatters();
  const { t } = useTranslation();
  if (subscription.status === "grace" && subscription.grace_until) {
    return (
      <span className="grid">
        <ExpiryText expiresAt={subscription.grace_until} />
        <span className="text-xs text-muted-foreground">
          {t("subscriptions.endedOn", { date: format.date(subscription.ends_at) })}
        </span>
      </span>
    );
  }
  if (["active", "suspended", "pending"].includes(subscription.status)) {
    return <ExpiryText expiresAt={subscription.ends_at} />;
  }
  return (
    <span className="text-muted-foreground">
      {format.date(subscription.ended_at ?? subscription.ends_at)}
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
          header: t("subscriptions.columns.customer"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const user = getValue();
            return (
              <div className="grid min-w-0">
                <Link
                  to="/customers/$customerId"
                  params={{ customerId: user.id }}
                  search={{ tab: "subscriptions" }}
                  className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
                >
                  <bdi>{user.name || user.username}</bdi>
                </Link>
                <span className="ltr-value truncate text-xs text-muted-foreground" dir="ltr">
                  {user.phone || user.email || user.username}
                </span>
              </div>
            );
          },
          meta: { cellClassName: "max-w-64" },
        }),
        helper.accessor("plan", {
          id: "plan",
          header: t("subscriptions.columns.plan"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const plan = getValue();
            return (
              <span className="flex flex-wrap items-center gap-1.5">
                <bdi>{planName(plan, i18n.language)}</bdi>
                {plan.is_trial ? <StatusBadge status="trial" /> : null}
              </span>
            );
          },
        }),
        helper.accessor("status", {
          id: "status",
          header: t("subscriptions.columns.status"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={row.original.status} />
              {row.original.status === "pending" && row.original.source === "trial" ? (
                <Badge tone="violet">{t("subscriptions.trialRequest")}</Badge>
              ) : null}
            </span>
          ),
        }),
        helper.accessor("source", {
          id: "source",
          header: t("subscriptions.columns.source"),
          enableSorting: false,
          cell: ({ getValue }) => t(`subscriptions.sources.${getValue()}`),
        }),
        helper.accessor("starts_at", {
          id: "starts_at",
          header: t("subscriptions.columns.starts"),
          cell: ({ getValue }) => (
            <span className="whitespace-nowrap">{format.date(getValue())}</span>
          ),
        }),
        helper.accessor("ends_at", {
          id: "ends_at",
          header: t("subscriptions.columns.ends"),
          cell: ({ row }) => <SubscriptionEnd subscription={row.original} />,
        }),
        helper.display({
          id: "actions",
          header: () => <span className="sr-only">{t("subscriptions.columns.actions")}</span>,
          cell: ({ row }) => <SubscriptionActions subscription={row.original} />,
          meta: { align: "end" },
        }),
      ]),
    [t, i18n.language, format],
  );
}

function apiParams(search: SubscriptionsSearch): SubscriptionsListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    status: search.status ? [search.status] : undefined,
    source: search.source,
    plan: search.plan,
    expiring_within_days: search.expiring,
    ordering: search.ordering ? [search.ordering] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

function Subscriptions() {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const plans = usePlansList({});
  const query = useSubscriptionsList(apiParams(search), {
    query: { placeholderData: keepPreviousData },
  });

  function update(patch: SearchPatch<SubscriptionsSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const planOf = (id: string | undefined) => plans.data?.find((plan) => plan.id === id);
  const chips: FilterChip[] = [];
  if (search.status) {
    chips.push({
      id: "status",
      label: t("subscriptions.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
      onRemove: () => {
        update({ status: undefined });
      },
    });
  }
  if (search.source) {
    chips.push({
      id: "source",
      label: t("subscriptions.filters.chipSource", {
        value: t(`subscriptions.sources.${search.source}`),
      }),
      onRemove: () => {
        update({ source: undefined });
      },
    });
  }
  const selectedPlan = planOf(search.plan);
  if (search.plan) {
    chips.push({
      id: "plan",
      label: t("subscriptions.filters.chipPlan", {
        value: selectedPlan ? planName(selectedPlan, i18n.language) : search.plan,
      }),
      onRemove: () => {
        update({ plan: undefined });
      },
    });
  }
  if (search.expiring) {
    chips.push({
      id: "expiring",
      label: t("customers.filters.expiringWithin", { count: search.expiring }),
      onRemove: () => {
        update({ expiring: undefined });
      },
    });
  }
  const filtered = search.q !== undefined || chips.length > 0;

  const newButton = can("subscriptions.edit") ? (
    <Button asChild>
      <Link to="/subscriptions" search={{ ...search, new: true }}>
        <Plus aria-hidden="true" />
        {t("subscriptions.new.button")}
      </Link>
    </Button>
  ) : null;

  return (
    <>
      <PageHeader
        title={t("subscriptions.title")}
        description={t("subscriptions.description")}
        actions={newButton}
      />
      <DataTable
        label={t("subscriptions.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(subscription) => subscription.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as SubscriptionsSearch["ordering"] });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(subscription) => {
          void navigate({
            to: "/customers/$customerId",
            params: { customerId: subscription.user.id },
            search: { tab: "subscriptions" },
          });
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("subscriptions.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <>
                <FacetFilter
                  single
                  title={t("subscriptions.filters.status")}
                  options={Object.values(SubscriptionStatus).map((value) => ({
                    value,
                    label: t(`ui:status.${value}`),
                  }))}
                  selected={search.status ? [search.status] : []}
                  onSelectedChange={(values) => {
                    update({ status: values[0] as SubscriptionsSearch["status"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("subscriptions.filters.plan")}
                  options={(plans.data ?? []).map((plan) => ({
                    value: plan.id,
                    label: planName(plan, i18n.language),
                  }))}
                  selected={search.plan ? [search.plan] : []}
                  onSelectedChange={(values) => {
                    update({ plan: values[0] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("subscriptions.filters.source")}
                  options={Object.values(SubscriptionSource).map((value) => ({
                    value,
                    label: t(`subscriptions.sources.${value}`),
                  }))}
                  selected={search.source ? [search.source] : []}
                  onSelectedChange={(values) => {
                    update({ source: values[0] as SubscriptionsSearch["source"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("customers.filters.expiring")}
                  options={EXPIRING_DAYS.map((days) => ({
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
              update({
                q: undefined,
                status: undefined,
                source: undefined,
                plan: undefined,
                expiring: undefined,
              });
            }}
          />
        }
        emptyState={
          filtered ? undefined : (
            <EmptyState
              icon={<CreditCard />}
              title={t("subscriptions.empty.title")}
              description={t("subscriptions.empty.description")}
              action={newButton}
            />
          )
        }
      />
      <NewSubscriptionDialog
        open={search.new === true && can("subscriptions.edit")}
        onOpenChange={(open) => {
          if (!open) update({ new: undefined }, { keepPage: true });
        }}
      />
    </>
  );
}

export function SubscriptionsPage() {
  const { t } = useTranslation();
  usePageTitle(t("subscriptions.title"));
  return (
    <RequirePermission permission="subscriptions.view">
      <Subscriptions />
    </RequirePermission>
  );
}
