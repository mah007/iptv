import { useDashboardBilling, type Amount } from "@smart-iptv/api";
import {
  BarList,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  StatTile,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { BadgeCheck, CalendarClock, Coins, Gift, TrendingUp, UserMinus } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { localName } from "../catalog/artwork";
import type { SubscriptionsSearch } from "./search";

const REFRESH_MS = 30_000;
const TILE_LINK =
  "block rounded-card outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring [&>[data-slot=stat-tile]]:hover:border-primary/40";

/** Amounts per currency, e.g. "SAR 1,240.00 · USD 30.00"; zero when there are none. */
function useAmounts() {
  const format = useFormatters();
  return (amounts: readonly Amount[], fallback = "SAR") =>
    amounts.length === 0
      ? format.money(0, fallback)
      : amounts.map((item) => format.money(item.amount, item.currency)).join(" · ");
}

/** Revenue this month against last month in the same currency, as a ratio. */
function revenueDelta(current: readonly Amount[], previous: readonly Amount[]): number | undefined {
  const now = current[0];
  if (now === undefined) return undefined;
  const before = previous.find((item) => item.currency === now.currency);
  if (before === undefined || before.amount === 0) return undefined;
  return (now.amount - before.amount) / before.amount;
}

/**
 * Billing figures for the dashboard (SPEC §8.3.1 KPI row: active subscriptions,
 * revenue month to date; plus MRR, trials, expiring, churn and plans).
 */
export function BillingKpis() {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const amounts = useAmounts();
  const query = useDashboardBilling({ query: { refetchInterval: REFRESH_MS } });
  if (query.isError) {
    return (
      <Card>
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      </Card>
    );
  }
  const data = query.data;
  const loading = data === undefined;
  const subscriptions = can("subscriptions.view");
  const link = (search: SubscriptionsSearch, tile: ReactNode) =>
    subscriptions ? (
      <Link to="/subscriptions" search={search} className={TILE_LINK}>
        {tile}
      </Link>
    ) : (
      tile
    );
  return (
    <section aria-labelledby="billing-kpis" className="grid gap-3">
      <h2 id="billing-kpis" className="text-base font-semibold text-foreground">
        {t("dashboard.billing.title")}
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {link(
          { status: "active" },
          <StatTile
            className="h-full"
            label={t("dashboard.billing.active")}
            icon={<BadgeCheck />}
            loading={loading}
            value={data ? format.number(data.active_subscribers) : ""}
            deltaLabel={
              data
                ? t("dashboard.billing.activeDetail", {
                    grace: format.number(data.grace),
                    suspended: format.number(data.suspended),
                  })
                : undefined
            }
          />,
        )}
        <StatTile
          className="h-full"
          label={t("dashboard.billing.revenue")}
          icon={<Coins />}
          loading={loading}
          value={data ? amounts(data.revenue_mtd) : ""}
          delta={data ? revenueDelta(data.revenue_mtd, data.revenue_last_month) : undefined}
          deltaLabel={t("dashboard.billing.revenueDetail")}
        />
        <StatTile
          className="h-full"
          label={t("dashboard.billing.mrr")}
          icon={<TrendingUp />}
          loading={loading}
          value={data ? amounts(data.mrr) : ""}
          deltaLabel={
            data ? t("dashboard.billing.mrrNet", { amount: amounts(data.mrr_net) }) : undefined
          }
        />
        {link(
          { expiring: 7, ordering: "ends_at" },
          <StatTile
            className="h-full"
            label={t("dashboard.billing.expiring")}
            icon={<CalendarClock />}
            loading={loading}
            value={data ? format.number(data.expiring_7d) : ""}
            deltaLabel={t("dashboard.billing.expiringDetail")}
          />,
        )}
        {link(
          { source: "trial" },
          <StatTile
            className="h-full"
            label={t("dashboard.billing.trials")}
            icon={<Gift />}
            loading={loading}
            value={data ? format.number(data.trials_active) : ""}
            deltaLabel={
              data
                ? t("dashboard.billing.trialRequests", {
                    count: data.trial_requests,
                    formatted: format.number(data.trial_requests),
                  })
                : undefined
            }
          />,
        )}
        <StatTile
          className="h-full"
          label={t("dashboard.billing.churn")}
          icon={<UserMinus />}
          loading={loading}
          value={data ? format.number(data.churned_30d) : ""}
          deltaLabel={
            data
              ? t("dashboard.billing.newSubscriptions", {
                  count: data.new_subscriptions_30d,
                  formatted: format.number(data.new_subscriptions_30d),
                })
              : undefined
          }
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("dashboard.billing.byPlan")}</CardTitle>
          <CardDescription>{t("dashboard.billing.byPlanHelp")}</CardDescription>
        </CardHeader>
        <CardContent>
          {data?.by_plan.length === 0 ? (
            <EmptyState className="py-6" title={t("dashboard.billing.noSubscribers")} />
          ) : (
            <BarList
              loading={loading}
              label={t("dashboard.billing.byPlan")}
              valueLabel={t("dashboard.billing.subscribers")}
              formatValue={(value) => format.number(value)}
              items={(data?.by_plan ?? []).map((plan) => ({
                key: plan.code,
                label: <bdi>{localName(plan, i18n.language)}</bdi>,
                value: plan.subscribers,
              }))}
            />
          )}
        </CardContent>
      </Card>
    </section>
  );
}
