import {
  getLockoutsListQueryKey,
  useLockoutsList,
  useLockoutsUnlock,
  type Lockout,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  RelativeTime,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { LockOpen, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { notifyError } from "../../lib/problems";

/** How often the list is read again: lockouts end by themselves. */
const REFRESH_MS = 15_000;

function Who({ lockout }: { lockout: Lockout }) {
  const { t } = useTranslation();
  const account = lockout.account;
  return (
    <div className="grid min-w-0 gap-0.5">
      {account ? (
        account.is_staff ? (
          <span className="flex items-center gap-1.5 font-medium text-foreground">
            {account.name || account.username}
            <Badge>{t("lockouts.admin")}</Badge>
          </span>
        ) : (
          <Link
            to="/customers/$customerId"
            params={{ customerId: account.id }}
            search={{ tab: "security" }}
            className="truncate font-medium text-foreground outline-none hover:underline focus-visible:underline"
          >
            {account.name || account.username}
          </Link>
        )
      ) : (
        <span className="text-muted-foreground">
          {lockout.kind === "xtream_address" ? t("lockouts.anyone") : t("lockouts.unknown")}
        </span>
      )}
      {lockout.login ? (
        <span className="truncate font-mono text-xs text-muted-foreground" dir="ltr">
          {lockout.login}
        </span>
      ) : null}
    </div>
  );
}

function Where({ lockout }: { lockout: Lockout }) {
  const { t } = useTranslation();
  if (lockout.ip) {
    return (
      <span className="font-mono text-xs" dir="ltr">
        {lockout.ip}
      </span>
    );
  }
  if (lockout.network === "*") return <span>{t("lockouts.everyNetwork")}</span>;
  if (lockout.network && lockout.network !== "-") {
    return (
      <span className="font-mono text-xs" dir="ltr">
        {t("lockouts.network", { network: lockout.network })}
      </span>
    );
  }
  return <span className="text-muted-foreground">{t("lockouts.anywhere")}</span>;
}

/**
 * Who can't sign in right now (SPEC §8.3 Security → Lockouts; ADR-0023): admin, portal
 * and app sign-ins, second factors, confirmations, TV logins and addresses. `customer`
 * limits it to one customer (their Security tab).
 */
export function LockoutsTable({ customer }: { customer?: string }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const queryClient = useQueryClient();
  const params = customer ? { customer } : undefined;
  const query = useLockoutsList(params, { query: { refetchInterval: REFRESH_MS } });
  const unlock = useLockoutsUnlock();
  const [pending, setPending] = useState<Lockout | null>(null);

  function mayUnlock(lockout: Lockout): boolean {
    if (!can("lockouts.manage")) return false;
    return !lockout.account?.is_staff || can("admins.manage");
  }

  if (query.isPending) return <Skeleton className="h-32 w-full" />;
  if (query.isError) {
    return (
      <QueryError
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={<ShieldCheck />}
        title={t("lockouts.empty.title")}
        description={customer ? t("lockouts.empty.customer") : t("lockouts.empty.description")}
      />
    );
  }
  return (
    <>
      <Table aria-label={t("lockouts.title")}>
        <TableHeader>
          <TableRow>
            <TableHead>{t("lockouts.columns.kind")}</TableHead>
            <TableHead>{t("lockouts.columns.who")}</TableHead>
            <TableHead>{t("lockouts.columns.where")}</TableHead>
            <TableHead className="text-end">{t("lockouts.columns.failures")}</TableHead>
            <TableHead>{t("lockouts.columns.until")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("lockouts.columns.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.data.map((lockout) => (
            <TableRow key={lockout.id}>
              <TableCell>
                <Badge tone="warning">{t(`lockouts.kinds.${lockout.kind}`)}</Badge>
              </TableCell>
              <TableCell className="max-w-64">
                <Who lockout={lockout} />
              </TableCell>
              <TableCell>
                <Where lockout={lockout} />
              </TableCell>
              <TableCell className="text-end tabular-nums">
                {format.number(lockout.failures)}
              </TableCell>
              <TableCell>
                {lockout.until ? (
                  <RelativeTime value={lockout.until} />
                ) : (
                  <span className="text-muted-foreground">{t("lockouts.untilUnknown")}</span>
                )}
              </TableCell>
              <TableCell className="text-end">
                {mayUnlock(lockout) ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setPending(lockout);
                    }}
                  >
                    <LockOpen aria-hidden="true" />
                    {t("lockouts.unlock")}
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={t("lockouts.confirm.title")}
        description={pending ? t(`lockouts.confirm.${pending.kind}`) : undefined}
        confirmLabel={t("lockouts.unlock")}
        onConfirm={async () => {
          if (!pending) return;
          try {
            await unlock.mutateAsync({ data: { id: pending.id } });
            toast.success(t("lockouts.unlocked"));
            setPending(null);
            await queryClient.invalidateQueries({ queryKey: getLockoutsListQueryKey() });
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </>
  );
}
