import {
  getMeInvoiceDocumentUrl,
  useMeInvoices,
  useMeSubscription,
  type CustomerInvoice,
  type CustomerSubscription,
} from "@smart-iptv/api-portal";
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@smart-iptv/ui";
import { getRouteApi, Link } from "@tanstack/react-router";
import { ExternalLink, Printer, ReceiptText } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "../lib/auth";
import { localName } from "../lib/local-name";
import { usePageTitle } from "../lib/page-title";
import { useCustomerFormatters } from "../lib/formatters";

const subscriptionApi = getRouteApi("/app/shell/account/subscription");

function SubscriptionDetails({ subscription }: { subscription: CustomerSubscription }) {
  const { t, i18n } = useTranslation();
  const format = useCustomerFormatters();
  return (
    <DescriptionList>
      <DescriptionItem label={t("subscription.plan")}>
        {localName(subscription.plan, i18n.language)}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.status")}>
        <StatusBadge
          status={
            subscription.plan.is_trial && subscription.status === "active"
              ? "trial"
              : subscription.status
          }
        />
      </DescriptionItem>
      <DescriptionItem label={t("subscription.started")}>
        {format.date(subscription.starts_at)}
      </DescriptionItem>
      <DescriptionItem
        label={subscription.status === "expired" ? t("subscription.ended") : t("subscription.ends")}
      >
        {format.date(subscription.ends_at)}
      </DescriptionItem>
      {subscription.grace_until && subscription.status === "grace" ? (
        <DescriptionItem label={t("subscription.graceUntil")}>
          {format.date(subscription.grace_until)}
        </DescriptionItem>
      ) : null}
      <DescriptionItem label={t("subscription.streams")}>
        {format.number(subscription.max_streams)}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.devices")}>
        {format.number(subscription.max_devices)}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.quality")}>
        {subscription.max_quality >= 2160 ? "4K" : `${String(subscription.max_quality)}p`}
      </DescriptionItem>
    </DescriptionList>
  );
}

/** Customers an admin manages by hand have an access profile instead of a subscription. */
function AccessDetails() {
  const { t } = useTranslation();
  const format = useCustomerFormatters();
  const me = useMe();
  const access = me?.access;
  if (!access) return <p className="text-sm text-muted-foreground">{t("subscription.none")}</p>;
  return (
    <DescriptionList>
      <DescriptionItem label={t("subscription.status")}>
        <StatusBadge status={access.status} />
      </DescriptionItem>
      <DescriptionItem label={t("subscription.ends")}>
        {access.expires_at ? format.date(access.expires_at) : t("subscription.noEnd")}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.streams")}>
        {format.number(access.max_streams)}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.devices")}>
        {format.number(access.max_devices)}
      </DescriptionItem>
      <DescriptionItem label={t("subscription.quality")}>
        {access.max_quality >= 2160 ? "4K" : `${String(access.max_quality)}p`}
      </DescriptionItem>
    </DescriptionList>
  );
}

/** Open the invoice's print-ready HTML (same origin, session cookie) and print it. */
function printInvoice(url: string): void {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.position = "fixed";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  frame.src = url;
  frame.addEventListener("load", () => {
    try {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    } catch {
      window.open(url, "_blank", "noopener");
    }
    window.setTimeout(() => {
      frame.remove();
    }, 60_000);
  });
  document.body.append(frame);
}

function InvoiceActions({ invoice, url }: { invoice: CustomerInvoice; url: string }) {
  const { t } = useTranslation();
  const label = invoice.number ?? t("invoices.unnumbered");
  return (
    <div className="flex justify-end gap-1">
      <Button asChild variant="ghost" size="sm">
        <a
          href={url}
          target="_blank"
          rel="noopener"
          aria-label={t("invoices.viewLabel", { number: label })}
        >
          <ExternalLink aria-hidden="true" />
          {t("invoices.view")}
        </a>
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t("invoices.print", { number: label })}
        onClick={() => {
          printInvoice(url);
        }}
      >
        <Printer aria-hidden="true" />
      </Button>
    </div>
  );
}

/** The invoice number, or "awaiting payment" for a checkout not paid yet (numbers come with payment). */
function InvoiceNumber({ invoice }: { invoice: CustomerInvoice }) {
  const { t } = useTranslation();
  return invoice.number ? (
    <span className="font-mono text-xs" dir="ltr">
      {invoice.number}
    </span>
  ) : (
    <span className="text-xs text-muted-foreground">{t("invoices.unnumbered")}</span>
  );
}

function InvoiceRows({ invoices }: { invoices: readonly CustomerInvoice[] }) {
  const { t, i18n } = useTranslation();
  const format = useCustomerFormatters();
  const locale = i18n.language === "ar" ? "ar" : "en";
  const rows = invoices.map((invoice) => ({
    invoice,
    url: getMeInvoiceDocumentUrl(invoice.id, { locale }),
    date: format.date(invoice.issued_at ?? invoice.created_at),
    plan: localName(invoice.plan, i18n.language),
    total: format.money(invoice.total, invoice.currency),
  }));
  return (
    <>
      {/* Phones: one card per invoice. */}
      <ul role="list" className="grid gap-3 sm:hidden">
        {rows.map(({ invoice, url, date, plan, total }) => (
          <li key={invoice.id} className="grid gap-2 rounded-card border border-border bg-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="grid gap-0.5">
                <span className="text-sm font-medium text-foreground">{plan}</span>
                <InvoiceNumber invoice={invoice} />
              </div>
              <StatusBadge status={invoice.status} />
            </div>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="text-muted-foreground">{date}</span>
              <span className="font-semibold tabular-nums text-foreground">{total}</span>
            </div>
            <InvoiceActions invoice={invoice} url={url} />
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto rounded-card border border-border sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("invoices.number")}</TableHead>
              <TableHead>{t("invoices.date")}</TableHead>
              <TableHead>{t("invoices.plan")}</TableHead>
              <TableHead className="text-end">{t("invoices.total")}</TableHead>
              <TableHead>{t("invoices.status")}</TableHead>
              <TableHead>
                <span className="sr-only">{t("invoices.actions")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ invoice, url, date, plan, total }) => (
              <TableRow key={invoice.id}>
                <TableCell>
                  <InvoiceNumber invoice={invoice} />
                </TableCell>
                <TableCell>{date}</TableCell>
                <TableCell>{plan}</TableCell>
                <TableCell className="text-end tabular-nums">{total}</TableCell>
                <TableCell>
                  <StatusBadge status={invoice.status} />
                </TableCell>
                <TableCell>
                  <InvoiceActions invoice={invoice} url={url} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
}

function Invoices() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const invoices = useMeInvoices({ page, page_size: 20 });
  return (
    <section aria-labelledby="invoices" className="grid gap-3">
      <h2 id="invoices" className="text-lg font-semibold text-foreground">
        {t("invoices.title")}
      </h2>
      {invoices.isError ? (
        <ErrorState
          error={invoices.error}
          onRetry={() => {
            void invoices.refetch();
          }}
        />
      ) : invoices.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : invoices.data.results.length === 0 ? (
        <EmptyState
          icon={<ReceiptText />}
          title={t("invoices.emptyTitle")}
          description={t("invoices.empty")}
        />
      ) : (
        <>
          <InvoiceRows invoices={invoices.data.results} />
          {invoices.data.next || invoices.data.previous ? (
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={!invoices.data.previous}
                onClick={() => {
                  setPage((current) => Math.max(1, current - 1));
                }}
              >
                {t("invoices.previous")}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!invoices.data.next}
                onClick={() => {
                  setPage((current) => current + 1);
                }}
              >
                {t("invoices.next")}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

/** Subscription status and plan, a waiting renewal, the invoices; renew through the plans page. */
export function SubscriptionPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("subscription.title"));
  const format = useCustomerFormatters();
  const { checkout } = subscriptionApi.useSearch();
  const subscription = useMeSubscription({ query: { refetchOnMount: "always" } });
  const current = subscription.data?.subscription ?? null;
  const pending = subscription.data?.pending ?? null;

  return (
    <div className="page-top mx-auto grid max-w-5xl gap-8 px-4 sm:px-6">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">
          {t("subscription.title")}
        </h1>
      </div>
      {checkout === "done" ? <Alert tone="success">{t("subscription.checkoutDone")}</Alert> : null}
      {checkout === "cancelled" ? (
        <Alert tone="info">{t("subscription.checkoutCancelled")}</Alert>
      ) : null}
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <CardTitle>{t("subscription.current")}</CardTitle>
            <CardDescription>{t("subscription.currentHint")}</CardDescription>
          </div>
          <Button asChild>
            <Link to="/plans">{current ? t("subscription.renew") : t("subscription.choose")}</Link>
          </Button>
        </CardHeader>
        <CardContent className="grid gap-4">
          {subscription.isError ? (
            <ErrorState
              error={subscription.error}
              onRetry={() => {
                void subscription.refetch();
              }}
            />
          ) : subscription.isPending ? (
            <Skeleton className="h-28 w-full" />
          ) : current ? (
            <SubscriptionDetails subscription={current} />
          ) : (
            <AccessDetails />
          )}
          {pending ? (
            <Alert tone="info">
              {t("subscription.pending", {
                plan: localName(pending.plan, i18n.language),
                date: format.date(pending.starts_at),
              })}
            </Alert>
          ) : null}
        </CardContent>
      </Card>
      <Invoices />
    </div>
  );
}
