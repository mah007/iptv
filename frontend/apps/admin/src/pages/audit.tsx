import { useAuditList, type AuditListParams, type AuditLog } from "@smart-iptv/api";
import {
  Badge,
  DEFAULT_TIME_ZONE,
  DataTable,
  DateRangeFilter,
  DescriptionItem,
  DescriptionList,
  DiffViewer,
  EmptyState,
  FacetFilter,
  FilterBar,
  PageHeader,
  RelativeTime,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
  sortingFromOrdering,
  sortingToOrdering,
  useFormatters,
  type FilterChip,
} from "@smart-iptv/ui";
import { keepPreviousData } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { ScrollText } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import type { AuditSearch } from "../features/audit/search";
import { useMe } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";
import { endOfDayIn, startOfDayIn } from "../lib/time";

const route = getRouteApi("/app/audit");
const helper = createDataTableColumnHelper<AuditLog>();

/** Actions the admin API records today; others still show, with their raw name. */
const KNOWN_ACTIONS = [
  "customer.create",
  "customer.update",
  "customer.access.update",
  "customer.suspend",
  "customer.reactivate",
  "customer.access.expire",
  "device.create",
  "device.reset_credentials",
  "device.block",
  "device.unblock",
  "device.approve",
  "device.revoke",
  "access_rule.create",
  "access_rule.delete",
  "admin.create",
  "admin.update",
  "admin.password_reset",
  "admin.mfa_reset",
  "role.create",
  "role.update",
  "role.delete",
  "setting.update",
  "library.create",
  "library.update",
  "library.delete",
  "library.scan",
  "category.create",
  "category.update",
  "category.delete",
  "category.reorder",
  "auth.login",
  "auth.logout",
  "auth.mfa_enroll",
  "system.expire",
];
const KNOWN_TARGETS = [
  "accounts.user",
  "accounts.device",
  "accounts.role",
  "core.setting",
  "library.library",
  "catalog.category",
];

/** i18next reads "." as nesting; action names use it too, so they are stored with "_". */
function actionKey(action: string): string {
  return `audit.actions.${action.replace(/\./gu, "_")}`;
}

function targetKey(type: string): string {
  return `audit.targets.${type.replace(/\./gu, "_")}`;
}

function ActionBadge({ action }: { action: string }) {
  const { t } = useTranslation();
  const label = t(actionKey(action), { defaultValue: "" });
  return label ? (
    <span className="grid min-w-0">
      <span className="truncate text-foreground">{label}</span>
      <code className="truncate font-mono text-[11px] text-muted-foreground" dir="ltr">
        {action}
      </code>
    </span>
  ) : (
    <code className="truncate font-mono text-xs text-foreground" dir="ltr">
      {action}
    </code>
  );
}

function TargetText({ entry }: { entry: AuditLog }) {
  const { t } = useTranslation();
  if (!entry.target_type)
    return <span className="text-muted-foreground">{t("audit.noTarget")}</span>;
  const label = t(targetKey(entry.target_type), { defaultValue: entry.target_type });
  const id = (
    <code className="truncate font-mono text-[11px] text-muted-foreground" dir="ltr">
      {entry.target_id}
    </code>
  );
  return (
    <span className="grid min-w-0">
      <span className="truncate">{label}</span>
      {entry.action.startsWith("customer.") && entry.target_id ? (
        <Link
          to="/customers/$customerId"
          params={{ customerId: entry.target_id }}
          className="truncate outline-none hover:underline focus-visible:underline"
        >
          {id}
        </Link>
      ) : (
        id
      )}
    </span>
  );
}

function ActorText({ entry }: { entry: AuditLog }) {
  const { t } = useTranslation();
  return entry.actor ? (
    <span className="font-medium text-foreground" dir="auto">
      {entry.actor.username}
    </span>
  ) : (
    <Badge>{t("audit.system")}</Badge>
  );
}

function AuditDetail({ entry, onClose }: { entry: AuditLog | null; onClose: () => void }) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <Sheet
      open={entry !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        {entry ? (
          <>
            <SheetHeader>
              <SheetTitle>{t(actionKey(entry.action), { defaultValue: entry.action })}</SheetTitle>
              <SheetDescription>
                {format.dateTime(entry.at, { timeStyle: "medium" })}
              </SheetDescription>
            </SheetHeader>
            <SheetBody className="grid content-start gap-6">
              <DescriptionList>
                <DescriptionItem label={t("audit.columns.action")}>
                  <code className="font-mono text-xs" dir="ltr">
                    {entry.action}
                  </code>
                </DescriptionItem>
                <DescriptionItem label={t("audit.columns.actor")}>
                  <ActorText entry={entry} />
                </DescriptionItem>
                <DescriptionItem label={t("audit.columns.ip")}>
                  {entry.actor_ip ? (
                    <span className="font-mono text-xs" dir="ltr">
                      {entry.actor_ip}
                    </span>
                  ) : null}
                </DescriptionItem>
                <DescriptionItem label={t("audit.columns.target")}>
                  <TargetText entry={entry} />
                </DescriptionItem>
              </DescriptionList>
              <section className="grid gap-2">
                <h3 className="text-sm font-semibold text-foreground">{t("audit.changes")}</h3>
                <DiffViewer before={entry.before} after={entry.after} />
              </section>
            </SheetBody>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function apiParams(search: AuditSearch, timeZone: string): AuditListParams {
  return compact({
    action: search.action,
    target_type: search.target_type,
    target_id: search.target_id,
    at_after: search.from ? startOfDayIn(search.from, timeZone) : undefined,
    at_before: search.to ? endOfDayIn(search.to, timeZone) : undefined,
    ordering: search.ordering,
    page: search.page,
    page_size: paginationFromSearch(search).pageSize,
  });
}

function Audit() {
  const { t } = useTranslation();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const [selected, setSelected] = useState<AuditLog | null>(null);
  const query = useAuditList(apiParams(search, timeZone), {
    query: { placeholderData: keepPreviousData },
  });

  function update(patch: SearchPatch<AuditSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const columns = useMemo(
    () =>
      helper.columns([
        helper.accessor("at", {
          id: "at",
          header: t("audit.columns.when"),
          cell: ({ getValue }) => <RelativeTime value={getValue()} />,
        }),
        helper.accessor("actor", {
          id: "actor",
          header: t("audit.columns.actor"),
          enableSorting: false,
          cell: ({ row }) => <ActorText entry={row.original} />,
        }),
        helper.accessor("action", {
          id: "action",
          header: t("audit.columns.action"),
          enableSorting: false,
          cell: ({ getValue }) => <ActionBadge action={getValue()} />,
          meta: { cellClassName: "max-w-64" },
        }),
        helper.accessor("target_type", {
          id: "target",
          header: t("audit.columns.target"),
          enableSorting: false,
          cell: ({ row }) => <TargetText entry={row.original} />,
          meta: { cellClassName: "max-w-64" },
        }),
        helper.accessor("actor_ip", {
          id: "ip",
          header: t("audit.columns.ip"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="font-mono text-xs text-muted-foreground" dir="ltr">
              {getValue()}
            </span>
          ),
        }),
      ]),
    [t],
  );

  const actionOptions = [
    ...KNOWN_ACTIONS,
    ...(search.action && !KNOWN_ACTIONS.includes(search.action) ? [search.action] : []),
  ].map((action) => ({ value: action, label: t(actionKey(action), { defaultValue: action }) }));
  const targetOptions = [
    ...KNOWN_TARGETS,
    ...(search.target_type && !KNOWN_TARGETS.includes(search.target_type)
      ? [search.target_type]
      : []),
  ].map((type) => ({ value: type, label: t(targetKey(type), { defaultValue: type }) }));

  const chips: FilterChip[] = [];
  if (search.action) {
    chips.push({
      id: "action",
      label: t("audit.filters.chipAction", {
        value: t(actionKey(search.action), { defaultValue: search.action }),
      }),
      onRemove: () => {
        update({ action: undefined });
      },
    });
  }
  if (search.target_type) {
    chips.push({
      id: "target_type",
      label: t("audit.filters.chipTarget", {
        value: t(targetKey(search.target_type), { defaultValue: search.target_type }),
      }),
      onRemove: () => {
        update({ target_type: undefined });
      },
    });
  }
  if (search.target_id) {
    chips.push({
      id: "target_id",
      label: t("audit.filters.chipTargetId", { value: search.target_id }),
      onRemove: () => {
        update({ target_id: undefined });
      },
    });
  }
  if (search.from || search.to) {
    chips.push({
      id: "dates",
      label: t("audit.filters.chipDates", { from: search.from ?? "…", to: search.to ?? "…" }),
      onRemove: () => {
        update({ from: undefined, to: undefined });
      },
    });
  }

  return (
    <>
      <PageHeader title={t("audit.title")} description={t("audit.description")} />
      <DataTable
        label={t("audit.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(entry) => entry.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as AuditSearch["ordering"] });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={setSelected}
        toolbar={
          <FilterBar
            className="w-full"
            filters={
              <>
                <FacetFilter
                  single
                  title={t("audit.filters.action")}
                  options={actionOptions}
                  selected={search.action ? [search.action] : []}
                  onSelectedChange={(values) => {
                    update({ action: values[0] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("audit.filters.target")}
                  options={targetOptions}
                  selected={search.target_type ? [search.target_type] : []}
                  onSelectedChange={(values) => {
                    update({ target_type: values[0] });
                  }}
                />
                <DateRangeFilter
                  title={t("audit.filters.dates")}
                  value={compact({ from: search.from, to: search.to })}
                  onValueChange={(range) => {
                    update({ from: range.from, to: range.to });
                  }}
                />
              </>
            }
            chips={chips}
            onClearAll={() => {
              update({
                action: undefined,
                target_type: undefined,
                target_id: undefined,
                from: undefined,
                to: undefined,
              });
            }}
          />
        }
        emptyState={
          chips.length > 0 ? undefined : (
            <EmptyState
              icon={<ScrollText />}
              title={t("audit.empty.title")}
              description={t("audit.empty.description")}
            />
          )
        }
      />
      <AuditDetail
        entry={selected}
        onClose={() => {
          setSelected(null);
        }}
      />
    </>
  );
}

export function AuditPage() {
  const { t } = useTranslation();
  usePageTitle(t("audit.title"));
  return (
    <RequirePermission permission="audit.view">
      <Audit />
    </RequirePermission>
  );
}
