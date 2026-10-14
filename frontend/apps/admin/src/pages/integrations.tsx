import { useIntegrationsList } from "@smart-iptv/api";
import { Card, PageHeader, Skeleton } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { GeoIpCard } from "../features/integrations/geoip-card";
import { IntegrationCard } from "../features/integrations/integration-card";
import { SettingsTabs } from "../features/settings/settings-tabs";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

function IntegrationsSkeleton() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-live="polite" className="grid gap-4 lg:grid-cols-2">
      <span className="sr-only">{t("layout.loading")}</span>
      {[0, 1, 2, 3].map((card) => (
        <div key={card} className="grid gap-4 rounded-card border border-border bg-card p-5">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-full max-w-80" />
          <Skeleton className="h-8 w-full max-w-72" />
          <Skeleton className="h-8 w-full max-w-72" />
        </div>
      ))}
    </div>
  );
}

/** Outside services, one card each: their settings, status, and a server-side Test. */
function Integrations() {
  const { t } = useTranslation();
  const can = useCan();
  const canEdit = can("settings.edit");
  const query = useIntegrationsList();

  return (
    <div className="grid gap-6">
      <PageHeader
        className="pb-0"
        title={t("integrations.title")}
        description={canEdit ? t("integrations.description") : t("settings.readOnly")}
      />
      <SettingsTabs />
      {query.isPending ? (
        <IntegrationsSkeleton />
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
        <div className="grid items-start gap-4 lg:grid-cols-2 [&>*]:min-w-0">
          {query.data.map((integration) => (
            <IntegrationCard key={integration.name} integration={integration} canEdit={canEdit} />
          ))}
          <GeoIpCard canEdit={canEdit} />
        </div>
      )}
    </div>
  );
}

export function IntegrationsPage() {
  const { t } = useTranslation();
  usePageTitle(t("integrations.title"));
  return (
    <RequirePermission permission="settings.view">
      <Integrations />
    </RequirePermission>
  );
}
