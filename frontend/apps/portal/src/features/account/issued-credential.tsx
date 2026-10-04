import type { MyIssuedCredential } from "@smart-iptv/api-portal";
import { Alert, Button, CopyField, QRCodeCard, SecretReveal, useCopy } from "@smart-iptv/ui";
import { ClipboardCopy, EyeOff } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { credentialQrValue } from "./credentials";

/**
 * A TV app login right after it was issued (add or reset). The password exists
 * only in this response: it can be revealed and copied here, and once hidden,
 * the password and the QR code that contains it are gone from the page.
 */
export function IssuedCredentialPanel({ credential }: { credential: MyIssuedCredential }) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(false);
  const { copy } = useCopy();
  const details = [
    `${t("credential.server")}: ${credential.server_url}`,
    `${t("credential.username")}: ${credential.username}`,
    `${t("credential.password")}: ${credential.password}`,
  ].join("\n");

  return (
    <div data-testid="issued-credential" className="grid gap-4">
      <Alert tone="warning">{t("credential.onceWarning")}</Alert>
      <div className="grid items-start gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
        <div className="grid min-w-0 gap-3">
          <CopyField label={t("credential.server")} value={credential.server_url} />
          <CopyField label={t("credential.username")} value={credential.username} />
          <SecretReveal
            label={t("credential.password")}
            secret={credential.password}
            onHidden={() => {
              setHidden(true);
            }}
          />
        </div>
        {hidden ? (
          <div className="flex min-h-40 flex-col items-center justify-center gap-2 self-stretch rounded-card border border-dashed border-border p-4 text-center text-ui text-muted-foreground sm:w-44">
            <EyeOff aria-hidden="true" className="size-5" />
            {t("credential.qrGone")}
          </div>
        ) : (
          <QRCodeCard
            value={credentialQrValue(credential)}
            label={t("credential.qrLabel")}
            description={t("credential.qrHint")}
            size={144}
            className="sm:w-44"
          />
        )}
      </div>
      <Button
        type="button"
        variant="secondary"
        className="justify-self-start"
        disabled={hidden}
        onClick={() => {
          void copy(details);
        }}
      >
        <ClipboardCopy aria-hidden="true" />
        {t("credential.copyAll")}
      </Button>
    </div>
  );
}
