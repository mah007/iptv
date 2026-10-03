import {
  useCustomersList,
  useDashboardKpis,
  type CustomerSummary,
  type CustomersListParams,
  type Kpis,
} from "@smart-iptv/api";
import {
  Avatar,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Skeleton,
  StatTile,
  StatusBadge,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import {
  CalendarClock,
  CalendarX2,
  MonitorSmartphone,
  PauseCircle,
  Plus,
  UserCheck,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { ExpiryText } from "../features/customers/expiry";
import type { CustomersSearch } from "../features/customers/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

interface Tile {
  key: string;
  label: string;
  value: (kpis: Kpis) => number;
  icon: ReactNode;
  detail?: (kpis: Kpis) => string;
  /** The customers list filtered to what the tile counts. */
  search?: CustomersSearch;
}

function useTiles(): Tile[] {
  const { t } = useTranslation();
  const format = useFormatters();
  return [
    {
      key: "customers",
      label: t("dashboard.kpis.customers"),
      value: (kpis) => kpis.customers_total,
      icon: <Users />,
      search: {},
    },
    {
      key: "active",
      label: t("dashboard.kpis.active"),
      value: (kpis) => kpis.customers_active,
      icon: <UserCheck />,
      detail: (kpis) =>
        kpis.customers_total > 0
          ? t("dashboard.kpis.activeShare", {
              share: format.percent(kpis.customers_active / kpis.customers_total),
            })
          : "",
      search: { access: "active" },
    },
    {
      key: "expiring",
      label: t("dashboard.kpis.expiring"),
      value: (kpis) => kpis.expiring_7d,
      icon: <CalendarClock />,
      detail: () => t("dashboard.kpis.expiringDetail"),
      search: { expiring: 7, ordering: "expires_at" },
    },
    {
      key: "expired",
      label: t("dashboard.kpis.expired"),
      value: (kpis) => kpis.customers_expired,
      icon: <CalendarX2 />,
      search: { access: "expired" },
    },
    {
      key: "suspended",
      label: t("dashboard.kpis.suspended"),
      value: (kpis) => kpis.customers_suspended,
      icon: <PauseCircle />,
      search: { status: "suspended" },
    },
    {
      key: "devices",
      label: t("dashboard.kpis.devices"),
      value: (kpis) => kpis.devices_total,
      icon: <MonitorSmartphone />,
      detail: (kpis) =>
        t("dashboard.kpis.devicesBlocked", {
          count: kpis.devices_blocked,
          formatted: format.number(kpis.devices_blocked),
        }),
    },
  ];
}

function KpiTiles() {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const tiles = useTiles();
  const kpis = useDashboardKpis({ query: { refetchInterval: 30_000 } });
  if (kpis.isError) {
    return (
      <Card>
        <QueryError
          error={kpis.error}
          onRetry={() => {
            void kpis.refetch();
          }}
        />
      </Card>
    );
  }
  const data = kpis.data;
  return (
    <section aria-label={t("dashboard.kpis.label")} className="grid gap-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tiles.map((tile) => {
          const content = (
            <StatTile
              className="h-full"
              label={tile.label}
              icon={tile.icon}
              loading={data === undefined}
              value={data ? format.number(tile.value(data)) : ""}
              deltaLabel={data && tile.detail ? tile.detail(data) || undefined : undefined}
            />
          );
          return tile.search !== undefined && can("customers.view") ? (
            <Link
              key={tile.key}
              to="/customers"
              search={tile.search}
              className="block rounded-card outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring [&>[data-slot=stat-tile]]:hover:border-primary/40"
            >
              {content}
            </Link>
          ) : (
            <div key={tile.key}>{content}</div>
          );
        })}
      </div>
      {data ? (
        <p className="text-xs text-muted-foreground">
          {t("dashboard.asOf")} <RelativeTime value={data.as_of} />
        </p>
      ) : null}
    </section>
  );
}

function CustomerRows({
  params,
  aside,
  emptyTitle,
}: {
  params: CustomersListParams;
  aside: (customer: CustomerSummary) => ReactNode;
  emptyTitle: string;
}) {
  const { t } = useTranslation();
  const query = useCustomersList(params);
  if (query.isPending) {
    return (
      <ul className="grid gap-3" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <li key={row} className="flex items-center gap-3">
            <Skeleton className="size-8 rounded-full" />
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-4 w-20" />
          </li>
        ))}
      </ul>
    );
  }
  if (query.isError) {
    return (
      <ErrorState
        className="py-6"
        title={t("dashboard.panelError")}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.results.length === 0) {
    return <EmptyState className="py-6" title={emptyTitle} />;
  }
  return (
    <ul className="-mx-2 grid">
      {query.data.results.map((customer) => (
        <li key={customer.id}>
          <Link
            to="/customers/$customerId"
            params={{ customerId: customer.id }}
            className="flex items-center gap-3 rounded-input px-2 py-2 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Avatar name={customer.name || customer.username} size="md" />
            <span className="grid min-w-0 flex-1">
              <span className="truncate text-ui font-medium text-foreground">
                {customer.name || customer.username}
              </span>
              <span className="ltr-value truncate text-xs text-muted-foreground" dir="ltr">
                {customer.phone || customer.email || customer.username}
              </span>
            </span>
            <span className="shrink-0 text-ui">{aside(customer)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function CustomerPanels() {
  const { t } = useTranslation();
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>{t("dashboard.expiringSoon")}</CardTitle>
          <Button asChild variant="link" size="xs">
            <Link to="/customers" search={{ expiring: 7, ordering: "expires_at" }}>
              {t("dashboard.viewAll")}
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          <CustomerRows
            params={{ expiring_within_days: 7, ordering: ["expires_at"], page_size: 6 }}
            aside={(customer) => <ExpiryText expiresAt={customer.expires_at} />}
            emptyTitle={t("dashboard.noneExpiring")}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>{t("dashboard.newest")}</CardTitle>
          <Button asChild variant="link" size="xs">
            <Link to="/customers">{t("dashboard.viewAll")}</Link>
          </Button>
        </CardHeader>
        <CardContent>
          <CustomerRows
            params={{ ordering: ["-created_at"], page_size: 6 }}
            aside={(customer) => (
              <span className="flex items-center gap-2">
                <StatusBadge status={customer.access_status} />
                <RelativeTime value={customer.created_at} className="text-muted-foreground" />
              </span>
            )}
            emptyTitle={t("dashboard.noCustomers")}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Dashboard() {
  const { t } = useTranslation();
  const can = useCan();
  return (
    <div className="grid gap-6">
      <PageHeader
        className="pb-0"
        title={t("dashboard.title")}
        description={t("dashboard.description")}
        actions={
          can("customers.edit") ? (
            <Button asChild>
              <Link to="/customers" search={{ new: true }}>
                <Plus aria-hidden="true" />
                {t("customers.new")}
              </Link>
            </Button>
          ) : null
        }
      />
      <KpiTiles />
      {can("customers.view") ? <CustomerPanels /> : null}
    </div>
  );
}

export function DashboardPage() {
  const { t } = useTranslation();
  usePageTitle(t("dashboard.title"));
  return (
    <RequirePermission permission="dashboard.view">
      <Dashboard />
    </RequirePermission>
  );
}
