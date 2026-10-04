import {
  getMeDevicesListQueryKey,
  useMeDevicesDelete,
  useMeDevicesList,
  useMeDevicesUpdate,
  type MyDevice,
} from "@smart-iptv/api-portal";
import {
  Badge,
  Button,
  ConfirmDialog,
  DeviceIcon,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  ErrorState,
  Input,
  Label,
  RelativeTime,
  Skeleton,
  StatusBadge,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  MonitorSmartphone,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  Tv,
} from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { AddTvAppDialog, ResetCredentialsDialog } from "../features/account/add-tv-app";
import { SetupGuide } from "../features/account/setup-guide";
import { usePageTitle } from "../lib/page-title";

function RenameDialog({
  device,
  onOpenChange,
}: {
  device: MyDevice | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const queryClient = useQueryClient();
  const update = useMeDevicesUpdate();
  const [name, setName] = useState("");
  const [lastId, setLastId] = useState<string | null>(null);
  if (device !== null && device.id !== lastId) {
    setLastId(device.id);
    setName(device.name);
  }
  return (
    <Dialog open={device !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (device === null || name.trim() === "") return;
            update.mutate(
              { id: device.id, data: { name: name.trim() } },
              {
                onSuccess: () => {
                  void queryClient.invalidateQueries({ queryKey: getMeDevicesListQueryKey() });
                  toast.success(t("devices.renamed"));
                  onOpenChange(false);
                },
                onError: () => {
                  toast.error(t("devices.errors.UNEXPECTED"));
                },
              },
            );
          }}
        >
          <DialogHeader>
            <DialogTitle>{t("devices.rename")}</DialogTitle>
            <DialogDescription>{t("devices.renameHint")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor={id}>{t("devices.add.name")}</Label>
            <Input
              id={id}
              value={name}
              maxLength={100}
              required
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" pending={update.isPending} disabled={name.trim() === ""}>
              {t("common.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeviceRow({
  device,
  onRename,
  onReset,
  onRemove,
}: {
  device: MyDevice;
  onRename: () => void;
  onReset: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const xtream = device.kind === "xtream";
  const hint = xtream ? t(`devices.apps.${device.app_hint}`) : device.name;
  const [guide, setGuide] = useState(false);
  return (
    <li className="grid gap-3 rounded-card border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-muted">
          <DeviceIcon
            hint={xtream ? "tv" : device.kind === "web" ? "browser" : "phone"}
            decorative
            className="size-5 text-foreground"
          />
        </span>
        <div className="grid min-w-0 flex-1 gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">{device.name}</span>
            {device.current ? <Badge tone="info">{t("devices.thisDevice")}</Badge> : null}
            {device.status !== "active" ? <StatusBadge status={device.status} /> : null}
          </div>
          <p className="text-xs text-muted-foreground">
            {t(`devices.kinds.${device.kind}`)}
            {xtream && device.app_hint !== "other" ? ` · ${hint}` : ""}
            {device.xtream_username ? (
              <>
                {" · "}
                <span dir="ltr" className="font-mono">
                  {device.xtream_username}
                </span>
              </>
            ) : null}
          </p>
          <p className="text-xs text-muted-foreground">
            {device.last_seen ? (
              <>
                {t("devices.lastSeen")} <RelativeTime value={device.last_seen} />
              </>
            ) : (
              t("devices.neverUsed")
            )}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t("devices.actions", { name: device.name })}
            >
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onRename}>
              <Pencil aria-hidden="true" />
              {t("devices.rename")}
            </DropdownMenuItem>
            {xtream ? (
              <DropdownMenuItem onSelect={onReset}>
                <KeyRound aria-hidden="true" />
                {t("devices.newCredentials")}
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="danger" onSelect={onRemove}>
              <Trash2 aria-hidden="true" />
              {device.current ? t("devices.signOutHere") : t("devices.remove")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {xtream ? (
        <div>
          <Button
            variant="link"
            size="xs"
            aria-expanded={guide}
            onClick={() => {
              setGuide((open) => !open);
            }}
          >
            {guide ? t("devices.hideGuide") : t("devices.showGuide")}
          </Button>
          {guide ? <SetupGuide app={device.app_hint} /> : null}
        </div>
      ) : null}
    </li>
  );
}

/** Devices and TV apps (SPEC §9): browsers and apps signed in, IPTV app logins; add, reset, remove. */
export function DevicesPage() {
  const { t } = useTranslation();
  usePageTitle(t("devices.title"));
  const queryClient = useQueryClient();
  const devices = useMeDevicesList({ query: { refetchOnMount: "always" } });
  const remove = useMeDevicesDelete();
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<MyDevice | null>(null);
  const [resetting, setResetting] = useState<MyDevice | null>(null);
  const [removing, setRemoving] = useState<MyDevice | null>(null);
  const list = (devices.data ?? []).filter((device) => device.status !== "revoked");
  const tvApps = list.filter((device) => device.kind === "xtream");
  const others = list.filter((device) => device.kind !== "xtream");

  const row = (device: MyDevice) => (
    <DeviceRow
      key={device.id}
      device={device}
      onRename={() => {
        setRenaming(device);
      }}
      onReset={() => {
        setResetting(device);
      }}
      onRemove={() => {
        setRemoving(device);
      }}
    />
  );

  return (
    <div className="page-top mx-auto grid max-w-4xl gap-8 px-4 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="grid gap-1">
          <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">
            {t("devices.title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("devices.subtitle")}</p>
        </div>
        <Button
          onClick={() => {
            setAdding(true);
          }}
        >
          <Plus aria-hidden="true" />
          {t("devices.addTvApp")}
        </Button>
      </div>

      {devices.isError ? (
        <ErrorState
          error={devices.error}
          onRetry={() => {
            void devices.refetch();
          }}
        />
      ) : devices.isPending ? (
        <div className="grid gap-3" aria-busy="true">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <>
          <section aria-labelledby="tv-apps" className="grid gap-3">
            <h2 id="tv-apps" className="text-lg font-semibold text-foreground">
              {t("devices.tvApps")}
            </h2>
            {tvApps.length === 0 ? (
              <EmptyState
                icon={<Tv />}
                title={t("devices.noTvAppsTitle")}
                description={t("devices.noTvApps")}
                action={
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setAdding(true);
                    }}
                  >
                    <Plus aria-hidden="true" />
                    {t("devices.addTvApp")}
                  </Button>
                }
              />
            ) : (
              <ul role="list" className="grid gap-3">
                {tvApps.map(row)}
              </ul>
            )}
          </section>
          <section aria-labelledby="browsers" className="grid gap-3">
            <h2 id="browsers" className="text-lg font-semibold text-foreground">
              {t("devices.browsers")}
            </h2>
            {others.length === 0 ? (
              <EmptyState
                icon={<MonitorSmartphone />}
                title={t("devices.noBrowsersTitle")}
                description={t("devices.noBrowsers")}
              />
            ) : (
              <ul role="list" className="grid gap-3">
                {others.map(row)}
              </ul>
            )}
          </section>
        </>
      )}

      <AddTvAppDialog open={adding} onOpenChange={setAdding} />
      <ResetCredentialsDialog
        device={resetting}
        onOpenChange={(open) => {
          if (!open) setResetting(null);
        }}
      />
      <RenameDialog
        device={renaming}
        onOpenChange={(open) => {
          if (!open) setRenaming(null);
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        tone="danger"
        title={t("devices.removeTitle", { name: removing?.name ?? "" })}
        description={
          removing?.kind === "xtream"
            ? t("devices.removeXtream")
            : removing?.current
              ? t("devices.removeCurrent")
              : t("devices.removeBrowser")
        }
        confirmLabel={t("devices.remove")}
        cancelLabel={t("common.cancel")}
        onConfirm={async () => {
          if (removing === null) return;
          try {
            await remove.mutateAsync({ id: removing.id });
            toast.success(t("devices.removed"));
            void queryClient.invalidateQueries({ queryKey: getMeDevicesListQueryKey() });
            setRemoving(null);
          } catch {
            toast.error(t("devices.errors.UNEXPECTED"));
          }
        }}
      />
    </div>
  );
}
