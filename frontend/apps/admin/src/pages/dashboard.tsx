import {
  useCustomersList,
  useDashboardKpis,
  useDashboardTimeseries,
  useMoviesList,
  useSeriesList,
  type Kpis,
  type Timeseries,
  type TopTitle,
} from "@smart-iptv/api";
import {
  BarList,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ColumnChart,
  DEFAULT_TIME_ZONE,
  EmptyState,
  ErrorState,
  LiveDuration,
  PageHeader,
  PosterCard,
  PosterGrid,
  PosterImage,
  RelativeTime,
  Skeleton,
  StatTile,
  TimeSeriesChart,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  CalendarClock,
  Clapperboard,
  Cpu,
  ListChecks,
  Play,
  Plus,
  Tv,
  UserCheck,
} from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { RecentActivity } from "../features/activity/activity";
import { imageSource, localName, localTitle } from "../features/catalog/artwork";
import { ExpiryText } from "../features/customers/expiry";
import { HealthStrip } from "../features/health/health-strip";
import { useLiveSessions } from "../features/sessions/live-sessions";
import { LIBRARY_VIEW, useCan, useMe } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const REFRESH_MS = 30_000;

/** Chart labels in the admin's language and time zone. */
function useChartFormat(timeZone: string) {
  const format = useFormatters(timeZone);
  return useMemo(() => {
    // A 24-hour clock keeps axis ticks short (no AM/PM) in every language.
    const time = new Intl.DateTimeFormat(format.locale, {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone,
    });
    const dayTime = new Intl.DateTimeFormat(format.locale, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone,
    });
    // Days arrive as calendar dates ("2026-10-04"): read and print them in UTC.
    const day = new Intl.DateTimeFormat(format.locale, {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
    const longDay = new Intl.DateTimeFormat(format.locale, { dateStyle: "full", timeZone: "UTC" });
    return {
      number: (value: number) => format.number(value),
      time: (at: string) => time.format(new Date(at)),
      dayTime: (at: string) => dayTime.format(new Date(at)),
      day: (at: string) => day.format(new Date(at)),
      longDay: (at: string) => longDay.format(new Date(at)),
    };
  }, [format, timeZone]);
}

interface Tile {
  key: string;
  label: string;
  icon: ReactNode;
  value: (kpis: Kpis) => number;
  detail?: (kpis: Kpis, series: Timeseries | undefined) => string;
  sparkline?: (series: Timeseries) => number[];
  /** Renders the tile as a link to the list it counts. */
  link?: ((className: string, children: ReactNode) => ReactNode) | undefined;
}

function KpiTiles({ series }: { series: Timeseries | undefined }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const kpis = useDashboardKpis({ query: { refetchInterval: REFRESH_MS } });
  const tiles: Tile[] = [
    {
      key: "active",
      label: t("dashboard.kpis.active"),
      icon: <UserCheck />,
      value: (data) => data.customers_active,
      detail: (data) =>
        data.customers_total > 0
          ? t("dashboard.kpis.activeShare", {
              share: format.percent(data.customers_active / data.customers_total),
            })
          : "",
      link: can("customers.view")
        ? (className, children) => (
            <Link to="/customers" search={{ access: "active" }} className={className}>
              {children}
            </Link>
          )
        : undefined,
    },
    {
      key: "streams",
      label: t("dashboard.kpis.streams"),
      icon: <Activity />,
      value: (data) => data.streams_now,
      detail: (data, timeseries) =>
        timeseries
          ? t("dashboard.kpis.streamsPeak", { peak: format.number(timeseries.streams_peak) })
          : t("dashboard.kpis.streamUsers", {
              count: data.stream_users_now,
              formatted: format.number(data.stream_users_now),
            }),
      sparkline: (timeseries) => timeseries.streams.map((point) => point.streams),
      link: can("customers.view")
        ? (className, children) => (
            <Link to="/sessions" className={className}>
              {children}
            </Link>
          )
        : undefined,
    },
    {
      key: "plays",
      label: t("dashboard.kpis.playsToday"),
      icon: <Play className="rtl:-scale-x-100" />,
      value: () => series?.days.at(-1)?.plays ?? 0,
      detail: (_data, timeseries) =>
        timeseries
          ? t("dashboard.kpis.watchHours", {
              hours: format.number(timeseries.days.at(-1)?.watch_hours ?? 0, {
                maximumFractionDigits: 1,
              }),
            })
          : "",
      sparkline: (timeseries) => timeseries.days.map((point) => point.plays),
    },
    {
      key: "expiring",
      label: t("dashboard.kpis.expiring"),
      icon: <CalendarClock />,
      value: (data) => data.expiring_7d,
      detail: () => t("dashboard.kpis.expiringDetail"),
      link: can("customers.view")
        ? (className, children) => (
            <Link
              to="/customers"
              search={{ expiring: 7, ordering: "expires_at" }}
              className={className}
            >
              {children}
            </Link>
          )
        : undefined,
    },
    {
      key: "reviews",
      label: t("dashboard.kpis.reviews"),
      icon: <ListChecks />,
      value: (data) => data.reviews_open,
      detail: () => t("dashboard.kpis.reviewsDetail"),
      link: can(LIBRARY_VIEW)
        ? (className, children) => (
            <Link to="/review" className={className}>
              {children}
            </Link>
          )
        : undefined,
    },
    {
      key: "transcode",
      label: t("dashboard.kpis.transcode"),
      icon: <Cpu />,
      value: (data) => data.transcode_queued + data.transcode_running,
      detail: (data) =>
        t("dashboard.kpis.transcodeDetail", {
          running: format.number(data.transcode_running),
          failed: format.number(data.transcode_failed_24h),
        }),
      link: can(LIBRARY_VIEW)
        ? (className, children) => (
            <Link to="/transcode" className={className}>
              {children}
            </Link>
          )
        : undefined,
    },
  ];

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
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {tiles.map((tile) => {
          const content = (
            <StatTile
              className="h-full"
              label={tile.label}
              icon={tile.icon}
              loading={data === undefined}
              value={data ? format.number(tile.value(data)) : ""}
              deltaLabel={data && tile.detail ? tile.detail(data, series) || undefined : undefined}
              sparkline={series && tile.sparkline ? tile.sparkline(series) : undefined}
            />
          );
          return (
            <div key={tile.key} className="contents">
              {tile.link ? tile.link(TILE_LINK_CLASS, content) : content}
            </div>
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

const TILE_LINK_CLASS =
  "block rounded-card outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring [&>[data-slot=stat-tile]]:hover:border-primary/40";

function ChartCard({
  title,
  description,
  children,
  action,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Charts({
  data,
  error,
  onRetry,
  timeZone,
}: {
  data: Timeseries | undefined;
  error: unknown;
  onRetry: () => void;
  timeZone: string;
}) {
  const { t, i18n } = useTranslation();
  const chart = useChartFormat(timeZone);
  if (error) {
    return (
      <Card>
        <QueryError error={error} onRetry={onRetry} />
      </Card>
    );
  }
  const loading = data === undefined;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <ChartCard
        className="lg:col-span-2"
        title={t("dashboard.charts.streams.title")}
        description={t("dashboard.charts.streams.description", {
          minutes: data?.bucket_minutes ?? 15,
        })}
        action={
          data && data.streams_peak > 0 ? (
            <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
              <span
                aria-hidden="true"
                className="w-4 border-t border-dashed border-muted-foreground"
              />
              {t("dashboard.charts.streams.peak", { peak: chart.number(data.streams_peak) })}
            </span>
          ) : null
        }
      >
        <TimeSeriesChart
          label={t("dashboard.charts.streams.title")}
          valueLabel={t("dashboard.charts.streams.value")}
          loading={loading}
          data={(data?.streams ?? []).map((point) => ({ at: point.at, value: point.streams }))}
          formatTick={chart.time}
          formatTooltip={chart.dayTime}
          formatValue={chart.number}
          reference={data && data.streams_peak > 0 ? { value: data.streams_peak } : undefined}
        />
      </ChartCard>
      <ChartCard
        title={t("dashboard.charts.categories.title")}
        description={t("dashboard.charts.categories.description")}
      >
        {data?.categories.length === 0 ? (
          <EmptyState className="py-6" title={t("dashboard.charts.noPlays")} />
        ) : (
          <BarList
            loading={loading}
            label={t("dashboard.charts.categories.title")}
            valueLabel={t("dashboard.charts.plays")}
            formatValue={chart.number}
            items={(data?.categories ?? []).map((category) => ({
              key: category.id,
              label: (
                <>
                  <bdi>{localName(category, i18n.language)}</bdi>
                  <span className="text-muted-foreground">
                    {" · "}
                    {t(`categories.kinds.${category.kind}`, { defaultValue: category.kind })}
                  </span>
                </>
              ),
              value: category.plays,
            }))}
          />
        )}
      </ChartCard>
      <ChartCard
        className="lg:col-span-2"
        title={t("dashboard.charts.signups.title")}
        description={t("dashboard.charts.signups.description")}
      >
        <ColumnChart
          label={t("dashboard.charts.signups.title")}
          loading={loading}
          series={[
            { key: "signups", label: t("dashboard.charts.signups.new"), tone: "accent" },
            { key: "churned", label: t("dashboard.charts.signups.churned"), tone: "muted" },
          ]}
          data={(data?.days ?? []).map((day) => ({
            at: day.date,
            signups: day.signups,
            churned: day.churned,
          }))}
          formatTick={chart.day}
          formatTooltip={chart.longDay}
          formatValue={chart.number}
        />
      </ChartCard>
      <ChartCard
        title={t("dashboard.charts.topTitles.title")}
        description={t("dashboard.charts.topTitles.description")}
      >
        <TopTitles titles={data?.top_titles} />
      </ChartCard>
    </div>
  );
}

function TopTitleLink({ title, children }: { title: TopTitle; children: ReactNode }) {
  const className =
    "flex min-w-0 items-center gap-3 rounded-input px-2 py-1.5 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring";
  return title.kind === "movie" ? (
    <Link to="/movies/$titleId" params={{ titleId: title.id }} className={className}>
      {children}
    </Link>
  ) : (
    <Link to="/series/$titleId" params={{ titleId: title.id }} className={className}>
      {children}
    </Link>
  );
}

function TopTitles({ titles }: { titles: readonly TopTitle[] | undefined }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  if (titles === undefined) {
    return (
      <ul className="grid gap-3" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <li key={row} className="flex items-center gap-3">
            <Skeleton className="h-12 w-8" />
            <Skeleton className="h-4 flex-1" />
          </li>
        ))}
      </ul>
    );
  }
  if (titles.length === 0) {
    return <EmptyState className="py-6" title={t("dashboard.charts.noPlays")} />;
  }
  return (
    <ol className="-mx-2 grid">
      {titles.map((title, index) => {
        const name = localTitle(title, i18n.language);
        const row = (
          <>
            <span className="w-4 shrink-0 text-xs text-muted-foreground tabular-nums">
              {format.number(index + 1)}
            </span>
            <PosterImage
              src={imageSource(title.poster)}
              blurhash={title.poster?.blurhash}
              alt=""
              className="w-8 shrink-0 rounded-[4px]"
            />
            <span className="grid min-w-0 flex-1">
              <span className="truncate text-ui font-medium text-foreground">
                <bdi>{name}</bdi>
              </span>
              <span className="text-xs text-muted-foreground">
                {title.kind === "movie" ? t("dashboard.kinds.movie") : t("dashboard.kinds.series")}
                {title.year ? ` · ${String(title.year)}` : ""}
              </span>
            </span>
            <span className="shrink-0 text-end text-xs text-muted-foreground tabular-nums">
              {t("dashboard.charts.topTitles.plays", {
                count: title.plays,
                formatted: format.number(title.plays),
              })}
            </span>
          </>
        );
        return (
          <li key={`${title.kind}-${title.id}`}>
            {can(LIBRARY_VIEW) ? (
              <TopTitleLink title={title}>{row}</TopTitleLink>
            ) : (
              <span className="flex items-center gap-3 px-2 py-1.5">{row}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function NowWatching() {
  const { t } = useTranslation();
  const { sessions, received } = useLiveSessions();
  const rows = [...sessions.values()]
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .slice(0, 6);
  if (!received) {
    return (
      <div className="grid gap-2" aria-busy="true">
        {[0, 1, 2].map((row) => (
          <Skeleton key={row} className="h-9 w-full" />
        ))}
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        className="py-6"
        icon={<Activity />}
        title={t("sessions.empty.title")}
        description={t("sessions.empty.description")}
      />
    );
  }
  return (
    <ul className="-mx-2 grid">
      {rows.map((session) => {
        const Icon = session.title.kind === "episode" ? Tv : Clapperboard;
        return (
          <li key={session.id} className="flex items-center gap-3 rounded-input px-2 py-2">
            <Icon
              role="img"
              aria-label={t(
                `sessions.kinds.${session.title.kind === "episode" ? "episode" : "movie"}`,
              )}
              className="size-4 shrink-0 text-muted-foreground"
            />
            <span className="grid min-w-0 flex-1">
              <span className="truncate text-ui font-medium text-foreground">
                <bdi>{session.title.name || t("sessions.unknownTitle")}</bdi>
              </span>
              <span className="truncate text-xs text-muted-foreground">
                <Link
                  to="/customers/$customerId"
                  params={{ customerId: session.user.id }}
                  className="outline-none hover:underline focus-visible:underline"
                >
                  <bdi>{session.user.name || t("sessions.unknownCustomer")}</bdi>
                </Link>
                {" · "}
                <bdi>{session.device.name || t("sessions.unknownDevice")}</bdi>
              </span>
            </span>
            {session.rendition ? (
              <span className="hidden font-mono text-xs text-muted-foreground sm:inline" dir="ltr">
                {session.rendition}
              </span>
            ) : null}
            <LiveDuration since={session.started_at} className="shrink-0 text-xs" />
          </li>
        );
      })}
    </ul>
  );
}

function ExpiringSoon() {
  const { t } = useTranslation();
  const query = useCustomersList({
    expiring_within_days: 7,
    ordering: ["expires_at"],
    page_size: 6,
  });
  if (query.isPending) {
    return (
      <div className="grid gap-2" aria-busy="true">
        {[0, 1, 2].map((row) => (
          <Skeleton key={row} className="h-9 w-full" />
        ))}
      </div>
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
    return <EmptyState className="py-6" title={t("dashboard.noneExpiring")} />;
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
            <span className="grid min-w-0 flex-1">
              <span className="truncate text-ui font-medium text-foreground">
                <bdi>{customer.name || customer.username}</bdi>
              </span>
              <span className="ltr-value truncate text-xs text-muted-foreground" dir="ltr">
                {customer.phone || customer.email || customer.username}
              </span>
            </span>
            <ExpiryText expiresAt={customer.expires_at} className="shrink-0 text-ui" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

function RecentlyAdded() {
  const { t, i18n } = useTranslation();
  const movies = useMoviesList({ ordering: "-created_at", page_size: 6 });
  const series = useSeriesList({ ordering: "-created_at", page_size: 6 });
  const titles = [
    ...(movies.data?.results ?? []).map((title) => ({ ...title, kind: "movie" as const })),
    ...(series.data?.results ?? []).map((title) => ({ ...title, kind: "series" as const })),
  ]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 6);
  const loading = movies.isPending || series.isPending;
  if (!loading && titles.length === 0) {
    return <EmptyState className="py-6" title={t("dashboard.recentlyAddedEmpty")} />;
  }
  return (
    <PosterGrid loading={loading} aria-label={t("dashboard.recentlyAdded")}>
      {titles.map((title) => (
        <PosterCard
          key={`${title.kind}-${title.id}`}
          asChild
          title={localTitle(title, i18n.language)}
          year={title.year}
          poster={imageSource(title.poster)}
          blurhash={title.poster?.blurhash}
          rating={title.rating}
        >
          {title.kind === "movie" ? (
            <Link to="/movies/$titleId" params={{ titleId: title.id }} />
          ) : (
            <Link to="/series/$titleId" params={{ titleId: title.id }} />
          )}
        </PosterCard>
      ))}
    </PosterGrid>
  );
}

function Dashboard() {
  const { t } = useTranslation();
  const can = useCan();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const timeseries = useDashboardTimeseries({ query: { refetchInterval: REFRESH_MS } });
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
      {can("settings.view") ? <HealthStrip /> : null}
      <KpiTiles series={timeseries.data} />
      <Charts
        data={timeseries.data}
        error={timeseries.error}
        onRetry={() => {
          void timeseries.refetch();
        }}
        timeZone={timeZone}
      />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        {can("customers.view") ? (
          <div className="grid gap-4">
            <ChartCard
              title={t("dashboard.nowWatching")}
              action={
                <Button asChild variant="link" size="xs">
                  <Link to="/sessions">{t("dashboard.viewAll")}</Link>
                </Button>
              }
            >
              <NowWatching />
            </ChartCard>
            <ChartCard
              title={t("dashboard.expiringSoon")}
              action={
                <Button asChild variant="link" size="xs">
                  <Link to="/customers" search={{ expiring: 7, ordering: "expires_at" }}>
                    {t("dashboard.viewAll")}
                  </Link>
                </Button>
              }
            >
              <ExpiringSoon />
            </ChartCard>
          </div>
        ) : null}
        <ChartCard title={t("dashboard.recentActivity")}>
          <RecentActivity />
        </ChartCard>
      </div>
      {can(LIBRARY_VIEW) ? (
        <section aria-labelledby="recently-added" className="grid gap-3">
          <h2 id="recently-added" className="text-base font-semibold text-foreground">
            {t("dashboard.recentlyAdded")}
          </h2>
          <RecentlyAdded />
        </section>
      ) : null}
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
