import {
  getInvoicesDocumentUrl,
  useCustomersPasswordInvite,
  useInvoicesList,
  usePaymentsList,
  useSubscriptionsList,
  type CustomerDetail,
  type PasswordInvitation,
  type Subscription,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  CopyField,
  DescriptionItem,
  DescriptionList,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Timeline,
  TimelineItem,
  toast,
  useFormatters,
  type TimelineTone,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { CalendarCheck, FileText, Plus, Receipt } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { notifyError } from "../../lib/problems";
import { NewSubscriptionDialog } from "../billing/new-subscription";
import { RecordPaymentDialog } from "../billing/payment-dialogs";
import { planName } from "../billing/pickers";
import { SubscriptionActions } from "../billing/subscription-actions";
import { ExpiryText } from "./expiry";

/** Statuses that govern access, newest first (SPEC §7.6: one active or grace at a time). */
const GOVERNING = ["active", "grace", "suspended", "pending"] as const;

const TONE: Record<string, TimelineTone> = {
  active: "success",
  grace: "warning",
  suspended: "danger",
  pending: "info",
  expired: "neutral",
  cancelled: "neutral",
};

function useSubscriptions(customerId: string) {
  return useSubscriptionsList({ user: customerId, page_size: 50, ordering: ["-starts_at"] });
}

/** The subscription that governs access now, for the overview (null when none). */
export function GoverningSubscription({ customer }: { customer: CustomerDetail }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const query = useSubscriptionsList({
    user: customer.id,
    status: [...GOVERNING],
    page_size: 1,
    ordering: ["-ends_at"],
  });
  const subscription = query.data?.results[0];
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <CardTitle>{t("customerBilling.subscription.title")}</CardTitle>
        {subscription ? <SubscriptionActions subscription={subscription} /> : null}
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : query.isError ? (
          <QueryError error={query.error} />
        ) : subscription === undefined ? (
          <p className="text-ui text-muted-foreground">{t("customerBilling.subscription.none")}</p>
        ) : (
          <DescriptionList>
            <DescriptionItem label={t("subscriptions.columns.plan")}>
              <bdi>{planName(subscription.plan, i18n.language)}</bdi>
            </DescriptionItem>
            <DescriptionItem label={t("subscriptions.columns.status")}>
              <StatusBadge status={subscription.status} />
            </DescriptionItem>
            <DescriptionItem label={t("subscriptions.columns.starts")}>
              {format.date(subscription.starts_at)}
            </DescriptionItem>
            <DescriptionItem label={t("subscriptions.columns.ends")}>
              <span className="inline-flex flex-wrap items-baseline gap-x-2">
                <span>{format.dateTime(subscription.ends_at)}</span>
                <ExpiryText expiresAt={subscription.ends_at} className="text-xs" />
              </span>
            </DescriptionItem>
            {subscription.status === "grace" && subscription.grace_until ? (
              <DescriptionItem label={t("customerBilling.subscription.graceUntil")}>
                {format.dateTime(subscription.grace_until)}
              </DescriptionItem>
            ) : null}
            <DescriptionItem label={t("subscriptions.columns.source")}>
              {t(`subscriptions.sources.${subscription.source}`)}
            </DescriptionItem>
          </DescriptionList>
        )}
      </CardContent>
    </Card>
  );
}

function SubscriptionEvent({ subscription }: { subscription: Subscription }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  // Blank while the subscription is running (the schema lists only the reasons).
  const endReason: string = subscription.end_reason;
  return (
    <TimelineItem
      title={
        <span className="flex flex-wrap items-center gap-2">
          <bdi>{planName(subscription.plan, i18n.language)}</bdi>
          <StatusBadge status={subscription.status} />
          {subscription.plan.is_trial ? <StatusBadge status="trial" /> : null}
        </span>
      }
      tone={TONE[subscription.status] ?? "neutral"}
      actor={subscription.created_by ? <bdi>{subscription.created_by}</bdi> : null}
      at={subscription.created_at}
    >
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span>
          {t("customerBilling.period", {
            start: format.date(subscription.starts_at),
            end: format.date(subscription.ends_at),
          })}
          {" · "}
          {t(`subscriptions.sources.${subscription.source}`)}
          {endReason ? ` · ${t(`customerBilling.endReasons.${endReason}`)}` : ""}
          {subscription.note ? (
            <>
              {" · "}
              <bdi>{subscription.note}</bdi>
            </>
          ) : null}
        </span>
        <SubscriptionActions subscription={subscription} />
      </span>
    </TimelineItem>
  );
}

/** Every subscription of the customer, newest first (SPEC §8.3.3 Subscriptions tab). */
export function CustomerSubscriptions({ customer }: { customer: CustomerDetail }) {
  const { t } = useTranslation();
  const can = useCan();
  const query = useSubscriptions(customer.id);
  const [creating, setCreating] = useState(false);
  const name = customer.name || customer.username;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{t("customerBilling.subscriptions.title")}</CardTitle>
          <CardDescription>{t("customerBilling.subscriptions.description")}</CardDescription>
        </div>
        {can("subscriptions.edit") ? (
          <Button
            onClick={() => {
              setCreating(true);
            }}
          >
            <Plus aria-hidden="true" />
            {t("subscriptions.new.button")}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.results.length === 0 ? (
          <EmptyState
            className="py-8"
            icon={<CalendarCheck />}
            title={t("customerBilling.subscriptions.empty")}
          />
        ) : (
          <Timeline>
            {query.data.results.map((subscription) => (
              <SubscriptionEvent key={subscription.id} subscription={subscription} />
            ))}
          </Timeline>
        )}
      </CardContent>
      {creating ? (
        <NewSubscriptionDialog
          open={creating}
          onOpenChange={setCreating}
          customer={{ id: customer.id, name }}
        />
      ) : null}
    </Card>
  );
}

/** Payments and invoices of the customer (SPEC §8.3.3 Payments & invoices tab). */
export function CustomerBilling({ customer }: { customer: CustomerDetail }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const payments = usePaymentsList({ user: customer.id, page_size: 20 });
  const invoices = useInvoicesList({ user: customer.id, page_size: 20 });
  const [recording, setRecording] = useState(false);
  const locale = i18n.language === "ar" ? "ar" : "en";
  return (
    <div className="grid gap-4">
      <Card className="p-0">
        <CardHeader className="flex-row items-start justify-between gap-3 p-(--density-card)">
          <CardTitle>{t("payments.title")}</CardTitle>
          {can("billing.manage") ? (
            <Button
              variant="secondary"
              onClick={() => {
                setRecording(true);
              }}
            >
              <Plus aria-hidden="true" />
              {t("payments.record.button")}
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="p-0">
          {payments.isPending ? (
            <Skeleton className="m-4 h-24" />
          ) : payments.isError ? (
            <QueryError error={payments.error} />
          ) : payments.data.results.length === 0 ? (
            <EmptyState className="py-8" icon={<Receipt />} title={t("payments.empty.title")} />
          ) : (
            <div className="overflow-x-auto">
              <Table aria-label={t("payments.title")}>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("payments.columns.paid")}</TableHead>
                    <TableHead className="text-end">{t("payments.columns.amount")}</TableHead>
                    <TableHead>{t("payments.columns.status")}</TableHead>
                    <TableHead>{t("payments.columns.method")}</TableHead>
                    <TableHead>{t("payments.columns.plan")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {payments.data.results.map((payment) => (
                    <TableRow key={payment.id}>
                      <TableCell>
                        <Link
                          to="/payments"
                          search={{ payment: payment.id }}
                          className="whitespace-nowrap hover:underline"
                        >
                          {payment.paid_at
                            ? format.dateTime(payment.paid_at)
                            : format.dateTime(payment.created_at)}
                        </Link>
                      </TableCell>
                      <TableCell className="text-end tabular-nums">
                        {format.money(payment.amount, payment.currency)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={payment.status} />
                      </TableCell>
                      <TableCell>{t(`payments.methods.${payment.method}`)}</TableCell>
                      <TableCell>
                        {payment.plan ? <bdi>{planName(payment.plan, i18n.language)}</bdi> : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      <Card className="p-0">
        <CardHeader className="p-(--density-card)">
          <CardTitle>{t("invoices.title")}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {invoices.isPending ? (
            <Skeleton className="m-4 h-24" />
          ) : invoices.isError ? (
            <QueryError error={invoices.error} />
          ) : invoices.data.results.length === 0 ? (
            <EmptyState className="py-8" icon={<FileText />} title={t("invoices.empty.title")} />
          ) : (
            <div className="overflow-x-auto">
              <Table aria-label={t("invoices.title")}>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("invoices.columns.number")}</TableHead>
                    <TableHead>{t("invoices.columns.issued")}</TableHead>
                    <TableHead className="text-end">{t("invoices.columns.total")}</TableHead>
                    <TableHead>{t("invoices.columns.status")}</TableHead>
                    <TableHead>
                      <span className="sr-only">{t("invoices.detail.open")}</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invoices.data.results.map((invoice) => (
                    <TableRow key={invoice.id}>
                      <TableCell>
                        <Link
                          to="/invoices"
                          search={{ invoice: invoice.id }}
                          className="font-mono text-xs hover:underline"
                          dir="ltr"
                        >
                          {invoice.number ?? t("invoices.draft")}
                        </Link>
                      </TableCell>
                      <TableCell>
                        {invoice.issued_at ? format.date(invoice.issued_at) : null}
                      </TableCell>
                      <TableCell className="text-end tabular-nums">
                        {format.money(invoice.total, invoice.currency)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={invoice.status} />
                      </TableCell>
                      <TableCell className="text-end">
                        <Button asChild variant="ghost" size="xs">
                          <a
                            href={getInvoicesDocumentUrl(invoice.id, { locale })}
                            target="_blank"
                            rel="noopener"
                          >
                            {t("invoices.detail.open")}
                          </a>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      {recording ? (
        <RecordPaymentDialog
          open={recording}
          onOpenChange={setRecording}
          customer={{ id: customer.id, name: customer.name || customer.username }}
        />
      ) : null}
    </div>
  );
}

/**
 * Invite the customer to set their portal password (single-use link). The link
 * is shown once; when it wasn't emailed the admin sends it themselves.
 */
export function InviteDialog({
  customer,
  open,
  onOpenChange,
}: {
  customer: CustomerDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const invite = useCustomersPasswordInvite();
  const [result, setResult] = useState<PasswordInvitation | null>(null);
  const name = customer.name || customer.username;
  return (
    <>
      <ConfirmDialog
        open={open}
        onOpenChange={onOpenChange}
        title={t("customerBilling.invite.title", { name })}
        description={
          customer.email
            ? t("customerBilling.invite.descriptionEmail", { email: customer.email })
            : t("customerBilling.invite.descriptionNoEmail")
        }
        confirmLabel={t("customerBilling.invite.confirm")}
        onConfirm={async () => {
          try {
            const invitation = await invite.mutateAsync({ id: customer.id });
            if (invitation.emailed) toast.success(t("customerBilling.invite.emailed", { name }));
            setResult(invitation);
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
      <Dialog
        open={result !== null}
        onOpenChange={(next) => {
          if (!next) setResult(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("customerBilling.invite.linkTitle")}</DialogTitle>
            <DialogDescription>
              {result?.emailed
                ? t("customerBilling.invite.linkEmailed")
                : t("customerBilling.invite.linkSendYourself")}
            </DialogDescription>
          </DialogHeader>
          {result ? (
            <>
              <CopyField label={t("customerBilling.invite.link")} value={result.url} />
              <p className="text-xs text-muted-foreground">
                {t("customerBilling.invite.expires", { time: format.dateTime(result.expires_at) })}
              </p>
              <Badge tone="warning" className="w-fit">
                {t("customerBilling.invite.once")}
              </Badge>
            </>
          ) : null}
          <DialogFooter>
            <Button
              onClick={() => {
                setResult(null);
              }}
            >
              {t("common.done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
