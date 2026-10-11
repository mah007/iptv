import {
  getSettingsListQueryKey,
  useSettingsImageUpload,
  type SettingEntry,
} from "@smart-iptv/api";
import { Button, ConfirmDialog, toast } from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { ImageOff, Upload } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { notifyError } from "../../lib/problems";
import { useSettingActions } from "./use-setting-actions";

const ACCEPT = "image/png,image/jpeg,image/webp";
const MAX_BYTES = 2 * 1024 * 1024;
/** A worker stores the upload; the settings list is read again until it shows up. */
const POLL_MS = 2000;
const POLL_TRIES = 15;

/** An image setting (the logos): preview, upload and remove (ADR-0019). */
export function LogoControl({
  entry,
  label,
  canEdit,
  describedBy,
}: {
  entry: SettingEntry;
  label: string;
  canEdit: boolean;
  describedBy: string;
}) {
  const { t } = useTranslation();
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const upload = useSettingsImageUpload();
  const actions = useSettingActions();
  const [waitingFor, setWaitingFor] = useState<string | null>(null);

  // The worker has stored the logo once the setting's value changes.
  if (waitingFor !== null && displayed(entry) !== waitingFor) setWaitingFor(null);

  // Until then, read the settings again every few seconds.
  useEffect(() => {
    if (waitingFor === null) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      void queryClient.invalidateQueries({ queryKey: getSettingsListQueryKey() });
      if (tries >= POLL_TRIES) {
        window.clearInterval(timer);
        setWaitingFor(null);
        toast.error(t("settings.logo.slow"));
      }
    }, POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [waitingFor, queryClient, t]);

  function choose(file: File | undefined): void {
    if (file === undefined) return;
    if (file.size > MAX_BYTES) {
      toast.error(t("settings.logo.tooLarge"));
      return;
    }
    const before = displayed(entry);
    upload.mutate(
      { key: entry.key, data: { file } },
      {
        onSuccess: () => {
          setWaitingFor(before);
          toast.info(t("settings.logo.uploaded"));
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  }

  const busy = upload.isPending || waitingFor !== null || actions.pending;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div
        className="grid h-14 w-40 place-items-center overflow-hidden rounded-input border border-border bg-muted/40 p-1.5"
        aria-describedby={describedBy}
      >
        {entry.image_url ? (
          <img
            src={entry.image_url}
            alt={t("settings.logo.preview", { name: label })}
            className="max-h-full max-w-full object-contain"
          />
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <ImageOff aria-hidden="true" className="size-4" />
            {t("settings.logo.none")}
          </span>
        )}
      </div>
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={input}
            id={inputId}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-label={t("settings.logo.choose", { name: label })}
            onChange={(event) => {
              choose(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <Button
            type="button"
            size="sm"
            variant="secondary"
            pending={busy}
            onClick={() => input.current?.click()}
          >
            <Upload aria-hidden="true" />
            {entry.image_url ? t("settings.logo.replace") : t("settings.logo.upload")}
          </Button>
          {entry.image_url ? (
            <ConfirmDialog
              title={t("settings.logo.removeTitle", { name: label })}
              confirmLabel={t("settings.logo.remove")}
              tone="danger"
              onConfirm={() =>
                actions.reset(entry.key).then(
                  () => {
                    toast.success(t("settings.logo.removed", { name: label }));
                  },
                  (error: unknown) => {
                    notifyError(t, error);
                  },
                )
              }
              trigger={
                <Button type="button" size="sm" variant="ghost" disabled={busy}>
                  {t("settings.logo.remove")}
                </Button>
              }
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function displayed(entry: SettingEntry): string {
  return typeof entry.value === "string" ? entry.value : "";
}
