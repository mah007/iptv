import {
  getGeoipStatusQueryKey,
  useGeoipLookup,
  useGeoipStatus,
  useGeoipUpdate,
  type GeoDatabase,
  type GeoLookup,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  DbipAttribution,
  Input,
  Label,
  Skeleton,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Globe2, RefreshCw, Search } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { notifyError } from "../../lib/problems";

function DatabaseRow({ name, database }: { name: "country" | "asn"; database: GeoDatabase }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <span className="text-ui text-foreground">{t(`geoip.databases.${name}`)}</span>
      {database.installed ? (
        <span className="text-xs text-muted-foreground">
          {t("geoip.release", {
            date: database.built_at
              ? new Intl.DateTimeFormat(format.locale, {
                  year: "numeric",
                  month: "long",
                  timeZone: "UTC",
                }).format(new Date(database.built_at))
              : "—",
            size: format.bytes(database.size),
          })}
        </span>
      ) : (
        <Badge tone="warning">{t("geoip.missing")}</Badge>
      )}
    </li>
  );
}

function LookupResult({ result }: { result: GeoLookup }) {
  const { t, i18n } = useTranslation();
  let country = result.country;
  if (country) {
    try {
      country = new Intl.DisplayNames([i18n.language], { type: "region" }).of(country) ?? country;
    } catch {
      // An unknown code stays as it is.
    }
  }
  return (
    <p className="text-ui text-foreground" aria-live="polite">
      <span dir="ltr" className="font-mono text-xs">
        {result.ip}
      </span>
      {" · "}
      {result.local
        ? t("geoip.local")
        : t("geoip.found", {
            country: country || t("geoip.unknown"),
            provider:
              result.asn_org || (result.asn ? `AS${String(result.asn)}` : t("geoip.unknown")),
          })}
    </p>
  );
}

/**
 * IP geolocation (ADR-0021): DB-IP Lite's country and ASN databases, which country rules
 * and the anti-sharing score read. Updated by a monthly job; licensed CC BY 4.0, so the
 * card credits DB-IP.
 */
export function GeoIpCard({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useGeoipStatus();
  const update = useGeoipUpdate();
  const lookup = useGeoipLookup();
  const [ip, setIp] = useState("");
  const [result, setResult] = useState<GeoLookup | null>(null);
  const ipId = useId();

  const installed = query.data?.databases.country.installed === true;
  const tone = !query.data?.enabled ? "neutral" : installed ? "success" : "warning";
  const status = !query.data?.enabled
    ? t("geoip.status.off")
    : installed
      ? t("geoip.status.ready")
      : t("geoip.status.missing");

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="flex items-center gap-2">
            <Globe2 aria-hidden="true" className="size-4 text-muted-foreground" />
            {t("geoip.title")}
          </CardTitle>
          {query.data ? (
            <Badge tone={tone} dot>
              {status}
            </Badge>
          ) : null}
        </div>
        <CardDescription>{t("geoip.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {query.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : (
          <>
            <ul className="divide-y divide-border">
              <DatabaseRow name="country" database={query.data.databases.country} />
              <DatabaseRow name="asn" database={query.data.databases.asn} />
            </ul>
            <p className="text-xs text-muted-foreground">
              {query.data.auto_update ? t("geoip.autoUpdate") : t("geoip.manualUpdate")}
            </p>
          </>
        )}
        <form
          className="grid gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            const address = ip.trim();
            lookup.mutate(
              { data: address ? { ip: address } : {} },
              {
                onSuccess: setResult,
                onError: (error) => {
                  notifyError(t, error);
                },
              },
            );
          }}
        >
          <Label htmlFor={ipId}>{t("geoip.lookupLabel")}</Label>
          <div className="flex gap-2">
            <Input
              id={ipId}
              dir="ltr"
              inputMode="text"
              placeholder={t("geoip.lookupPlaceholder")}
              value={ip}
              onChange={(event) => {
                setIp(event.target.value);
              }}
            />
            <Button type="submit" variant="secondary" pending={lookup.isPending}>
              <Search aria-hidden="true" />
              {t("geoip.lookup")}
            </Button>
          </div>
          {result ? <LookupResult result={result} /> : null}
        </form>
      </CardContent>
      <CardFooter className="mt-auto flex flex-wrap items-center justify-between gap-3">
        <DbipAttribution />
        {canEdit ? (
          <Button
            variant="secondary"
            size="sm"
            pending={update.isPending}
            onClick={() => {
              update.mutate(undefined, {
                onSuccess: () => {
                  toast.success(t("geoip.updateQueued"));
                  window.setTimeout(() => {
                    void queryClient.invalidateQueries({ queryKey: getGeoipStatusQueryKey() });
                  }, 15_000);
                },
                onError: (error) => {
                  notifyError(t, error);
                },
              });
            }}
          >
            <RefreshCw aria-hidden="true" />
            {t("geoip.update")}
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  );
}
