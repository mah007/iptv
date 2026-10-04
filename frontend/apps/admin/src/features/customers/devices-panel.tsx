import { zodResolver } from "@hookform/resolvers/zod";
import {
  useCustomersDevicesCreate,
  useDevicesApprove,
  useDevicesBlock,
  useDevicesResetCredentials,
  useDevicesRevoke,
  useDevicesUnblock,
  type CustomerDetail,
  type Device,
  type IssuedCredential,
} from "@smart-iptv/api";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  CountryFlag,
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
  Form,
  Label,
  RelativeTime,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  cn,
  toast,
  useCopy,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Check,
  CircleCheck,
  Copy,
  KeyRound,
  MonitorSmartphone,
  MoreHorizontal,
  Plus,
  ShieldOff,
  Trash2,
} from "lucide-react";
import { useId, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { useCan } from "../../lib/auth";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { invalidateCustomer } from "./cache";
import { CredentialFields, DeviceFields } from "./form-fields";
import { IssuedCredentialPanel } from "./issued-credential";
import {
  DEVICE_DEFAULTS,
  DEVICE_FIELD_PATHS,
  RESET_FIELD_PATHS,
  deviceFormSchema,
  deviceRequest,
  resetDefaults,
  resetFormSchema,
  resetRequest,
  type DeviceFormInput,
  type DeviceFormValues,
  type ResetFormInput,
  type ResetFormValues,
} from "./schemas";

interface Issued {
  credential: IssuedCredential;
  kind: "added" | "reset";
}
interface PendingAction {
  kind: "reset" | "block" | "revoke";
  device: Device;
}

function deviceName(device: Device, unnamed: string): string {
  return device.name || unnamed;
}

/** Copy a short value (a username) from a table cell. */
function CopyValue({ value, label }: { value: string; label: string }) {
  const { copied, copy } = useCopy();
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      className="text-muted-foreground hover:text-foreground"
      aria-label={label}
      onClick={() => {
        void copy(value);
      }}
    >
      {copied ? <Check aria-hidden="true" className="text-success" /> : <Copy aria-hidden="true" />}
    </Button>
  );
}

/** The credential that was just issued: shown once, then gone. */
function IssuedDialog({ issued, onClose }: { issued: Issued | null; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={issued !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        className="max-w-2xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        {issued ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CircleCheck aria-hidden="true" className="size-5 text-success" />
                {issued.kind === "reset"
                  ? t("devices.issued.resetTitle")
                  : t("devices.issued.addedTitle")}
              </DialogTitle>
              <DialogDescription>
                {issued.kind === "reset"
                  ? t("devices.issued.resetDescription")
                  : t("devices.issued.addedDescription")}
              </DialogDescription>
            </DialogHeader>
            <IssuedCredentialPanel credential={issued.credential} />
            <DialogFooter>
              <Button onClick={onClose}>{t("devices.issued.done")}</Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/** "Add device": a new IPTV app login for this customer. */
function AddDeviceDialog({
  customerId,
  open,
  onOpenChange,
  onIssued,
}: {
  customerId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onIssued: (credential: IssuedCredential) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const form = useForm<DeviceFormInput, unknown, DeviceFormValues>({
    resolver: zodResolver(deviceFormSchema),
    defaultValues: { device: DEVICE_DEFAULTS },
  });
  const create = useCustomersDevicesCreate();

  const submit = form.handleSubmit(async ({ device }) => {
    try {
      const credential = await create.mutateAsync({ id: customerId, data: deviceRequest(device) });
      form.reset();
      onOpenChange(false);
      onIssued(credential);
      void invalidateCustomer(queryClient, customerId);
    } catch (error) {
      const fields = applyFieldErrors(error, form.setError, DEVICE_FIELD_PATHS, t);
      if (fields.length === 0) notifyError(t, error);
    }
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) form.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("devices.add.title")}</DialogTitle>
          <DialogDescription>{t("devices.add.description")}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            noValidate
            className="grid gap-4"
            onSubmit={(event) => {
              void submit(event);
            }}
          >
            <DeviceFields />
            <DialogFooter>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  onOpenChange(false);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" pending={form.formState.isSubmitting}>
                {t("devices.add.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

/** The reset form of one device, mounted fresh each time the dialog opens. */
function ResetForm({
  device,
  onClose,
  onIssued,
}: {
  device: Device;
  onClose: () => void;
  onIssued: (credential: IssuedCredential) => void;
}) {
  const { t } = useTranslation();
  const reset = useDevicesResetCredentials();
  const form = useForm<ResetFormInput, unknown, ResetFormValues>({
    resolver: zodResolver(resetFormSchema),
    defaultValues: resetDefaults(device.xtream_username ?? ""),
  });

  async function submit(values: ResetFormValues): Promise<void> {
    try {
      const credential = await reset.mutateAsync({
        id: device.id,
        data: resetRequest(values, device.xtream_username),
      });
      onClose();
      onIssued(credential);
    } catch (error) {
      const fields = applyFieldErrors(error, form.setError, RESET_FIELD_PATHS, t);
      if (fields.length === 0) notifyError(t, error);
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {t("devices.reset.title", { name: deviceName(device, t("devices.unnamed")) })}
        </DialogTitle>
        <DialogDescription>{t("devices.reset.description")}</DialogDescription>
      </DialogHeader>
      <Form {...form}>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            void form.handleSubmit(submit)(event);
          }}
        >
          <CredentialFields resetting />
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" pending={form.formState.isSubmitting}>
              {t("devices.reset.confirm")}
            </Button>
          </DialogFooter>
        </form>
      </Form>
    </>
  );
}

/** "Reset password": a generated one, or one the admin sets (optionally renaming the login). */
function ResetDialog({
  device,
  onOpenChange,
  onIssued,
}: {
  device: Device | null;
  onOpenChange: (open: boolean) => void;
  onIssued: (credential: IssuedCredential) => void;
}) {
  return (
    <Dialog open={device !== null} onOpenChange={onOpenChange}>
      <DialogContent
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        {device ? (
          <ResetForm
            key={device.id}
            device={device}
            onClose={() => {
              onOpenChange(false);
            }}
            onIssued={onIssued}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function DeviceRow({
  device,
  manage,
  onAction,
  onApprove,
  onUnblock,
}: {
  device: Device;
  manage: boolean;
  onAction: (action: PendingAction) => void;
  onApprove: (device: Device) => void;
  onUnblock: (device: Device) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const revoked = device.status === "revoked";
  const appLabel = t(`devices.apps.${device.app_hint}`);
  return (
    <TableRow className={cn(revoked && "opacity-60")}>
      <TableCell>
        <div className="flex min-w-0 items-center gap-2.5">
          <DeviceIcon hint={device.app_hint} decorative className="size-5" />
          <div className="grid min-w-0">
            <span className="truncate font-medium text-foreground">
              {deviceName(device, t("devices.unnamed"))}
            </span>
            <span className="truncate text-xs text-muted-foreground">{appLabel}</span>
          </div>
        </div>
      </TableCell>
      <TableCell>
        {device.xtream_username ? (
          <span className="inline-flex items-center gap-1">
            <span className="font-mono text-ui" dir="ltr">
              {device.xtream_username}
            </span>
            <CopyValue value={device.xtream_username} label={t("devices.copyUsername")} />
          </span>
        ) : (
          <span className="text-muted-foreground">{t("devices.noCredential")}</span>
        )}
      </TableCell>
      <TableCell>
        {device.blocked && device.blocked_reason ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                className="rounded-badge outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <StatusBadge status={device.status} />
              </span>
            </TooltipTrigger>
            <TooltipContent>{device.blocked_reason}</TooltipContent>
          </Tooltip>
        ) : (
          <StatusBadge status={device.status} />
        )}
      </TableCell>
      <TableCell>
        {device.last_seen ? (
          <div className="grid">
            <RelativeTime value={device.last_seen} />
            {device.last_ip ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                {device.last_country ? <CountryFlag code={device.last_country} /> : null}
                <span className="font-mono" dir="ltr">
                  {device.last_ip}
                </span>
              </span>
            ) : null}
          </div>
        ) : (
          <span className="text-muted-foreground">{t("devices.neverUsed")}</span>
        )}
      </TableCell>
      <TableCell>
        <span title={format.dateTime(device.created_at)}>
          <RelativeTime value={device.created_at} />
        </span>
      </TableCell>
      <TableCell className="w-10 text-end">
        {manage && !revoked ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t("devices.actions.label", {
                  name: deviceName(device, t("devices.unnamed")),
                })}
              >
                <MoreHorizontal aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              {device.xtream_username ? (
                <DropdownMenuItem
                  onSelect={() => {
                    onAction({ kind: "reset", device });
                  }}
                >
                  <KeyRound aria-hidden="true" />
                  {t("devices.actions.reset")}
                </DropdownMenuItem>
              ) : null}
              {device.status === "pending" ? (
                <DropdownMenuItem
                  onSelect={() => {
                    onApprove(device);
                  }}
                >
                  <CircleCheck aria-hidden="true" />
                  {t("devices.actions.approve")}
                </DropdownMenuItem>
              ) : null}
              {device.blocked ? (
                <DropdownMenuItem
                  onSelect={() => {
                    onUnblock(device);
                  }}
                >
                  <ShieldOff aria-hidden="true" />
                  {t("devices.actions.unblock")}
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem
                  onSelect={() => {
                    onAction({ kind: "block", device });
                  }}
                >
                  <Ban aria-hidden="true" />
                  {t("devices.actions.block")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-danger-text focus:text-danger-text"
                onSelect={() => {
                  onAction({ kind: "revoke", device });
                }}
              >
                <Trash2 aria-hidden="true" />
                {t("devices.actions.revoke")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

/** Devices & credentials tab (SPEC §8.3.3): add, reset with reveal-once, block, approve, revoke. */
export function DevicesPanel({ customer }: { customer: CustomerDetail }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const manage = can("devices.manage");
  const queryClient = useQueryClient();
  const reasonId = useId();
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [action, setAction] = useState<PendingAction | null>(null);
  const [reason, setReason] = useState("");
  const [showRevoked, setShowRevoked] = useState(false);

  const block = useDevicesBlock();
  const unblock = useDevicesUnblock();
  const approve = useDevicesApprove();
  const revoke = useDevicesRevoke();

  const current = customer.devices.filter((device) => device.status !== "revoked");
  const revoked = customer.devices.filter((device) => device.status === "revoked");
  const shown = showRevoked ? [...current, ...revoked] : current;
  const max = customer.access?.max_devices ?? null;
  const atLimit = max !== null && current.length >= max;
  const unnamed = t("devices.unnamed");

  function refresh(): void {
    void invalidateCustomer(queryClient, customer.id);
  }

  /** Run a confirmed action; a failure keeps the dialog open (ConfirmDialog contract). */
  async function confirm(run: () => Promise<unknown>): Promise<void> {
    try {
      await run();
    } catch (error) {
      notifyError(t, error);
      throw error;
    } finally {
      refresh();
    }
  }

  function closeAction(open: boolean): void {
    if (!open) {
      setAction(null);
      setReason("");
    }
  }

  const name = action ? deviceName(action.device, unnamed) : "";

  return (
    <>
      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 pb-(--density-card)">
          <div className="grid gap-1">
            <CardTitle>{t("devices.title")}</CardTitle>
            <CardDescription>
              {max === null
                ? t("devices.usageNoLimit", { count: current.length })
                : t("devices.usage", {
                    used: format.number(current.length),
                    max: format.number(max),
                  })}
            </CardDescription>
          </div>
          {manage ? (
            atLimit ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    tabIndex={0}
                    className="rounded-input outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Button disabled className="pointer-events-none">
                      <Plus aria-hidden="true" />
                      {t("devices.add.button")}
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-64">{t("devices.add.atLimit")}</TooltipContent>
              </Tooltip>
            ) : (
              <Button
                onClick={() => {
                  setAdding(true);
                }}
              >
                <Plus aria-hidden="true" />
                {t("devices.add.button")}
              </Button>
            )
          ) : null}
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {shown.length === 0 ? (
            <EmptyState
              className="border-t border-border"
              icon={<MonitorSmartphone />}
              title={t("devices.empty.title")}
              description={t("devices.empty.description")}
            />
          ) : (
            <Table aria-label={t("devices.title")} className="border-t border-border">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("devices.columns.device")}</TableHead>
                  <TableHead>{t("devices.columns.username")}</TableHead>
                  <TableHead>{t("devices.columns.status")}</TableHead>
                  <TableHead>{t("devices.columns.lastSeen")}</TableHead>
                  <TableHead>{t("devices.columns.added")}</TableHead>
                  <TableHead className="w-10">
                    <span className="sr-only">{t("devices.columns.actions")}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((device) => (
                  <DeviceRow
                    key={device.id}
                    device={device}
                    manage={manage}
                    onAction={setAction}
                    onApprove={(target) => {
                      approve.mutate(
                        { id: target.id },
                        {
                          onSuccess: () => {
                            toast.success(t("devices.toasts.approved"));
                            refresh();
                          },
                          onError: (error) => {
                            notifyError(t, error);
                          },
                        },
                      );
                    }}
                    onUnblock={(target) => {
                      unblock.mutate(
                        { id: target.id },
                        {
                          onSuccess: () => {
                            toast.success(t("devices.toasts.unblocked"));
                            refresh();
                          },
                          onError: (error) => {
                            notifyError(t, error);
                          },
                        },
                      );
                    }}
                  />
                ))}
              </TableBody>
            </Table>
          )}
          {revoked.length > 0 ? (
            <div className="border-t border-border px-(--density-card) py-2">
              <Button
                variant="link"
                size="xs"
                onClick={() => {
                  setShowRevoked(!showRevoked);
                }}
              >
                {showRevoked
                  ? t("devices.hideRevoked")
                  : t("devices.showRevoked", { count: revoked.length })}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {manage ? (
        <AddDeviceDialog
          customerId={customer.id}
          open={adding}
          onOpenChange={setAdding}
          onIssued={(credential) => {
            setIssued({ credential, kind: "added" });
          }}
        />
      ) : null}
      <IssuedDialog
        issued={issued}
        onClose={() => {
          setIssued(null);
        }}
      />

      {manage ? (
        <ResetDialog
          device={action?.kind === "reset" ? action.device : null}
          onOpenChange={(open) => {
            closeAction(open);
            if (!open) refresh();
          }}
          onIssued={(credential) => {
            setIssued({ credential, kind: "reset" });
          }}
        />
      ) : null}
      <ConfirmDialog
        open={action?.kind === "block"}
        onOpenChange={closeAction}
        title={t("devices.block.title", { name })}
        description={t("devices.block.description")}
        confirmLabel={t("devices.block.confirm")}
        tone="danger"
        onConfirm={() =>
          confirm(async () => {
            if (!action) return;
            await block.mutateAsync({
              id: action.device.id,
              data: reason.trim() ? { reason: reason.trim() } : {},
            });
            toast.success(t("devices.toasts.blocked"));
          })
        }
      >
        <div className="grid gap-1.5">
          <Label htmlFor={reasonId}>{t("devices.block.reason")}</Label>
          <Textarea
            id={reasonId}
            rows={2}
            maxLength={200}
            dir="auto"
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </div>
      </ConfirmDialog>
      <ConfirmDialog
        open={action?.kind === "revoke"}
        onOpenChange={closeAction}
        title={t("devices.revoke.title", { name })}
        description={t("devices.revoke.description")}
        confirmLabel={t("devices.revoke.confirm")}
        tone="danger"
        {...(action?.device.xtream_username
          ? { confirmationText: action.device.xtream_username }
          : {})}
        onConfirm={() =>
          confirm(async () => {
            if (!action) return;
            await revoke.mutateAsync({ id: action.device.id });
            toast.success(t("devices.toasts.revoked"));
          })
        }
      />
    </>
  );
}
