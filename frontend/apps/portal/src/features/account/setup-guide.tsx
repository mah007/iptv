import { Alert } from "@smart-iptv/ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { APP_HINTS, MAC_ACTIVATED, type AppHintValue } from "./credentials";

function isKnownApp(value: string): value is AppHintValue {
  return (APP_HINTS as readonly string[]).includes(value);
}

/**
 * How to enter the login in the app the customer picked (SPEC §9 setup
 * guides): numbered steps per app, with the warning for apps activated by MAC
 * address, whose vendors keep the login on their servers.
 */
export function SetupGuide({ app }: { app: string }) {
  const { t } = useTranslation();
  const headingId = useId();
  const hint: AppHintValue = isKnownApp(app) ? app : "other";
  const steps = t(`devices.guides.${hint}`, { returnObjects: true }) as unknown;
  const list = Array.isArray(steps)
    ? steps.filter((step): step is string => typeof step === "string")
    : [];
  return (
    <section
      aria-labelledby={headingId}
      className="grid gap-3 rounded-card border border-border bg-muted/40 p-4"
    >
      <h3 id={headingId} className="text-sm font-semibold text-foreground">
        {t("devices.guideTitle", { app: t(`devices.apps.${hint}`) })}
      </h3>
      <ol className="grid list-decimal gap-1.5 ps-5 text-sm text-muted-foreground marker:text-foreground">
        {list.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {MAC_ACTIVATED.has(hint) ? <Alert tone="warning">{t("devices.macWarning")}</Alert> : null}
    </section>
  );
}
