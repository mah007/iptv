import { useAuthMe, useSettingsList } from "@smart-iptv/api";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useFormatters,
  useNow,
} from "@smart-iptv/ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCan } from "../lib/auth";

/** The server's default (SESSION_COOKIE_AGE); the security setting overrides it. */
const DEFAULT_TIMEOUT_MIN = 30;
/** The warning shows this long before the session ends. */
const WARNING_MS = 2 * 60_000;
/** Activity is recorded at most this often (and shared with other tabs). */
const RECORD_EVERY_MS = 10_000;
const STORAGE_KEY = "smart-iptv.activity";
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

function storedActivity(): number {
  try {
    return Number(window.localStorage.getItem(STORAGE_KEY)) || 0;
  } catch {
    return 0;
  }
}

function useTimeoutMinutes(): number {
  const can = useCan();
  const settings = useSettingsList({
    query: { enabled: can("settings.view"), staleTime: 300_000 },
  });
  const value = settings.data?.find(
    (entry) => entry.key === "security.admin_idle_timeout_min",
  )?.value;
  return typeof value === "number" && value > 0 ? value : DEFAULT_TIMEOUT_MIN;
}

/**
 * SPEC §8.2 session security: after the idle timeout without input (in any tab
 * of the admin) the admin is signed out, with a warning two minutes before.
 * Background refreshes don't count as activity, so an open dashboard doesn't
 * keep an unattended session alive.
 */
export function IdleTimeout({ onSignOut }: { onSignOut: () => void }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const timeoutMs = useTimeoutMinutes() * 60_000;
  const now = useNow(1000);
  const [lastActivity, setLastActivity] = useState(() => Math.max(Date.now(), storedActivity()));
  const warningRef = useRef(false);
  const signedOut = useRef(false);
  const me = useAuthMe({ query: { enabled: false } });

  const remaining = lastActivity + timeoutMs - now;
  const warning = remaining <= WARNING_MS && remaining > 0;

  useEffect(() => {
    warningRef.current = warning;
  });

  useEffect(() => {
    let recorded = 0;
    function record(): void {
      if (warningRef.current) return; // only "Stay signed in" ends the warning
      const at = Date.now();
      if (at - recorded < RECORD_EVERY_MS) return;
      recorded = at;
      setLastActivity(at);
      try {
        window.localStorage.setItem(STORAGE_KEY, String(at));
      } catch {
        // Storage may be unavailable (private mode); this tab still tracks itself.
      }
    }
    function onStorage(event: StorageEvent): void {
      if (event.key !== STORAGE_KEY || event.newValue === null) return;
      const at = Number(event.newValue);
      if (Number.isFinite(at)) setLastActivity((current) => Math.max(current, at));
    }
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, record, { passive: true });
    window.addEventListener("storage", onStorage);
    return () => {
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, record);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  useEffect(() => {
    if (remaining > 0 || signedOut.current) return;
    signedOut.current = true;
    onSignOut();
  }, [remaining, onSignOut]);

  function stay(): void {
    const at = Date.now();
    setLastActivity(at);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(at));
    } catch {
      // See record().
    }
    // A request renews the server's session too.
    void me.refetch();
  }

  return (
    <Dialog open={warning}>
      <DialogContent
        hideClose
        role="alertdialog"
        onEscapeKeyDown={(event) => {
          event.preventDefault();
        }}
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("idle.title")}</DialogTitle>
          <DialogDescription>
            {t("idle.description", {
              time: format.duration(Math.max(0, Math.ceil(remaining / 1000))),
            })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="secondary" onClick={onSignOut}>
            {t("userMenu.signOut")}
          </Button>
          <Button onClick={stay}>{t("idle.stay")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
