import { useSystemHealth, type HealthStatus, type ServiceHealth } from "@smart-iptv/api";
import { Skeleton, Tooltip, TooltipContent, TooltipTrigger, cn } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

/** How often the health views refresh. */
export const HEALTH_REFRESH_MS = 30_000;

export const STATUS_DOT: Record<HealthStatus, string> = {
  ok: "bg-success",
  degraded: "bg-warning",
  down: "bg-danger",
};

/** "Postgres", "Media edge (nginx-stream)": a service name in the admin's language. */
export function serviceLabel(t: TFunction, name: string): string {
  if (name.startsWith("edge:")) {
    return t("health.services.edge", { host: name.slice("edge:".length) });
  }
  return t(`health.services.${name}`, { defaultValue: name });
}

export function StatusDot({ status, className }: { status: HealthStatus; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-2 shrink-0 rounded-full", STATUS_DOT[status], className)}
    />
  );
}

/** Why a service is not OK, in words; empty when it is. */
export function serviceProblem(t: TFunction, service: ServiceHealth): string {
  if (service.status === "ok") return "";
  if (service.error === "late_heartbeat" || service.error === "no_heartbeat") {
    return t(`health.errors.${service.error}`);
  }
  if (service.error === "no_transcoder" || service.error === "bad_url") {
    return t(`health.errors.${service.error}`);
  }
  return service.error ? t("health.errors.failed", { error: service.error }) : "";
}

/** One line of dots on the dashboard (SPEC §8.3: system health strip), linking to Health. */
export function HealthStrip() {
  const { t } = useTranslation();
  const query = useSystemHealth({ query: { refetchInterval: HEALTH_REFRESH_MS } });
  if (query.isPending) return <Skeleton className="h-10 w-full" />;
  if (query.isError) return null;
  const services = query.data.services;
  return (
    <section
      aria-label={t("health.strip")}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-card border border-border bg-card px-4 py-2.5"
    >
      <Link
        to="/health"
        className="flex items-center gap-2 rounded-badge text-ui font-medium text-foreground outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
      >
        <StatusDot status={query.data.status} />
        {t(`health.overall.${query.data.status}`)}
      </Link>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {services.map((service) => {
          const problem = serviceProblem(t, service);
          return (
            <li key={service.name}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    tabIndex={0}
                    className="flex items-center gap-1.5 rounded-badge text-xs text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <StatusDot status={service.status} />
                    {serviceLabel(t, service.name)}
                    <span className="sr-only">{t(`health.status.${service.status}`)}</span>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  {t(`health.status.${service.status}`)}
                  {problem ? ` · ${problem}` : ""}
                </TooltipContent>
              </Tooltip>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
