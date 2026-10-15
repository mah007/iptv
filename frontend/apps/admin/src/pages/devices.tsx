import {
  DevicesListAppHint,
  DevicesListKind,
  DevicesListStatus,
  getDevicesListQueryKey,
  useDevicesBulk,
  useDevicesList,
  type DeviceBulkAction,
  type DeviceListItem,
  type DevicesListParams,
} from "@smart-iptv/api";
import {
  Button,
  ConfirmDialog,
  CountryFlag,
  DataTable,
  DeviceIcon,
  EmptyState,
  FacetFilter,
  FilterBar,
  Label,
  PageHeader,
  RelativeTime,
  SearchInput,
  StatusBadge,
  Textarea,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
  sortingFromOrdering,
  sortingToOrdering,
  toast,
  type FilterChip,
  type RowSelectionState,
} from "@smart-iptv/ui";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { Ban, CircleCheck, MonitorSmartphone, ShieldOff, Trash2 } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { RevokeByAppDialog } from "../features/customers/revoke-by-app";
import { SEEN_WINDOWS, type DevicesSearch } from "../features/devices/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/devices");
const helper = createDataTableColumnHelper<DeviceListItem>();

function useColumns() {
  const { t } = useTranslation();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("name", {
          id: "name",
          header: t("devicesPage.columns.device"),
          cell: ({ row }) => {
            const device = row.original;
            return (
              <div className="flex min-w-0 items-center gap-2.5">
                <DeviceIcon hint={device.app_hint} decorative className="size-5 shrink-0" />
                <div className="grid min-w-0">
                  <span className="truncate font-medium text-foreground" dir="auto">
                    {device.name || t("devices.unnamed")}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {device.kind === "xtream"
                      ? t(`devices.apps.${device.app_hint}`)
                      : t(`devicesPage.kinds.${device.kind}`)}
                  </span>
                </div>
              </div>
            );
          },
          meta: { cellClassName: "max-w-64" },
        }),
        helper.accessor("customer", {
          id: "customer",
          header: t("devicesPage.columns.customer"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const customer = getValue();
            return (
              <Link
                to="/customers/$customerId"
                params={{ customerId: customer.id }}
                search={{ tab: "devices" }}
                className="truncate text-foreground outline-none hover:underline focus-visible:underline"
              >
                {customer.name || customer.username}
              </Link>
            );
          },
        }),
        helper.accessor("xtream_username", {
          id: "login",
          header: t("devicesPage.columns.login"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const value = getValue();
            return value ? (
              <span className="font-mono text-xs" dir="ltr">
                {value}
              </span>
            ) : (
              <span className="text-muted-foreground">{t("devices.noCredential")}</span>
            );
          },
        }),
        helper.accessor("status", {
          id: "status",
          header: t("devicesPage.columns.status"),
          enableSorting: false,
          cell: ({ getValue }) => <StatusBadge status={getValue()} />,
        }),
        helper.accessor("last_seen", {
          id: "last_seen",
          header: t("devicesPage.columns.lastSeen"),
          cell: ({ row }) => {
            const device = row.original;
            if (!device.last_seen) {
              return <span className="text-muted-foreground">{t("devices.neverUsed")}</span>;
            }
            return (
              <span className="flex flex-wrap items-center gap-1.5">
                <RelativeTime value={device.last_seen} />
                {device.last_country ? <CountryFlag code={device.last_country} /> : null}
                {device.last_ip ? (
                  <span className="font-mono text-xs text-muted-foreground" dir="ltr">
                    {device.last_ip}
                  </span>
                ) : null}
              </span>
            );
          },
        }),
        helper.accessor("created_at", {
          id: "created_at",
          header: t("devicesPage.columns.added"),
          cell: ({ getValue }) => <RelativeTime value={getValue()} />,
        }),
      ]),
    [t],
  );
}

function apiParams(search: DevicesSearch): DevicesListParams {
  const { pageSize } = paginationFromSearch(search);
  return compact({
    search: search.q,
    kind: search.kind,
    app_hint: search.app,
    status: search.status,
    seen_within_days: search.seen,
    ordering: search.ordering ? [search.ordering] : undefined,
    page: search.page,
    page_size: pageSize,
  });
}

interface BulkAction {
  action: DeviceBulkAction;
  ids: string[];
}

function BulkBar({ ids, onAction }: { ids: string[]; onAction: (action: BulkAction) => void }) {
  const { t } = useTranslation();
  const button = (action: DeviceBulkAction, icon: ReactNode) => (
    <Button
      size="sm"
      variant={action === "revoke" ? "danger" : "secondary"}
      onClick={() => {
        onAction({ action, ids });
      }}
    >
      {icon}
      {t(`devicesPage.bulk.${action}`)}
    </Button>
  );
  return (
    <>
      {button("block", <Ban aria-hidden="true" />)}
      {button("unblock", <CircleCheck aria-hidden="true" />)}
      {button("revoke", <Trash2 aria-hidden="true" />)}
    </>
  );
}

function Devices() {
  const { t } = useTranslation();
  const can = useCan();
  const manage = can("devices.manage");
  const queryClient = useQueryClient();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const columns = useColumns();
  const reasonField = useId();
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [pending, setPending] = useState<BulkAction | null>(null);
  const [reason, setReason] = useState("");
  const [revoking, setRevoking] = useState(false);
  const bulk = useDevicesBulk();
  const query = useDevicesList(apiParams(search), {
    query: { placeholderData: keepPreviousData },
  });

  function update(patch: SearchPatch<DevicesSearch>, { keepPage = false } = {}): void {
    setSelection({});
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  async function run(action: BulkAction): Promise<void> {
    try {
      const result = await bulk.mutateAsync({
        data: { ids: action.ids, action: action.action, reason: reason.trim() },
      });
      toast.success(
        t(`devicesPage.bulk.done.${action.action}`, {
          count: result.changed,
          skipped: result.skipped,
        }),
      );
      setSelection({});
      setPending(null);
      setReason("");
      await queryClient.invalidateQueries({ queryKey: getDevicesListQueryKey() });
    } catch (error) {
      notifyError(t, error);
      throw error;
    }
  }

  const chips: FilterChip[] = [];
  const facet = (
    id: "kind" | "app" | "status",
    value: string | undefined,
    label: (value: string) => string,
  ) => {
    if (value !== undefined) {
      chips.push({
        id,
        label: label(value),
        onRemove: () => {
          update({ [id]: undefined });
        },
      });
    }
  };
  facet("kind", search.kind, (value) =>
    t("devicesPage.filters.chip", {
      name: t("devicesPage.filters.kind"),
      value: t(`devicesPage.kinds.${value}`),
    }),
  );
  facet("app", search.app, (value) =>
    t("devicesPage.filters.chip", {
      name: t("devicesPage.filters.app"),
      value: t(`devices.apps.${value}`),
    }),
  );
  facet("status", search.status, (value) =>
    t("devicesPage.filters.chip", {
      name: t("devicesPage.filters.status"),
      value: t(`ui:status.${value}`),
    }),
  );
  if (search.seen !== undefined) {
    chips.push({
      id: "seen",
      label: t("devicesPage.filters.seenWithin", { count: search.seen }),
      onRemove: () => {
        update({ seen: undefined });
      },
    });
  }
  const filtered = chips.length > 0 || search.q !== undefined;
  const count = pending?.ids.length ?? 0;

  return (
    <>
      <PageHeader
        title={t("devicesPage.title")}
        description={t("devicesPage.description")}
        actions={
          manage ? (
            <Button
              variant="secondary"
              onClick={() => {
                setRevoking(true);
              }}
            >
              <ShieldOff aria-hidden="true" />
              {t("revokeByApp.action")}
            </Button>
          ) : null
        }
      />
      <RevokeByAppDialog open={revoking} onOpenChange={setRevoking} />
      <DataTable
        label={t("devicesPage.title")}
        columns={columns}
        data={query.data?.results}
        getRowId={(device) => device.id}
        rowCount={query.data?.count}
        pagination={paginationFromSearch(search)}
        onPaginationChange={(pagination) => {
          update(paginationToSearch(pagination), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) as DevicesSearch["ordering"] });
        }}
        {...(manage
          ? {
              rowSelection: selection,
              onRowSelectionChange: setSelection,
              bulkActions: (ids: string[]) => <BulkBar ids={ids} onAction={setPending} />,
            }
          : {})}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("devicesPage.filters.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            filters={
              <>
                <FacetFilter
                  single
                  title={t("devicesPage.filters.status")}
                  options={Object.values(DevicesListStatus).map((value) => ({
                    value,
                    label: t(`ui:status.${value}`),
                  }))}
                  selected={search.status ? [search.status] : []}
                  onSelectedChange={(values) => {
                    update({ status: values[0] as DevicesSearch["status"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("devicesPage.filters.kind")}
                  options={Object.values(DevicesListKind).map((value) => ({
                    value,
                    label: t(`devicesPage.kinds.${value}`),
                  }))}
                  selected={search.kind ? [search.kind] : []}
                  onSelectedChange={(values) => {
                    update({ kind: values[0] as DevicesSearch["kind"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("devicesPage.filters.app")}
                  options={Object.values(DevicesListAppHint).map((value) => ({
                    value,
                    label: t(`devices.apps.${value}`),
                  }))}
                  selected={search.app ? [search.app] : []}
                  onSelectedChange={(values) => {
                    update({ app: values[0] as DevicesSearch["app"] });
                  }}
                />
                <FacetFilter
                  single
                  title={t("devicesPage.filters.seen")}
                  options={SEEN_WINDOWS.map((days) => ({
                    value: String(days),
                    label: t("devicesPage.filters.days", { count: days }),
                  }))}
                  selected={search.seen ? [String(search.seen)] : []}
                  onSelectedChange={(values) => {
                    update({ seen: values[0] ? Number(values[0]) : undefined });
                  }}
                />
              </>
            }
            chips={chips}
            onClearAll={() => {
              update({
                q: undefined,
                kind: undefined,
                app: undefined,
                status: undefined,
                seen: undefined,
              });
            }}
          />
        }
        emptyState={
          filtered ? undefined : (
            <EmptyState
              icon={<MonitorSmartphone />}
              title={t("devicesPage.empty.title")}
              description={t("devicesPage.empty.description")}
            />
          )
        }
      />
      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPending(null);
            setReason("");
          }
        }}
        title={pending ? t(`devicesPage.bulk.confirm.${pending.action}`, { count }) : ""}
        description={
          pending ? t(`devicesPage.bulk.confirmDescription.${pending.action}`) : undefined
        }
        confirmLabel={pending ? t(`devicesPage.bulk.${pending.action}`) : ""}
        tone={pending?.action === "unblock" ? "default" : "danger"}
        onConfirm={async () => {
          if (pending) await run(pending);
        }}
      >
        {pending?.action === "block" ? (
          <div className="grid gap-1.5">
            <Label htmlFor={reasonField}>{t("devices.block.reason")}</Label>
            <Textarea
              id={reasonField}
              rows={2}
              maxLength={200}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
            />
          </div>
        ) : null}
      </ConfirmDialog>
    </>
  );
}

export function DevicesPage() {
  const { t } = useTranslation();
  usePageTitle(t("devicesPage.title"));
  return (
    <RequirePermission permission="customers.view">
      <Devices />
    </RequirePermission>
  );
}
