import {
  getMonitoringStatusQueryKey,
  isApiError,
  useMonitoringStatus,
  useMonitoringTestAlert,
  useMonitoringTicket,
  type MonitoringStatus,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  PageHeader,
  Skeleton,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { BellRing, ChartLine, ExternalLink, Gauge, PowerOff, Siren } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

const route = getRouteApi("/app/monitoring");

/** The monitoring UIs on grafana.<domain> (ADR-0018), each opened with a one-time link. */
const TOOLS: readonly {
  id: "grafana" | "prometheus" | "alertmanager";
  path: string;
  icon: ReactNode;
}[] = [
  { id: "grafana", path: "/", icon: <Gauge aria-hidden="true" /> },
  { id: "prometheus", path: "/prometheus/", icon: <ChartLine aria-hidden="true" /> },
  { id: "alertmanager", path: "/alertmanager/", icon: <BellRing aria-hidden="true" /> },
];

function YesNo({ value }: { value: boolean }) {
  const { t } = useTranslation();
  return (
    <Badge tone={value ? "success" : "neutral"} dot>
      {value ? t("monitoring.on") : t("monitoring.off")}
    </Badge>
  );
}

/** Alert delivery (settings) and the deployment values that need a restart (.env). */
function Delivery({ status }: { status: MonitoringStatus }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const testAlert = useMonitoringTestAlert();
  const send = (stop: boolean) => {
    testAlert.mutate(
      { data: stop ? { stop: true } : { minutes: 10 } },
      {
        onSuccess: () => {
          toast.success(stop ? t("monitoring.test.stopped") : t("monitoring.test.started"));
          void queryClient.invalidateQueries({ queryKey: getMonitoringStatusQueryKey() });
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  };
  const deployment = status.deployment;
  return (
    <div className="grid gap-(--density-page) lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>{t("monitoring.delivery.title")}</CardTitle>
          <CardDescription>{t("monitoring.delivery.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <DescriptionList>
            <DescriptionItem label={t("monitoring.delivery.alerts")}>
              <YesNo value={status.alerts_enabled} />
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.delivery.email")}>
              {status.email_configured
                ? t("monitoring.delivery.recipients", { count: status.email_recipients })
                : t("monitoring.delivery.noSmtp")}
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.delivery.telegram")}>
              <YesNo value={status.telegram_configured} />
            </DescriptionItem>
          </DescriptionList>
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          {can("settings.view") ? (
            <Button asChild variant="secondary">
              <Link to="/settings">{t("monitoring.delivery.edit")}</Link>
            </Button>
          ) : null}
          {can("settings.edit") ? (
            status.fire_drill_active ? (
              <Button
                variant="secondary"
                pending={testAlert.isPending}
                onClick={() => {
                  send(true);
                }}
              >
                {t("monitoring.test.stop")}
              </Button>
            ) : (
              <Button
                pending={testAlert.isPending}
                onClick={() => {
                  send(false);
                }}
              >
                <Siren aria-hidden="true" />
                {t("monitoring.test.send")}
              </Button>
            )
          ) : null}
        </CardFooter>
        {status.fire_drill_active ? (
          <CardContent>
            <Alert tone="info" title={t("monitoring.test.runningTitle")}>
              {t("monitoring.test.running")}
            </Alert>
          </CardContent>
        ) : null}
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("monitoring.deployment.title")}</CardTitle>
          <CardDescription>{t("monitoring.deployment.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <DescriptionList>
            <DescriptionItem label={t("monitoring.deployment.enabled")}>
              <YesNo value={status.enabled} />
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.deployment.metrics")}>
              {deployment.prometheus_retention
                ? t("monitoring.deployment.metricsValue", {
                    time: deployment.prometheus_retention,
                    size: deployment.prometheus_retention_size,
                  })
                : null}
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.deployment.logs")}>
              {deployment.loki_retention || null}
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.deployment.fallbackEmail")}>
              <YesNo value={deployment.fallback_email} />
            </DescriptionItem>
            <DescriptionItem label={t("monitoring.deployment.fallbackTelegram")}>
              <YesNo value={deployment.fallback_telegram} />
            </DescriptionItem>
          </DescriptionList>
          <p className="mt-3 text-xs text-muted-foreground">{t("monitoring.deployment.restart")}</p>
        </CardContent>
      </Card>
    </div>
  );
}

function Monitoring() {
  const { t } = useTranslation();
  const { next, failed } = route.useSearch();
  const status = useMonitoringStatus();
  const ticket = useMonitoringTicket();
  const [opening, setOpening] = useState<string | null>(null);
  const started = useRef(false);

  const open = (path: string) => {
    setOpening(path);
    ticket.mutate(
      { data: { next: path } },
      {
        onSuccess: (link) => {
          window.location.assign(link.url);
        },
        onError: (error) => {
          setOpening(null);
          if (!(isApiError(error) && error.code === "NOT_FOUND")) notifyError(t, error);
        },
      },
    );
  };

  // Sent here by grafana.<domain> to sign in: carry on to the page that was asked for.
  useEffect(() => {
    if (next !== undefined && failed !== true && !started.current) {
      started.current = true;
      open(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on arrival
  }, []);

  const disabled =
    (isApiError(ticket.error) && ticket.error.code === "NOT_FOUND") ||
    status.data?.enabled === false;
  if (disabled) {
    return (
      <EmptyState
        className="min-h-[50vh]"
        icon={<PowerOff />}
        title={t("monitoring.disabled.title")}
        description={t("monitoring.disabled.description")}
      />
    );
  }

  return (
    <div className="grid gap-(--density-page)">
      {failed === true ? (
        <Alert tone="warning" title={t("monitoring.failed.title")}>
          {t("monitoring.failed.description")}
        </Alert>
      ) : null}
      {next !== undefined && failed !== true && opening !== null ? (
        <Alert tone="info" title={t("monitoring.opening")}>
          {t("monitoring.openingDescription")}
        </Alert>
      ) : null}
      <div className="grid gap-(--density-page) md:grid-cols-3">
        {TOOLS.map((tool) => (
          <Card key={tool.id}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 [&>svg]:size-4 [&>svg]:text-muted-foreground">
                {tool.icon}
                {t(`monitoring.tools.${tool.id}.title`)}
              </CardTitle>
              <CardDescription>{t(`monitoring.tools.${tool.id}.description`)}</CardDescription>
            </CardHeader>
            <CardContent />
            <CardFooter>
              <Button
                variant={tool.id === "grafana" ? "primary" : "secondary"}
                pending={opening === tool.path}
                disabled={opening !== null && opening !== tool.path}
                onClick={() => {
                  open(tool.path);
                }}
              >
                <ExternalLink aria-hidden="true" className="rtl:-scale-x-100" />
                {t("monitoring.open")}
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>
      {status.isPending ? (
        <Skeleton className="h-56" />
      ) : status.isError ? (
        <QueryError
          error={status.error}
          onRetry={() => {
            void status.refetch();
          }}
        />
      ) : (
        <Delivery status={status.data} />
      )}
      <p className="text-ui text-muted-foreground">{t("monitoring.note")}</p>
    </div>
  );
}

export function MonitoringPage() {
  const { t } = useTranslation();
  usePageTitle(t("monitoring.title"));
  return (
    <RequirePermission permission="monitoring.view">
      <PageHeader title={t("monitoring.title")} description={t("monitoring.description")} />
      <Monitoring />
    </RequirePermission>
  );
}
