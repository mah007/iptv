import {
  getSystemHealthQueryKey,
  useSystemHealth,
  type Health,
  type HealthStatus,
  type RedisHealth,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  PageHeader,
  ProgressBar,
  RelativeTime,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useFormatters,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Cpu, ExternalLink, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import {
  HEALTH_REFRESH_MS,
  StatusDot,
  serviceLabel,
  serviceProblem,
} from "../features/health/health-strip";
import { usePageTitle } from "../lib/page-title";

const STATUS_TONE: Record<HealthStatus, BadgeTone> = {
  ok: "success",
  degraded: "warning",
  down: "danger",
};

function StatusBadge({ status }: { status: HealthStatus }) {
  const { t } = useTranslation();
  return (
    <Badge tone={STATUS_TONE[status]} dot>
      {t(`health.status.${status}`)}
    </Badge>
  );
}

function Services({ health }: { health: Health }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <section aria-labelledby="health-services" className="grid gap-3">
      <h2 id="health-services" className="text-base font-semibold text-foreground">
        {t("health.sections.services")}
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {health.services.map((service) => {
          const problem = serviceProblem(t, service);
          return (
            <li
              key={service.name}
              data-status={service.status}
              className="grid gap-2 rounded-card border border-border bg-card p-(--density-card)"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2 text-ui font-medium text-foreground">
                  <StatusDot status={service.status} />
                  <span className="truncate">{serviceLabel(t, service.name)}</span>
                </span>
                <StatusBadge status={service.status} />
              </div>
              <p className="text-xs text-muted-foreground tabular-nums">
                {service.age_s !== null
                  ? t("health.heartbeat", { age: format.duration(service.age_s, "short") })
                  : service.latency_ms !== null
                    ? t("health.latency", {
                        ms: format.number(service.latency_ms, { maximumFractionDigits: 1 }),
                      })
                    : null}
              </p>
              {problem ? <p className="text-xs text-danger-text">{problem}</p> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Queues({ health }: { health: Health }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("health.sections.queues")}</CardTitle>
        <CardDescription>{t("health.queuesHelp")}</CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        <Table aria-label={t("health.sections.queues")}>
          <TableHeader>
            <TableRow>
              <TableHead>{t("health.columns.queue")}</TableHead>
              <TableHead className="text-end">{t("health.columns.waiting")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {health.queues.map((queue) => (
              <TableRow key={queue.name}>
                <TableCell>
                  <code className="font-mono text-xs" dir="ltr">
                    {queue.name}
                  </code>
                </TableCell>
                <TableCell className="text-end tabular-nums">
                  {queue.depth === null ? (
                    <span className="text-muted-foreground">{t("health.unknown")}</span>
                  ) : (
                    format.number(queue.depth)
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function Transcoders({ health }: { health: Health }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("health.sections.transcoders")}</CardTitle>
        <CardDescription>{t("health.transcodersHelp")}</CardDescription>
      </CardHeader>
      <CardContent>
        {health.transcoders.length === 0 ? (
          <EmptyState
            className="py-6"
            icon={<Cpu />}
            title={t("health.noTranscoders.title")}
            description={t("health.noTranscoders.description")}
          />
        ) : (
          <ul className="grid gap-4">
            {health.transcoders.map((worker) => (
              <li
                key={worker.host}
                className="grid gap-2 border-b border-border pb-4 last:border-0 last:pb-0"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-ui font-medium text-foreground" dir="ltr">
                    {worker.host}
                  </span>
                  <Badge tone="primary">
                    {t(`transcode.backends.${worker.best}`, { defaultValue: worker.best })}
                  </Badge>
                  {worker.checked_at ? (
                    <span className="text-xs text-muted-foreground">
                      {t("health.checked")} <RelativeTime value={worker.checked_at} />
                    </span>
                  ) : null}
                </div>
                <DescriptionList>
                  <DescriptionItem label={t("health.encoders")}>
                    <span className="flex flex-wrap gap-1">
                      {Object.entries(worker.backends).map(([backend, codecs]) => (
                        <Badge key={backend} dir="ltr">
                          {backend}: {codecs.join(", ")}
                        </Badge>
                      ))}
                    </span>
                  </DescriptionItem>
                  {worker.gpus.length > 0 ? (
                    <DescriptionItem label={t("health.gpus")}>
                      <span dir="ltr">{worker.gpus.join(", ")}</span>
                    </DescriptionItem>
                  ) : null}
                  <DescriptionItem label={t("health.ffmpeg")}>
                    <span className="font-mono text-xs" dir="ltr">
                      {worker.ffmpeg || t("health.unknown")}
                    </span>
                  </DescriptionItem>
                </DescriptionList>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RedisCard({ store }: { store: RedisHealth }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <CardTitle>{serviceLabel(t, store.name)}</CardTitle>
        <StatusBadge status={store.status} />
      </CardHeader>
      <CardContent className="grid gap-3">
        {store.used_memory !== null ? (
          store.max_memory ? (
            <ProgressBar
              value={store.used_memory}
              max={store.max_memory}
              label={t("health.memoryOf", {
                used: format.bytes(store.used_memory),
                max: format.bytes(store.max_memory),
              })}
            />
          ) : (
            <p className="text-ui text-foreground tabular-nums">
              {t("health.memory", { used: format.bytes(store.used_memory) })}
            </p>
          )
        ) : null}
        <DescriptionList>
          <DescriptionItem label={t("health.policy")}>
            <code className="font-mono text-xs" dir="ltr">
              {store.policy || t("health.unknown")}
            </code>
          </DescriptionItem>
          <DescriptionItem label={t("health.evicted")}>
            <span
              className={
                store.name === "redis_state" && (store.evicted_keys ?? 0) > 0
                  ? "font-medium text-danger-text tabular-nums"
                  : "tabular-nums"
              }
            >
              {store.evicted_keys === null
                ? t("health.unknown")
                : format.number(store.evicted_keys)}
            </span>
          </DescriptionItem>
        </DescriptionList>
        {store.name === "redis_state" ? (
          <p className="text-xs text-muted-foreground">{t("health.stateEvictionsHelp")}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Database({ health }: { health: Health }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const database = health.database;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <CardTitle>{t("health.sections.database")}</CardTitle>
        <StatusBadge status={database.status} />
      </CardHeader>
      <CardContent>
        {database.connections !== null && database.max_connections !== null ? (
          <ProgressBar
            value={database.connections}
            max={database.max_connections}
            tone={database.status === "ok" ? "primary" : "warning"}
            label={t("health.connections", {
              used: format.number(database.connections),
              max: format.number(database.max_connections),
            })}
          />
        ) : (
          <p className="text-ui text-danger-text">
            {t("health.errors.failed", { error: database.error })}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function HealthSkeleton() {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4" role="status" aria-live="polite">
      <span className="sr-only">{t("layout.loading")}</span>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((card) => (
          <Skeleton key={card} className="h-24 w-full" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}

function SystemHealth() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useSystemHealth({ query: { refetchInterval: HEALTH_REFRESH_MS } });
  const health = query.data;
  return (
    <div className="grid gap-6">
      <PageHeader
        className="pb-0"
        title={
          <span className="flex flex-wrap items-center gap-3">
            {t("health.title")}
            {health ? <StatusBadge status={health.status} /> : null}
          </span>
        }
        description={
          health ? (
            <span>
              {t("health.description")} {t("dashboard.asOf")} <RelativeTime value={health.as_of} />
            </span>
          ) : (
            t("health.description")
          )
        }
        actions={
          <>
            {health?.grafana_url ? (
              <Button asChild variant="secondary">
                <a href={health.grafana_url} target="_blank" rel="noreferrer noopener">
                  <ExternalLink aria-hidden="true" className="rtl:-scale-x-100" />
                  {t("health.grafana")}
                </a>
              </Button>
            ) : null}
            <Button
              variant="secondary"
              disabled={query.isFetching}
              onClick={() => {
                void queryClient.invalidateQueries({ queryKey: getSystemHealthQueryKey() });
              }}
            >
              <RefreshCw
                aria-hidden="true"
                className={query.isFetching ? "motion-safe:animate-spin" : ""}
              />
              {t("health.refresh")}
            </Button>
          </>
        }
      />
      {query.isPending ? (
        <HealthSkeleton />
      ) : query.isError ? (
        <Card>
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        </Card>
      ) : (
        <>
          <Services health={query.data} />
          <div className="grid items-start gap-4 lg:grid-cols-2">
            <Transcoders health={query.data} />
            <Queues health={query.data} />
          </div>
          <div className="grid items-start gap-4 lg:grid-cols-3">
            {query.data.redis.map((store) => (
              <RedisCard key={store.name} store={store} />
            ))}
            <Database health={query.data} />
          </div>
        </>
      )}
    </div>
  );
}

export function HealthPage() {
  const { t } = useTranslation();
  usePageTitle(t("health.title"));
  return (
    <RequirePermission permission="settings.view">
      <SystemHealth />
    </RequirePermission>
  );
}
