import {
  CategoryKind,
  getCategoriesListQueryKey,
  getLiveChannelsListQueryKey,
  getLiveIntegrationsListQueryKey,
  useCategoriesDelete,
  useCategoriesList,
  useLiveChannelsBulkEnable,
  useLiveChannelsDelete,
  useLiveChannelsList,
  useLiveChannelsReorder,
  useLiveIntegrationsDelete,
  useLiveIntegrationsList,
  useLiveIntegrationsSync,
  useLiveOverview,
  type Category,
  type ChannelStatusStateEnum,
  type LiveChannel,
  type LiveIntegration,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  PageHeader,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  StatTile,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsList,
  TabsTrigger,
  toast,
  useFormatters,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowUp,
  Pencil,
  Plus,
  Plug,
  RefreshCw,
  Radio,
  Trash2,
  Tv2,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { localName } from "../features/catalog/artwork";
import { ChannelSheet } from "../features/live/channel-sheet";
import { IntegrationSheet } from "../features/live/integration-sheet";
import { LIVE_TABS, type LiveTab } from "../features/live/search";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { compact } from "../lib/search";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";
import { CategoryForm, CategoryRows } from "./categories";

const route = getRouteApi("/app/live");
const PAGE_SIZE = 100;
const ALL_GROUPS = "all";

const STATE_TONES: Record<ChannelStatusStateEnum, BadgeTone> = {
  live: "success",
  starting: "info",
  idle: "neutral",
  failed: "danger",
  disabled: "neutral",
  unlicensed: "danger",
};

function ChannelState({ channel }: { channel: LiveChannel }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const status = channel.status;
  return (
    <div className="grid gap-0.5">
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone={STATE_TONES[status.state]} dot>
          {t(`liveTv.states.${status.state}`)}
        </Badge>
        {status.recording ? <Badge tone="danger">{t("liveTv.recording")}</Badge> : null}
      </span>
      <span className="text-xs text-muted-foreground tabular-nums">
        {t("liveTv.viewers", { count: status.viewers })}
        {status.bitrate_kbps > 0 ? ` · ${format.bitrate(status.bitrate_kbps * 1000)}` : ""}
      </span>
      {status.state === "failed" && status.error ? (
        <span className="text-xs text-danger-text">
          {t(`liveTv.errors.${status.error.startsWith("exit_") ? "exit" : status.error}`, {
            defaultValue: status.error,
          })}
        </span>
      ) : null}
    </div>
  );
}

function ChannelRows({
  channels,
  manage,
  ordered,
  onEdit,
  onDelete,
}: {
  channels: readonly LiveChannel[];
  manage: boolean;
  ordered: Category | null;
  onEdit: (channel: LiveChannel) => void;
  onDelete: (channel: LiveChannel) => void;
}) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const bulk = useLiveChannelsBulkEnable();
  const reorder = useLiveChannelsReorder();

  function refresh(): void {
    void queryClient.invalidateQueries({ queryKey: getLiveChannelsListQueryKey() });
  }

  function setEnabled(channel: LiveChannel, enabled: boolean): void {
    bulk.mutate(
      { data: { ids: [channel.id], enabled } },
      {
        onSuccess: () => {
          toast.success(
            enabled
              ? t("liveTv.enabledToast", { name: channel.name })
              : t("liveTv.disabledToast", { name: channel.name }),
          );
        },
        onError: (error) => {
          notifyError(t, error);
        },
        onSettled: refresh,
      },
    );
  }

  function move(index: number, offset: -1 | 1): void {
    if (ordered === null) return;
    const ids = channels.map((channel) => channel.id);
    const moved = ids[index];
    const other = ids[index + offset];
    if (moved === undefined || other === undefined) return;
    ids[index] = other;
    ids[index + offset] = moved;
    reorder.mutate(
      { data: { group: ordered.id, ids } },
      {
        onError: (error) => {
          notifyError(t, error);
        },
        onSettled: refresh,
      },
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12 text-end">{t("liveTv.columns.number")}</TableHead>
            <TableHead>{t("liveTv.columns.channel")}</TableHead>
            <TableHead>{t("liveTv.columns.group")}</TableHead>
            <TableHead>{t("liveTv.columns.status")}</TableHead>
            <TableHead>{t("liveTv.columns.catchup")}</TableHead>
            <TableHead>{t("liveTv.columns.rights")}</TableHead>
            <TableHead>{t("liveTv.columns.enabled")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("liveTv.columns.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {channels.map((channel, index) => (
            <TableRow key={channel.id}>
              <TableCell className="text-end tabular-nums text-muted-foreground">
                {channel.xc_id}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-2.5">
                  {channel.logo_url ? (
                    <img
                      src={channel.logo_url}
                      alt=""
                      className="size-8 shrink-0 rounded bg-muted object-contain"
                      loading="lazy"
                    />
                  ) : (
                    <span className="flex size-8 shrink-0 items-center justify-center rounded bg-muted text-muted-foreground">
                      <Tv2 aria-hidden="true" className="size-4" />
                    </span>
                  )}
                  <div className="grid gap-0.5">
                    <bdi className="font-medium">{channel.name}</bdi>
                    {channel.name_ar ? (
                      <bdi lang="ar" className="text-xs text-muted-foreground">
                        {channel.name_ar}
                      </bdi>
                    ) : null}
                    {channel.epg_channel_id ? (
                      <span className="font-mono text-xs text-muted-foreground" dir="ltr">
                        {channel.epg_channel_id}
                      </span>
                    ) : null}
                  </div>
                </div>
              </TableCell>
              <TableCell>{localName(channel.group, i18n.language)}</TableCell>
              <TableCell>
                <ChannelState channel={channel} />
              </TableCell>
              <TableCell className="tabular-nums">
                {channel.catchup_days > 0 ? (
                  <div className="grid gap-0.5">
                    <span>{t("liveTv.catchupDays", { count: channel.catchup_days })}</span>
                    <span className="text-xs text-muted-foreground">
                      {format.bytes(channel.status.archive_bytes)}
                    </span>
                  </div>
                ) : (
                  <span className="text-muted-foreground">{t("liveTv.noCatchup")}</span>
                )}
              </TableCell>
              <TableCell>
                {channel.rights_holder ? (
                  <div className="grid gap-0.5">
                    <bdi className="text-sm">{channel.rights_holder}</bdi>
                    {channel.license_expires_at ? (
                      <span
                        className={
                          channel.license_valid
                            ? "text-xs text-muted-foreground"
                            : "text-xs text-danger-text"
                        }
                      >
                        {channel.license_valid
                          ? t("liveTv.licenseUntil", {
                              date: format.date(channel.license_expires_at),
                            })
                          : t("liveTv.licenseExpired")}
                      </span>
                    ) : null}
                  </div>
                ) : (
                  <Badge tone="warning">{t("liveTv.rightsMissing")}</Badge>
                )}
              </TableCell>
              <TableCell>
                <Switch
                  checked={channel.enabled}
                  disabled={!manage || bulk.isPending}
                  aria-label={t("liveTv.enableLabel", { name: channel.name })}
                  onCheckedChange={(checked) => {
                    setEnabled(channel, checked);
                  }}
                />
              </TableCell>
              <TableCell>
                {manage ? (
                  <div className="flex items-center justify-end gap-0.5">
                    {ordered !== null ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          disabled={index === 0 || reorder.isPending}
                          aria-label={t("liveTv.moveUp", { name: channel.name })}
                          onClick={() => {
                            move(index, -1);
                          }}
                        >
                          <ArrowUp aria-hidden="true" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          disabled={index === channels.length - 1 || reorder.isPending}
                          aria-label={t("liveTv.moveDown", { name: channel.name })}
                          onClick={() => {
                            move(index, 1);
                          }}
                        >
                          <ArrowDown aria-hidden="true" />
                        </Button>
                      </>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("liveTv.edit", { name: channel.name })}
                      onClick={() => {
                        onEdit(channel);
                      }}
                    >
                      <Pencil aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="text-danger-text"
                      aria-label={t("liveTv.delete.label", { name: channel.name })}
                      onClick={() => {
                        onDelete(channel);
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </div>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Loading() {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 p-4" role="status" aria-live="polite">
      <span className="sr-only">{t("layout.loading")}</span>
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
    </div>
  );
}

function ChannelsTab({
  groups,
  manage,
  budgetBytes,
}: {
  groups: readonly Category[];
  manage: boolean;
  budgetBytes: number;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const group = search.group;
  const query = useLiveChannelsList({
    page_size: PAGE_SIZE,
    ...(group ? { group } : {}),
    ...(search.q ? { q: search.q } : {}),
  });
  const remove = useLiveChannelsDelete();
  const [sheet, setSheet] = useState<{ open: boolean; channel: LiveChannel | null }>({
    open: false,
    channel: null,
  });
  const [deleting, setDeleting] = useState<LiveChannel | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const ordered = group ? (groups.find((item) => item.id === group) ?? null) : null;

  const newButton = manage ? (
    <Button
      disabled={groups.length === 0}
      onClick={() => {
        setSheet({ open: true, channel: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("liveTv.new")}
    </Button>
  ) : null;

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select
          value={group ?? ALL_GROUPS}
          onValueChange={(value) => {
            void navigate({
              search: (previous) =>
                compact({ ...previous, group: value === ALL_GROUPS ? undefined : value }),
              replace: true,
            });
          }}
        >
          <SelectTrigger className="w-56" aria-label={t("liveTv.filters.group")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_GROUPS}>{t("liveTv.filters.allGroups")}</SelectItem>
            {groups.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {localName(item, i18n.language)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          type="search"
          className="w-64"
          aria-label={t("liveTv.filters.search")}
          placeholder={t("liveTv.filters.search")}
          defaultValue={search.q ?? ""}
          onChange={(event) => {
            const value = event.target.value.trim();
            void navigate({
              search: (previous) => compact({ ...previous, q: value || undefined }),
              replace: true,
            });
          }}
        />
        <div className="ms-auto">{newButton}</div>
      </div>
      {group === undefined && manage ? (
        <p className="mb-2 text-xs text-muted-foreground">{t("liveTv.orderHint")}</p>
      ) : null}
      <Card className="p-0">
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.results.length === 0 ? (
          <EmptyState
            icon={<Radio />}
            title={t("liveTv.empty.title")}
            description={
              groups.length === 0 ? t("liveTv.empty.noGroups") : t("liveTv.empty.description")
            }
            action={newButton}
          />
        ) : (
          <ChannelRows
            channels={query.data.results}
            manage={manage}
            ordered={ordered}
            onEdit={(channel) => {
              setSheet({ open: true, channel });
            }}
            onDelete={(channel) => {
              setDeleting(channel);
              setDeleteOpen(true);
            }}
          />
        )}
      </Card>
      <ChannelSheet
        channel={sheet.channel}
        groups={groups}
        defaultGroup={group ?? groups[0]?.id}
        budgetBytes={budgetBytes}
        open={sheet.open}
        onOpenChange={(open) => {
          setSheet((previous) => ({ ...previous, open }));
        }}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        tone="danger"
        title={t("liveTv.delete.title", { name: deleting?.name ?? "" })}
        description={t("liveTv.delete.description")}
        confirmLabel={t("liveTv.delete.confirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
            toast.success(t("liveTv.delete.done", { name: deleting.name }));
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getLiveChannelsListQueryKey() });
          }
        }}
      />
    </>
  );
}

function GroupsTab({ groups, manage }: { groups: readonly Category[]; manage: boolean }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const remove = useCategoriesDelete();
  const [editing, setEditing] = useState<{ open: boolean; category: Category | null }>({
    open: false,
    category: null,
  });
  const [deleting, setDeleting] = useState<Category | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const newButton = manage ? (
    <Button
      onClick={() => {
        setEditing({ open: true, category: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("liveTv.groups.new")}
    </Button>
  ) : null;
  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <p className="text-sm text-muted-foreground">{t("liveTv.groups.description")}</p>
        <div className="ms-auto">{newButton}</div>
      </div>
      <Card className="p-0">
        {groups.length === 0 ? (
          <EmptyState
            icon={<Tv2 />}
            title={t("liveTv.groups.empty")}
            description={t("liveTv.groups.emptyDescription")}
            action={newButton}
          />
        ) : (
          <CategoryRows
            kind={CategoryKind.live}
            categories={groups}
            manage={manage}
            onEdit={(category) => {
              setEditing({ open: true, category });
            }}
            onDelete={(category) => {
              setDeleting(category);
              setDeleteOpen(true);
            }}
          />
        )}
      </Card>
      <Dialog
        open={editing.open}
        onOpenChange={(open) => {
          setEditing((previous) => ({ ...previous, open }));
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editing.category ? t("liveTv.groups.editTitle") : t("liveTv.groups.newTitle")}
            </DialogTitle>
            <DialogDescription>{t("liveTv.groups.formDescription")}</DialogDescription>
          </DialogHeader>
          {editing.open ? (
            <CategoryForm
              kind={CategoryKind.live}
              category={editing.category}
              onDone={() => {
                setEditing((previous) => ({ ...previous, open: false }));
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        tone="danger"
        title={t("liveTv.groups.deleteTitle", {
          name: deleting ? localName(deleting, i18n.language) : "",
        })}
        description={t("liveTv.groups.deleteDescription")}
        confirmLabel={t("liveTv.delete.confirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getCategoriesListQueryKey() });
          }
        }}
      />
    </>
  );
}

function syncSummary(result: unknown): Record<string, number> {
  const values: Record<string, number> = { created: 0, updated: 0, missing: 0, total: 0 };
  if (typeof result === "object" && result !== null) {
    for (const key of Object.keys(values)) {
      const value = (result as Record<string, unknown>)[key];
      if (typeof value === "number") values[key] = value;
    }
  }
  return values;
}

function IntegrationsTab({ manage }: { manage: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useLiveIntegrationsList(undefined, { query: { refetchInterval: 10_000 } });
  const sync = useLiveIntegrationsSync();
  const remove = useLiveIntegrationsDelete();
  const [sheet, setSheet] = useState<{ open: boolean; integration: LiveIntegration | null }>({
    open: false,
    integration: null,
  });
  const [deleting, setDeleting] = useState<LiveIntegration | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const newButton = manage ? (
    <Button
      onClick={() => {
        setSheet({ open: true, integration: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("liveTv.integrations.new")}
    </Button>
  ) : null;

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <p className="text-sm text-muted-foreground">{t("liveTv.integrations.intro")}</p>
        <div className="ms-auto">{newButton}</div>
      </div>
      {query.isPending ? (
        <Card className="p-0">
          <Loading />
        </Card>
      ) : query.isError ? (
        <Card className="p-0">
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        </Card>
      ) : query.data.length === 0 ? (
        <Card className="p-0">
          <EmptyState
            icon={<Plug />}
            title={t("liveTv.integrations.empty")}
            description={t("liveTv.integrations.emptyDescription")}
            action={newButton}
          />
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {query.data.map((integration) => {
            const summary = syncSummary(integration.last_result);
            return (
              <Card key={integration.id} className="grid gap-2 p-4">
                <div className="flex items-start gap-2">
                  <div className="grid gap-0.5">
                    <span className="font-medium">{integration.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {t(`liveTv.integrations.kinds.${integration.kind}`)} ·{" "}
                      <bdi dir="ltr" className="font-mono">
                        {integration.base_url}
                      </bdi>
                    </span>
                  </div>
                  <Badge tone="neutral" className="ms-auto">
                    {t("liveTv.integrations.channels", { count: integration.channel_count })}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {integration.last_sync_at ? (
                    <>
                      {t("liveTv.integrations.lastSync")}{" "}
                      <RelativeTime value={integration.last_sync_at} />
                      {" · "}
                      {t("liveTv.integrations.result", summary)}
                    </>
                  ) : (
                    t("liveTv.integrations.neverSynced")
                  )}
                </p>
                {integration.last_error ? (
                  <p className="text-xs text-danger-text">{integration.last_error}</p>
                ) : null}
                {manage ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      pending={sync.isPending && sync.variables.id === integration.id}
                      onClick={() => {
                        sync.mutate(
                          { id: integration.id },
                          {
                            onSuccess: () => {
                              toast.success(t("liveTv.integrations.syncQueued"));
                              void queryClient.invalidateQueries({
                                queryKey: getLiveChannelsListQueryKey(),
                              });
                            },
                            onError: (error) => {
                              notifyError(t, error);
                            },
                          },
                        );
                      }}
                    >
                      <RefreshCw aria-hidden="true" />
                      {t("liveTv.integrations.sync")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setSheet({ open: true, integration });
                      }}
                    >
                      <Pencil aria-hidden="true" />
                      {t("liveTv.integrations.edit")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-danger-text"
                      onClick={() => {
                        setDeleting(integration);
                        setDeleteOpen(true);
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                      {t("liveTv.integrations.delete")}
                    </Button>
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}
      <IntegrationSheet
        integration={sheet.integration}
        open={sheet.open}
        onOpenChange={(open) => {
          setSheet((previous) => ({ ...previous, open }));
        }}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        tone="danger"
        title={t("liveTv.integrations.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("liveTv.integrations.deleteDescription")}
        confirmLabel={t("liveTv.delete.confirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getLiveIntegrationsListQueryKey() });
          }
        }}
      />
    </>
  );
}

function Overview() {
  const { t } = useTranslation();
  const format = useFormatters();
  const query = useLiveOverview({ query: { refetchInterval: 10_000 } });
  const data = query.data;
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile
        label={t("liveTv.overview.packager")}
        value={
          data
            ? data.packager_running
              ? t("liveTv.overview.running", { count: data.running_channels })
              : t("liveTv.overview.stopped")
            : "—"
        }
        loading={query.isPending}
      />
      <StatTile
        label={t("liveTv.overview.channels")}
        value={
          data
            ? t("liveTv.overview.enabled", { enabled: data.enabled_channels, total: data.channels })
            : "—"
        }
        loading={query.isPending}
      />
      <StatTile
        label={t("liveTv.overview.viewers")}
        value={data ? format.number(data.viewers) : "—"}
        loading={query.isPending}
      />
      <StatTile
        label={t("liveTv.overview.archive")}
        value={
          data
            ? t("liveTv.overview.archiveUse", {
                used: format.bytes(data.archive_bytes),
                budget: format.bytes(data.archive_budget_bytes),
              })
            : "—"
        }
        loading={query.isPending}
      />
    </div>
  );
}

function LiveTv() {
  const { t } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const tab: LiveTab = search.tab ?? "channels";
  const groupsQuery = useCategoriesList({ kind: CategoryKind.live, page_size: PAGE_SIZE });
  const overview = useLiveOverview();
  const groups = groupsQuery.data?.results ?? [];
  return (
    <>
      <PageHeader title={t("liveTv.title")} description={t("liveTv.description")} />
      <Overview />
      <Tabs
        value={tab}
        onValueChange={(value) => {
          const next = LIVE_TABS.find((candidate) => candidate === value);
          void navigate({
            search: next && next !== "channels" ? { tab: next } : {},
            replace: true,
          });
        }}
        className="mb-4"
      >
        <TabsList>
          {LIVE_TABS.map((value) => (
            <TabsTrigger key={value} value={value} aria-controls="live-panel">
              {t(`liveTv.tabs.${value}`)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <section id="live-panel" role="tabpanel" aria-label={t(`liveTv.tabs.${tab}`)}>
        {groupsQuery.isError ? (
          <Card className="p-0">
            <QueryError
              error={groupsQuery.error}
              onRetry={() => {
                void groupsQuery.refetch();
              }}
            />
          </Card>
        ) : tab === "channels" ? (
          <ChannelsTab
            groups={groups}
            manage={manage}
            budgetBytes={overview.data?.archive_budget_bytes ?? 0}
          />
        ) : tab === "groups" ? (
          <GroupsTab groups={groups} manage={manage} />
        ) : (
          <IntegrationsTab manage={manage} />
        )}
      </section>
    </>
  );
}

export function LiveTvPage() {
  const { t } = useTranslation();
  usePageTitle(t("liveTv.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <LiveTv />
    </RequirePermission>
  );
}
