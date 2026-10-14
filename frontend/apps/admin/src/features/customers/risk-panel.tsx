import {
  getCustomersListQueryKey,
  getCustomersRetrieveQueryKey,
  getCustomersRiskQueryKey,
  useCustomersRisk,
  useCustomersRiskResetCredentials,
  useCustomersRiskReview,
  useCustomersRiskSuspend,
  useCustomersRiskWarn,
  type Assessment,
  type RiskFlag,
  type Sighting,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  DbipAttribution,
  EmptyState,
  Label,
  RelativeTime,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Textarea,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Globe2, Plane, ShieldAlert, ShieldCheck } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { notifyError } from "../../lib/problems";

/** A country code as its name in the admin's language, falling back to the code. */
function countryName(code: string, locale: string): string {
  if (!code) return "";
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function Signals({ assessment }: { assessment: Assessment }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  return (
    <div className="grid gap-3">
      <ul className="grid gap-2 sm:grid-cols-2">
        {assessment.signals.map((signal) => {
          const over = signal.points > 0;
          return (
            <li
              key={signal.name}
              className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
            >
              <span className="text-ui text-foreground">{t(`risk.signals.${signal.name}`)}</span>
              <Badge tone={over ? "warning" : "neutral"}>
                <span dir="ltr" className="tabular-nums">
                  {signal.name === "impossible_travel"
                    ? format.number(signal.value)
                    : `${format.number(signal.value)} / ${format.number(signal.limit)}`}
                </span>
              </Badge>
            </li>
          );
        })}
      </ul>
      {assessment.travel.length > 0 ? (
        <ul className="grid gap-1.5">
          {assessment.travel.map((event) => (
            <li
              key={`${event.at}-${event.from_country}-${event.to_country}`}
              className="flex items-center gap-2 text-ui text-foreground"
            >
              <Plane aria-hidden="true" className="size-4 shrink-0 text-warning-text" />
              {t("risk.travel", {
                from: countryName(event.from_country, i18n.language),
                to: countryName(event.to_country, i18n.language),
                km: format.number(event.km),
                hours: format.number(event.hours, { maximumFractionDigits: 1 }),
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function SightingsTable({ sightings }: { sightings: readonly Sighting[] }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  if (sightings.length === 0) {
    return <EmptyState className="py-6" icon={<Globe2 />} title={t("risk.sightings.empty")} />;
  }
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("risk.sightings.network")}</TableHead>
            <TableHead>{t("risk.sightings.provider")}</TableHead>
            <TableHead>{t("risk.sightings.country")}</TableHead>
            <TableHead>{t("risk.sightings.device")}</TableHead>
            <TableHead>{t("risk.sightings.source")}</TableHead>
            <TableHead className="text-end">{t("risk.sightings.hits")}</TableHead>
            <TableHead>{t("risk.sightings.lastSeen")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sightings.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <span dir="ltr" className="font-mono text-xs">
                  {item.network === "local" ? t("risk.sightings.local") : item.network}
                </span>
              </TableCell>
              <TableCell className="max-w-48 truncate">
                {item.asn_org || (item.asn ? `AS${String(item.asn)}` : "—")}
              </TableCell>
              <TableCell>{countryName(item.country, i18n.language) || "—"}</TableCell>
              <TableCell className="max-w-40 truncate">{item.device_name || "—"}</TableCell>
              <TableCell>{t(`risk.sources.${item.source}`)}</TableCell>
              <TableCell className="text-end tabular-nums">{format.number(item.hits)}</TableCell>
              <TableCell>
                <RelativeTime value={item.last_seen} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ReviewButton({ customerId, onDone }: { customerId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const [note, setNote] = useState("");
  const noteId = useId();
  const review = useCustomersRiskReview();
  return (
    <ConfirmDialog
      title={t("risk.review.title")}
      description={t("risk.review.description")}
      confirmLabel={t("risk.review.confirm")}
      cancelLabel={t("common.cancel")}
      onConfirm={async () => {
        try {
          await review.mutateAsync({ id: customerId, data: { note } });
          toast.success(t("risk.review.done"));
          setNote("");
          onDone();
        } catch (error) {
          notifyError(t, error);
          throw error;
        }
      }}
      trigger={
        <Button variant="secondary" size="sm">
          <ShieldCheck aria-hidden="true" />
          {t("risk.review.action")}
        </Button>
      }
    >
      <div className="grid gap-1.5">
        <Label htmlFor={noteId}>{t("risk.review.noteLabel")}</Label>
        <Textarea
          id={noteId}
          rows={3}
          maxLength={2000}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
      </div>
    </ConfirmDialog>
  );
}

function FlagSummary({ flag }: { flag: RiskFlag }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <div className="grid gap-1 text-ui">
      <p className="text-foreground">
        {flag.status === "open"
          ? t("risk.flag.open", { score: format.number(flag.score) })
          : t("risk.flag.reviewed", {
              name: flag.reviewed_by_name || t("risk.flag.someone"),
              date: flag.reviewed_at ? format.dateTime(flag.reviewed_at) : "",
            })}
      </p>
      <p className="text-xs text-muted-foreground">
        {t("risk.flag.opened", { date: format.dateTime(flag.opened_at) })}
        {flag.actions.length > 0
          ? ` · ${flag.actions.map((entry) => t(`risk.actions.${entry.action}`)).join(" → ")}`
          : ""}
      </p>
      {flag.note ? <p className="text-xs text-muted-foreground">{flag.note}</p> : null}
    </div>
  );
}

/**
 * Anti-sharing (SPEC §11.1; ADR-0021): the customer's score with its evidence, the networks
 * they were seen on, and the escalation ladder — warn, reset the app logins, suspend.
 * Nothing happens on its own: a flag only asks an admin to look.
 */
export function RiskPanel({ customerId, active }: { customerId: string; active: boolean }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const queryClient = useQueryClient();
  const query = useCustomersRisk(customerId);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: getCustomersRiskQueryKey(customerId) });
    void queryClient.invalidateQueries({ queryKey: getCustomersListQueryKey() });
    void queryClient.invalidateQueries({ queryKey: getCustomersRetrieveQueryKey(customerId) });
  };
  const failed = (error: unknown): never => {
    notifyError(t, error);
    throw error;
  };
  const warn = useCustomersRiskWarn();
  const reset = useCustomersRiskResetCredentials();
  const suspend = useCustomersRiskSuspend();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlert aria-hidden="true" className="size-4 text-muted-foreground" />
          {t("risk.title")}
        </CardTitle>
        <CardDescription>{t("risk.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {query.isPending ? (
          <div className="grid gap-3" role="status">
            <span className="sr-only">{t("layout.loading")}</span>
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : (
          <>
            {!query.data.enabled ? <Alert tone="info">{t("risk.disabled")}</Alert> : null}
            {!query.data.geoip_installed ? (
              <Alert tone="warning">{t("risk.noDatabase")}</Alert>
            ) : null}
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={query.data.assessment.flagged ? "danger" : "success"} dot>
                {query.data.assessment.flagged ? t("risk.flagged") : t("risk.clear")}
              </Badge>
              <span className="text-ui text-muted-foreground">
                {t("risk.score", { score: format.number(query.data.assessment.score) })}
              </span>
            </div>
            {query.data.flag ? <FlagSummary flag={query.data.flag} /> : null}
            <Signals assessment={query.data.assessment} />
            <div className="flex flex-wrap gap-2">
              {can("customers.edit") ? (
                <ConfirmDialog
                  title={t("risk.warn.title")}
                  description={t("risk.warn.description")}
                  confirmLabel={t("risk.warn.confirm")}
                  cancelLabel={t("common.cancel")}
                  onConfirm={async () => {
                    try {
                      await warn.mutateAsync({ id: customerId });
                      toast.success(t("risk.warn.done"));
                      refresh();
                    } catch (error) {
                      failed(error);
                    }
                  }}
                  trigger={
                    <Button variant="secondary" size="sm">
                      {t("risk.warn.action")}
                    </Button>
                  }
                />
              ) : null}
              {can("devices.manage") ? (
                <ConfirmDialog
                  tone="danger"
                  title={t("risk.reset.title")}
                  description={t("risk.reset.description")}
                  confirmLabel={t("risk.reset.confirm")}
                  cancelLabel={t("common.cancel")}
                  onConfirm={async () => {
                    try {
                      const result = await reset.mutateAsync({ id: customerId });
                      toast.success(t("risk.reset.done", { count: result.logins }));
                      refresh();
                    } catch (error) {
                      failed(error);
                    }
                  }}
                  trigger={
                    <Button variant="secondary" size="sm">
                      {t("risk.reset.action")}
                    </Button>
                  }
                />
              ) : null}
              {can("customers.edit") && active ? (
                <ConfirmDialog
                  tone="danger"
                  title={t("risk.suspend.title")}
                  description={t("risk.suspend.description")}
                  confirmLabel={t("risk.suspend.confirm")}
                  cancelLabel={t("common.cancel")}
                  onConfirm={async () => {
                    try {
                      await suspend.mutateAsync({ id: customerId });
                      toast.success(t("risk.suspend.done"));
                      refresh();
                    } catch (error) {
                      failed(error);
                    }
                  }}
                  trigger={
                    <Button variant="secondary" size="sm" className="text-danger-text">
                      {t("risk.suspend.action")}
                    </Button>
                  }
                />
              ) : null}
              {can("customers.edit") && query.data.flag?.status === "open" ? (
                <ReviewButton customerId={customerId} onDone={refresh} />
              ) : null}
            </div>
            <div className="grid gap-2">
              <h3 className="text-ui font-medium text-foreground">{t("risk.sightings.title")}</h3>
              <SightingsTable sightings={query.data.sightings} />
            </div>
            <DbipAttribution />
          </>
        )}
      </CardContent>
    </Card>
  );
}
