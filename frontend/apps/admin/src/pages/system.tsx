import { useSystemInfo, type BootstrapSecret, type SystemInfo } from "@smart-iptv/api";
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
  PageHeader,
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
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { SettingsTabs } from "../features/settings/settings-tabs";
import { usePageTitle } from "../lib/page-title";

const LOCATION_TONES: Record<BootstrapSecret["location"], BadgeTone> = {
  environment: "neutral",
  file: "info",
  missing: "warning",
};

function OnOff({ value }: { value: boolean }) {
  const { t } = useTranslation();
  return (
    <Badge tone={value ? "success" : "neutral"} dot>
      {value ? t("system.on") : t("system.off")}
    </Badge>
  );
}

function Mono({ children }: { children: string }) {
  const { t } = useTranslation();
  return children ? (
    <code className="font-mono text-xs break-all" dir="ltr">
      {children}
    </code>
  ) : (
    <span className="text-muted-foreground">{t("system.unknown")}</span>
  );
}

function Installation({ info }: { info: SystemInfo }) {
  const { t } = useTranslation();
  const format = useFormatters(info.time_zone);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("system.installation.title")}</CardTitle>
        <CardDescription>{t("system.installation.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <DescriptionList>
          <DescriptionItem label={t("system.installation.environment")}>
            <Badge tone={info.environment === "production" ? "success" : "warning"}>
              {t(`system.environment.${info.environment}`)}
            </Badge>
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.version")}>
            <Mono>{info.version}</Mono>
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.commit")}>
            <Mono>{info.commit}</Mono>
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.domain")}>
            <Mono>{info.domain}</Mono>
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.time")}>
            {format.dateTime(info.server_time)} ({info.time_zone})
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.monitoring")}>
            <OnOff value={info.monitoring_enabled} />
          </DescriptionItem>
          <DescriptionItem label={t("system.installation.liveIngest")}>
            <OnOff value={info.live_ingest_enabled} />
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function Hosts({ info }: { info: SystemInfo }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("system.hosts.title")}</CardTitle>
        <CardDescription>{t("system.hosts.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <DescriptionList>
          {info.hosts.map((host) => (
            <DescriptionItem key={host.role} label={t(`system.hosts.roles.${host.role}`)}>
              <a
                href={host.url}
                target="_blank"
                rel="noreferrer"
                dir="ltr"
                className="font-mono text-xs break-all text-primary underline-offset-4 hover:underline"
              >
                {host.url}
              </a>
            </DescriptionItem>
          ))}
          <DescriptionItem label={t("system.paths.mediaRoot")}>
            <Mono>{info.media_root}</Mono>
          </DescriptionItem>
          <DescriptionItem label={t("system.paths.libraryRoot")}>
            <Mono>{info.library_root}</Mono>
          </DescriptionItem>
          <DescriptionItem label={t("system.paths.dataRoot")}>
            <Mono>{info.data_root}</Mono>
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function ServerConfig({ info }: { info: SystemInfo }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("system.serverConfig.title")}</CardTitle>
        <CardDescription>{t("system.serverConfig.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {info.from_server_config.length === 0 ? (
          <p className="text-ui text-muted-foreground">{t("system.serverConfig.none")}</p>
        ) : (
          <ul className="grid gap-2">
            {info.from_server_config.map((setting) => (
              <li key={setting.key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-ui font-medium">
                  {t(`settings.keys.${setting.key}.label`, { defaultValue: setting.key })}
                </span>
                <code className="font-mono text-xs text-muted-foreground" dir="ltr">
                  {setting.env.join(", ")}
                </code>
              </li>
            ))}
          </ul>
        )}
        <div>
          <Button asChild variant="secondary" size="sm">
            <Link to="/settings/integrations">{t("system.serverConfig.open")}</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Secrets({ info }: { info: SystemInfo }) {
  const { t } = useTranslation();
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>{t("system.secrets.title")}</CardTitle>
        <CardDescription>{t("system.secrets.description")}</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("system.secrets.name")}</TableHead>
              <TableHead>{t("system.secrets.location")}</TableHead>
              <TableHead>{t("system.secrets.why")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {info.secrets.map((secret) => (
              <TableRow key={secret.name}>
                <TableCell>
                  <code className="font-mono text-xs" dir="ltr">
                    {secret.name}
                  </code>
                </TableCell>
                <TableCell>
                  <Badge
                    tone={
                      secret.location === "missing" && !secret.required
                        ? "neutral"
                        : LOCATION_TONES[secret.location]
                    }
                  >
                    {t(
                      `system.secrets.where.${secret.location === "missing" && !secret.required ? "unused" : secret.location}`,
                    )}
                  </Badge>
                </TableCell>
                <TableCell className="min-w-56 text-ui text-muted-foreground">
                  {t(`system.secrets.reasons.${secret.reason}`)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function Rotations({ info }: { info: SystemInfo }) {
  const { t } = useTranslation();
  const format = useFormatters(info.time_zone);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("system.rotations.title")}</CardTitle>
        <CardDescription>{t("system.rotations.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {info.key_rotations.length === 0 ? (
          <p className="text-ui text-muted-foreground">{t("system.rotations.none")}</p>
        ) : (
          <DescriptionList>
            {info.key_rotations.map((rotation) => (
              <DescriptionItem
                key={rotation.kind}
                label={t(`system.rotations.kinds.${rotation.kind}`, {
                  defaultValue: rotation.kind,
                })}
              >
                {format.dateTime(rotation.at)}
                {rotation.actor ? ` · ${rotation.actor}` : ""}
              </DescriptionItem>
            ))}
          </DescriptionList>
        )}
      </CardContent>
    </Card>
  );
}

/** Read-only facts about this installation, so nothing important needs SSH to look at. */
function System() {
  const { t } = useTranslation();
  const query = useSystemInfo();
  return (
    <div className="grid gap-6">
      <PageHeader
        className="pb-0"
        title={t("system.title")}
        description={t("system.description")}
      />
      <SettingsTabs />
      {query.isPending ? (
        <div role="status" aria-live="polite" className="grid gap-4 lg:grid-cols-2">
          <span className="sr-only">{t("layout.loading")}</span>
          {[0, 1, 2, 3].map((card) => (
            <Skeleton key={card} className="h-48 w-full" />
          ))}
        </div>
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
          <Installation info={query.data} />
          <Hosts info={query.data} />
          <ServerConfig info={query.data} />
          <Rotations info={query.data} />
          <Secrets info={query.data} />
        </div>
      )}
    </div>
  );
}

export function SystemPage() {
  const { t } = useTranslation();
  usePageTitle(t("system.title"));
  return (
    <RequirePermission permission="settings.view">
      <System />
    </RequirePermission>
  );
}
