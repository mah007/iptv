import { useCustomersResetMfa, type CustomerDetail } from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Smartphone } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useCan } from "../../lib/auth";
import { notifyError } from "../../lib/problems";
import { invalidateCustomer } from "./cache";

/**
 * The customer's optional two-factor sign-in (ADR-0024), on the Security tab. Support's
 * recovery for a lost phone: switch it off (customers.edit, a code first, audited); the
 * customer's next sign-in needs the password alone.
 */
export function CustomerTwoFactorCard({ customer }: { customer: CustomerDetail }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const reset = useCustomersResetMfa();
  const name = customer.name || customer.username;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle className="flex items-center gap-2">
            <Smartphone aria-hidden="true" className="size-4 text-muted-foreground" />
            {t("customers.twoFactor.title")}
            <Badge tone={customer.mfa_enabled ? "success" : "neutral"} dot className="ms-1">
              {customer.mfa_enabled ? t("customers.twoFactor.on") : t("customers.twoFactor.off")}
            </Badge>
          </CardTitle>
          <CardDescription>
            {customer.mfa_enabled
              ? t("customers.twoFactor.onHint")
              : t("customers.twoFactor.offHint")}
          </CardDescription>
        </div>
      </CardHeader>
      {customer.mfa_enabled && can("customers.edit") ? (
        <CardContent>
          <ConfirmDialog
            tone="danger"
            title={t("customers.twoFactor.resetTitle", { name })}
            description={t("customers.twoFactor.resetDescription")}
            confirmLabel={t("customers.twoFactor.reset")}
            cancelLabel={t("common.cancel")}
            onConfirm={async () => {
              try {
                await reset.mutateAsync({ id: customer.id });
              } catch (error) {
                notifyError(t, error);
                throw error;
              }
              void invalidateCustomer(queryClient, customer.id);
              toast.success(t("customers.twoFactor.resetDone", { name }));
            }}
            trigger={
              <Button type="button" variant="secondary" size="sm">
                {t("customers.twoFactor.reset")}
              </Button>
            }
          />
        </CardContent>
      ) : null}
    </Card>
  );
}
