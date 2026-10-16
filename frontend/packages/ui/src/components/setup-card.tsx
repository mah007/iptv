import { Printer } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { useFormatters } from "../lib/format";
import { Button } from "./button";
import { Checkbox } from "./checkbox";
import { Label } from "./label";

/** One IPTV login as a setup card shows it (G-25, ADR-0024). */
export interface SetupCardLogin {
  serviceName: string;
  serverUrl: string;
  username: string;
  password: string;
  deviceName?: string;
}

/**
 * What the card's QR code holds: the server and the username, and the password only when
 * it is printed (the same JSON as the one-time reveal's code, SPEC §8.3.2).
 */
export function setupCardQrValue(login: SetupCardLogin, withPassword: boolean): string {
  return JSON.stringify({
    server: login.serverUrl,
    username: login.username,
    ...(withPassword ? { password: login.password } : {}),
  });
}

/** Printing adds this class to <html>; the print stylesheet then shows only the card. */
export const PRINTING_CLASS = "printing-setup-card";

/**
 * The printed card: black on white whatever the theme, in the page's language and
 * direction. Drawn in the browser from what the page already holds; nothing is sent,
 * stored or logged (G-25).
 */
export function SetupCard({
  login,
  withPassword,
  printedAt,
}: {
  login: SetupCardLogin;
  withPassword: boolean;
  printedAt: Date;
}) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const rows: { label: string; value: string }[] = [
    { label: t("setupCard.server"), value: login.serverUrl },
    { label: t("setupCard.username"), value: login.username },
    {
      label: t("setupCard.password"),
      value: withPassword ? login.password : t("setupCard.passwordNotPrinted"),
    },
  ];
  return (
    <article
      data-slot="setup-card"
      aria-label={t("setupCard.title")}
      className="mx-auto grid max-w-[170mm] gap-5 rounded-[3mm] border border-black/50 bg-white p-[8mm] text-black"
    >
      <header className="grid gap-1 border-b border-black/30 pb-3">
        <p className="text-sm font-semibold tracking-wide uppercase">{login.serviceName}</p>
        <h1 className="text-2xl font-bold">{t("setupCard.title")}</h1>
        {login.deviceName ? (
          <p className="text-sm" dir="auto">
            {t("setupCard.device", { name: login.deviceName })}
          </p>
        ) : null}
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-6">
        <dl className="grid gap-3">
          {rows.map((row) => (
            <div key={row.label} className="grid gap-0.5">
              <dt className="text-xs font-semibold uppercase">{row.label}</dt>
              <dd className="font-mono text-lg break-all" dir="ltr">
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
        <figure className="grid justify-items-center gap-1">
          <QRCodeSVG
            value={setupCardQrValue(login, withPassword)}
            size={150}
            level="M"
            marginSize={2}
            bgColor="#ffffff"
            fgColor="#000000"
            role="img"
            aria-label={
              withPassword ? t("setupCard.qrWithPassword") : t("setupCard.qrWithoutPassword")
            }
          />
          <figcaption className="max-w-[150px] text-center text-xs">
            {t("setupCard.qrHint")}
          </figcaption>
        </figure>
      </div>
      <ol className="grid list-decimal gap-1 ps-5 text-sm">
        <li>{t("setupCard.steps.app")}</li>
        <li>{t("setupCard.steps.xtream")}</li>
        <li>{t("setupCard.steps.enter")}</li>
      </ol>
      <footer className="grid gap-1 border-t border-black/30 pt-3 text-xs">
        <p className="font-semibold">{t("setupCard.keepSafe")}</p>
        <p>{t("setupCard.printedAt", { date: format.dateTime(printedAt) })}</p>
      </footer>
    </article>
  );
}

/**
 * "Print setup card" for a login on its one-time reveal screen: the browser's print
 * dialog with only the card on the page (print it, or choose "Save as PDF"). The password
 * goes on the card only when the box is ticked; the card is removed again once printing
 * ends.
 */
export function PrintSetupCard({
  login,
  disabled = false,
}: {
  login: SetupCardLogin;
  disabled?: boolean;
}) {
  const { t } = useTranslation("ui");
  const checkboxId = useId();
  const [withPassword, setWithPassword] = useState(true);
  const [printing, setPrinting] = useState<Date | null>(null);

  useEffect(() => {
    if (printing === null) return undefined;
    const root = document.documentElement;
    root.classList.add(PRINTING_CLASS);
    const done = (): void => {
      root.classList.remove(PRINTING_CLASS);
      setPrinting(null);
    };
    window.addEventListener("afterprint", done, { once: true });
    // After the card is on the page: print() blocks until the dialog closes.
    const frame = window.requestAnimationFrame(() => {
      window.print();
    });
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("afterprint", done);
      root.classList.remove(PRINTING_CLASS);
    };
  }, [printing]);

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <Button
        type="button"
        variant="secondary"
        disabled={disabled}
        onClick={() => {
          setPrinting(new Date());
        }}
      >
        <Printer aria-hidden="true" />
        {t("setupCard.print")}
      </Button>
      <div className="flex items-center gap-2">
        <Checkbox
          id={checkboxId}
          checked={withPassword}
          disabled={disabled}
          onCheckedChange={(checked) => {
            setWithPassword(checked === true);
          }}
        />
        <Label htmlFor={checkboxId} className="text-ui font-normal">
          {t("setupCard.includePassword")}
        </Label>
      </div>
      {printing !== null
        ? createPortal(
            <div data-print-root="setup-card">
              <SetupCard login={login} withPassword={withPassword} printedAt={printing} />
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
