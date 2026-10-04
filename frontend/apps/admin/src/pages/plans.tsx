import {
  getPlansListQueryKey,
  usePlansDestroy,
  usePlansList,
  usePlansMigrateSubscriptions,
  usePlansReorder,
  usePlansUpdate,
  type Plan,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  ConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  PageHeader,
  Skeleton,
  StatusBadge,
  cn,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  MoreHorizontal,
  Package,
  Pencil,
  Plus,
  Power,
  Repeat,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { planDuration } from "../features/billing/labels";
import { planName } from "../features/billing/pickers";
import { PlanSheet } from "../features/billing/plan-sheet";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

type Confirm = { kind: "migrate" | "delete"; plan: Plan } | null;

function PlanCard({
  plan,
  first,
  last,
  onEdit,
  onMove,
  onConfirm,
  onToggle,
}: {
  plan: Plan;
  first: boolean;
  last: boolean;
  onEdit: () => void;
  onMove: (step: -1 | 1) => void;
  onConfirm: (kind: "migrate" | "delete") => void;
  onToggle: () => void;
}) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const editable = can("plans.edit");
  const name = planName(plan, i18n.language);
  const description = i18n.language === "ar" ? plan.description_ar : plan.description_en;
  const content = (["allow_movies", "allow_series", "allow_live"] as const).filter(
    (key) => plan[key],
  );
  return (
    <Card className={cn("flex flex-col", !plan.active && "opacity-70")}>
      <CardContent className="flex flex-1 flex-col gap-3 pt-(--density-card)">
        <div className="flex items-start justify-between gap-2">
          <div className="grid min-w-0 gap-0.5">
            <h2 className="truncate text-base font-semibold text-foreground">
              <bdi>{name}</bdi>
            </h2>
            <code className="truncate font-mono text-xs text-muted-foreground" dir="ltr">
              {plan.code}
            </code>
          </div>
          {editable ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={t("plans.actions", { name })}>
                  <MoreHorizontal aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={onEdit}>
                  <Pencil aria-hidden="true" />
                  {t("plans.edit")}
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={first}
                  onSelect={() => {
                    onMove(-1);
                  }}
                >
                  <ArrowUp aria-hidden="true" />
                  {t("plans.moveUp")}
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={last}
                  onSelect={() => {
                    onMove(1);
                  }}
                >
                  <ArrowDown aria-hidden="true" />
                  {t("plans.moveDown")}
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={onToggle}>
                  <Power aria-hidden="true" />
                  {plan.active ? t("plans.deactivate") : t("plans.activate")}
                </DropdownMenuItem>
                {plan.subscribers > 0 ? (
                  <DropdownMenuItem
                    onSelect={() => {
                      onConfirm("migrate");
                    }}
                  >
                    <Repeat aria-hidden="true" />
                    {t("plans.migrate.action")}
                  </DropdownMenuItem>
                ) : null}
                {plan.subscribers === 0 ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-danger-text"
                      onSelect={() => {
                        onConfirm("delete");
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                      {t("plans.delete.action")}
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {plan.active ? <StatusBadge status="active" /> : <Badge>{t("plans.inactive")}</Badge>}
          {plan.is_trial ? <StatusBadge status="trial" /> : null}
          <Badge tone="neutral">{t("plans.version", { version: plan.version })}</Badge>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-2xl font-semibold text-foreground">
            {format.money(plan.price_total.total, plan.currency)}
          </span>
          <span className="text-xs text-muted-foreground">
            {planDuration(t, plan.duration_months, plan.duration_days)}
          </span>
        </div>
        {description ? (
          <p className="line-clamp-2 text-ui text-muted-foreground" dir="auto">
            {description}
          </p>
        ) : null}
        <ul className="grid gap-1 text-ui text-foreground">
          <li>{t("plans.preview.streams", { count: plan.max_streams })}</li>
          <li>{t("plans.preview.devices", { count: plan.max_devices })}</li>
          <li>{t(`customers.quality.${String(plan.max_quality)}`)}</li>
          <li className="text-muted-foreground">
            {content.length === 0
              ? t("customers.detail.noContent")
              : content.map((key) => t(`customers.access.${key}`)).join(" · ")}
            {plan.category_ids.length > 0
              ? ` · ${t("plans.categoriesCount", { count: plan.category_ids.length })}`
              : ""}
          </li>
        </ul>
        <p className="mt-auto border-t border-border pt-3 text-xs text-muted-foreground">
          {t("plans.subscribers", {
            count: plan.subscribers,
            formatted: format.number(plan.subscribers),
          })}
        </p>
      </CardContent>
    </Card>
  );
}

function Plans() {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const query = usePlansList({});
  const reorder = usePlansReorder();
  const update = usePlansUpdate();
  const migrate = usePlansMigrateSubscriptions();
  const destroy = usePlansDestroy();
  const [editing, setEditing] = useState<Plan | null>(null);
  const [sheet, setSheet] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const plans = [...(query.data ?? [])].sort((a, b) => a.sort - b.sort);

  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: getPlansListQueryKey() });
  }

  function move(index: number, step: -1 | 1): void {
    const ids = plans.map((plan) => plan.id);
    const target = index + step;
    const [moved] = ids.splice(index, 1);
    if (moved === undefined) return;
    ids.splice(target, 0, moved);
    reorder.mutate(
      { data: { ids } },
      {
        onSuccess: (ordered) => {
          queryClient.setQueryData(getPlansListQueryKey(), ordered);
          toast.success(t("plans.reordered"));
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  }

  const newButton = can("plans.edit") ? (
    <Button
      onClick={() => {
        setEditing(null);
        setSheet(true);
      }}
    >
      <Plus aria-hidden="true" />
      {t("plans.new")}
    </Button>
  ) : null;

  return (
    <>
      <PageHeader
        title={t("plans.title")}
        description={t("plans.description")}
        actions={newButton}
      />
      {query.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" role="status">
          <span className="sr-only">{t("layout.loading")}</span>
          {[0, 1, 2].map((card) => (
            <Skeleton key={card} className="h-72 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : plans.length === 0 ? (
        <EmptyState
          icon={<Package />}
          title={t("plans.empty.title")}
          description={t("plans.empty.description")}
          action={newButton}
        />
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label={t("plans.title")}>
          {plans.map((plan, index) => (
            <li key={plan.id} className="grid">
              <PlanCard
                plan={plan}
                first={index === 0}
                last={index === plans.length - 1}
                onEdit={() => {
                  setEditing(plan);
                  setSheet(true);
                }}
                onMove={(step) => {
                  move(index, step);
                }}
                onConfirm={(kind) => {
                  setConfirm({ kind, plan });
                }}
                onToggle={() => {
                  update.mutate(
                    { id: plan.id, data: { active: !plan.active } },
                    {
                      onSuccess: () => {
                        toast.success(plan.active ? t("plans.deactivated") : t("plans.activated"));
                        void refresh();
                      },
                      onError: (error) => {
                        notifyError(t, error);
                      },
                    },
                  );
                }}
              />
            </li>
          ))}
        </ul>
      )}
      <PlanSheet plan={editing} open={sheet} onOpenChange={setSheet} />
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        tone={confirm?.kind === "delete" ? "danger" : "default"}
        title={
          confirm?.kind === "delete"
            ? t("plans.delete.title", { name: planName(confirm.plan, i18n.language) })
            : t("plans.migrate.title", {
                name: confirm ? planName(confirm.plan, i18n.language) : "",
              })
        }
        description={
          confirm?.kind === "delete"
            ? t("plans.delete.description")
            : t("plans.migrate.description", { count: confirm?.plan.subscribers ?? 0 })
        }
        confirmLabel={
          confirm?.kind === "delete" ? t("plans.delete.action") : t("plans.migrate.confirm")
        }
        {...(confirm?.kind === "migrate" ? { confirmationText: confirm.plan.code } : {})}
        onConfirm={async () => {
          if (confirm === null) return;
          try {
            if (confirm.kind === "delete") {
              await destroy.mutateAsync({ id: confirm.plan.id });
              toast.success(t("plans.delete.done"));
            } else {
              const result = await migrate.mutateAsync({ id: confirm.plan.id });
              toast.success(t("plans.migrate.done", { count: result.migrated }));
            }
            await refresh();
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </>
  );
}

export function PlansPage() {
  const { t } = useTranslation();
  usePageTitle(t("plans.title"));
  return (
    <RequirePermission permission="plans.view">
      <Plans />
    </RequirePermission>
  );
}
