import { getCustomersListQueryKey, useDevicesRevokeByApp, type AppHint } from "@smart-iptv/api";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { ShieldOff } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { notifyError } from "../../lib/problems";
import { APP_HINTS } from "./schemas";

/**
 * G-26: revoke every IPTV login made for one app at once, after a vendor breach or a
 * public leak. It counts first (a dry run); the real run asks the admin to confirm it's
 * them (ADR-0021).
 */
export function RevokeByAppDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const appId = useId();
  const notifyId = useId();
  const [app, setApp] = useState<AppHint | "">("");
  const [notify, setNotify] = useState(true);
  const [counted, setCounted] = useState<number | null>(null);
  const revoke = useDevicesRevokeByApp();

  function close(next: boolean): void {
    if (!next) {
      setApp("");
      setCounted(null);
      setNotify(true);
    }
    onOpenChange(next);
  }

  async function count(hint: AppHint): Promise<void> {
    setCounted(null);
    try {
      const result = await revoke.mutateAsync({ data: { app_hint: hint, dry_run: true } });
      setCounted(result.devices);
    } catch (error) {
      notifyError(t, error);
    }
  }

  async function run(): Promise<void> {
    if (!app) return;
    try {
      const result = await revoke.mutateAsync({
        data: { app_hint: app, dry_run: false, notify },
      });
      toast.success(t("revokeByApp.done", { count: result.devices }));
      void queryClient.invalidateQueries({ queryKey: getCustomersListQueryKey() });
      close(false);
    } catch (error) {
      notifyError(t, error);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("revokeByApp.title")}</DialogTitle>
          <DialogDescription>{t("revokeByApp.description")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor={appId}>{t("revokeByApp.app")}</Label>
            <Select
              value={app}
              onValueChange={(value) => {
                const hint = value as AppHint;
                setApp(hint);
                void count(hint);
              }}
            >
              <SelectTrigger id={appId}>
                <SelectValue placeholder={t("revokeByApp.pick")} />
              </SelectTrigger>
              <SelectContent>
                {APP_HINTS.map((hint) => (
                  <SelectItem key={hint} value={hint}>
                    {t(`devices.apps.${hint}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {counted !== null ? (
            <Alert tone={counted > 0 ? "warning" : "info"}>
              {t("revokeByApp.counted", { count: counted, formatted: format.number(counted) })}
            </Alert>
          ) : null}
          <div className="flex items-start gap-2">
            <Checkbox
              id={notifyId}
              checked={notify}
              onCheckedChange={(value) => {
                setNotify(value === true);
              }}
            />
            <Label htmlFor={notifyId} className="leading-snug font-normal">
              {t("revokeByApp.notify")}
            </Label>
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              close(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            variant="danger"
            disabled={!app || !counted}
            pending={revoke.isPending}
            onClick={() => void run()}
          >
            <ShieldOff aria-hidden="true" />
            {t("revokeByApp.confirm", { count: counted ?? 0 })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
