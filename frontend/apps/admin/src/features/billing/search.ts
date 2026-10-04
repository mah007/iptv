import {
  InvoicesListOrderingItem,
  InvoiceStatus,
  OutboxStatus,
  PaymentProviderCode,
  PaymentsListOrderingItem,
  PaymentStatus,
  SubscriptionSource,
  SubscriptionsListOrderingItem,
  SubscriptionStatus,
} from "@smart-iptv/api";
import { parseTableSearch } from "@smart-iptv/ui";

import { booleanParam, compact, enumParam, intParam, stringParam } from "../../lib/search";

/* URL state of the billing pages (SPEC §8.2: shareable views). */

export const EXPIRING_DAYS = [7, 14, 30] as const;

export function parseSubscriptionsSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    status: enumParam(raw.status, Object.values(SubscriptionStatus)),
    source: enumParam(raw.source, Object.values(SubscriptionSource)),
    plan: stringParam(raw.plan),
    expiring: intParam(raw.expiring, EXPIRING_DAYS),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, Object.values(SubscriptionsListOrderingItem)),
    /** Opens the new-subscription dialog. */
    new: booleanParam(raw.new),
  });
}
export type SubscriptionsSearch = ReturnType<typeof parseSubscriptionsSearch>;

export function parsePaymentsSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    status: enumParam(raw.status, Object.values(PaymentStatus)),
    provider: enumParam(raw.provider, Object.values(PaymentProviderCode)),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, Object.values(PaymentsListOrderingItem)),
    /** The payment open in the detail sheet. */
    payment: stringParam(raw.payment),
    /** Opens the record-payment dialog. */
    record: booleanParam(raw.record),
  });
}
export type PaymentsSearch = ReturnType<typeof parsePaymentsSearch>;

export function parseInvoicesSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    status: enumParam(raw.status, Object.values(InvoiceStatus)),
    page: table.page,
    page_size: table.page_size,
    ordering: enumParam(table.ordering, Object.values(InvoicesListOrderingItem)),
    invoice: stringParam(raw.invoice),
  });
}
export type InvoicesSearch = ReturnType<typeof parseInvoicesSearch>;

export function parseNotificationsSearch(raw: Record<string, unknown>) {
  const table = parseTableSearch(raw);
  return compact({
    q: stringParam(raw.q),
    status: enumParam(raw.status, Object.values(OutboxStatus)),
    page: table.page,
    page_size: table.page_size,
    message: stringParam(raw.message),
  });
}
export type NotificationsSearch = ReturnType<typeof parseNotificationsSearch>;

export const TEMPLATE_LOCALES = ["en", "ar"] as const;

export function parseTemplatesSearch(raw: Record<string, unknown>) {
  return compact({
    key: stringParam(raw.key),
    locale: enumParam(raw.locale, TEMPLATE_LOCALES),
  });
}
export type TemplatesSearch = ReturnType<typeof parseTemplatesSearch>;
