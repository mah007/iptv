import type { SettingEntry } from "@smart-iptv/api";
import { Badge, Button, ConfirmDialog, PasswordInput, type BadgeTone } from "@smart-iptv/ui";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/** How a secret stands: stored, from `.env`, turned off here, not set, or unreadable. */
export type SecretState = "stored" | "server" | "off" | "unset" | "undecryptable";

export function secretState(entry: SettingEntry): SecretState {
  if (entry.undecryptable) return "undecryptable";
  if (entry.source === "environment") return "server";
  if (entry.source === "database") return entry.is_set ? "stored" : "off";
  return "unset";
}

const TONES: Record<SecretState, BadgeTone> = {
  stored: "success",
  server: "info",
  off: "neutral",
  unset: "neutral",
  undecryptable: "danger",
};

/** The secret's status: a badge, and a line saying where its value comes from. */
export function SecretStatus({ entry, id }: { entry: SettingEntry; id?: string }) {
  const { t } = useTranslation();
  const state = secretState(entry);
  return (
    <div className="grid gap-1">
      <Badge tone={TONES[state]} dot className="w-fit">
        {t(`settings.secret.state.${state}`)}
      </Badge>
      <p id={id} className="text-xs text-muted-foreground">
        {t(`settings.secret.note.${state}`, { variable: entry.env.join(", ") })}
      </p>
    </div>
  );
}

interface SecretActionsProps {
  entry: SettingEntry;
  label: string;
  canEdit: boolean;
  pending: boolean;
  onClear: () => void;
  onAdopt: () => void;
  onTurnOff: () => void;
}

/** Clear (back to `.env` or nothing), move a `.env` value in, or turn a `.env` value off. */
export function SecretActions({
  entry,
  label,
  canEdit,
  pending,
  onClear,
  onAdopt,
  onTurnOff,
}: SecretActionsProps) {
  const { t } = useTranslation();
  if (!canEdit) return null;
  const state = secretState(entry);
  return (
    <>
      {state === "server" ? (
        <>
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={onAdopt}>
            {t("settings.secret.adopt")}
          </Button>
          <ConfirmDialog
            title={t("settings.secret.turnOffTitle", { name: label })}
            description={t("settings.secret.turnOffDescription")}
            confirmLabel={t("settings.secret.turnOff")}
            tone="danger"
            onConfirm={onTurnOff}
            trigger={
              <Button type="button" size="sm" variant="ghost" disabled={pending}>
                {t("settings.secret.turnOff")}
              </Button>
            }
          />
        </>
      ) : null}
      {state === "stored" || state === "off" || state === "undecryptable" ? (
        <ConfirmDialog
          title={t("settings.secret.clearTitle", { name: label })}
          description={
            entry.env.length > 0
              ? t("settings.secret.clearToServer", { variable: entry.env.join(", ") })
              : t("settings.secret.clearDescription")
          }
          confirmLabel={t("settings.secret.clear")}
          tone="danger"
          onConfirm={onClear}
          trigger={
            <Button type="button" size="sm" variant="ghost" disabled={pending}>
              {t("settings.secret.clear")}
            </Button>
          }
        />
      ) : null}
    </>
  );
}

interface SecretInputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
}

/** A write-only input for a new secret value: never prefilled, never echoed back. */
export function SecretInput({
  id,
  value,
  onChange,
  describedBy,
  invalid,
  disabled,
}: SecretInputProps) {
  const { t } = useTranslation();
  return (
    <PasswordInput
      id={id}
      value={value}
      dir="ltr"
      autoComplete="new-password"
      placeholder={t("settings.newSecret")}
      aria-describedby={describedBy}
      aria-invalid={invalid ? true : undefined}
      disabled={disabled}
      className="sm:w-72"
      onChange={(event) => {
        onChange(event.target.value);
      }}
    />
  );
}

/** "Set" or "Replace": reveals the input, which a caller saves on its own terms. */
export function ReplaceButton({
  entry,
  onClick,
  disabled,
}: {
  entry: SettingEntry;
  onClick: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const state = secretState(entry);
  return (
    <Button type="button" size="sm" variant="secondary" disabled={disabled} onClick={onClick}>
      <KeyRound aria-hidden="true" />
      {state === "stored" || state === "server"
        ? t("settings.secret.replace")
        : t("settings.secret.set")}
    </Button>
  );
}

/** Local state of a secret being replaced: the reveal flag and the typed value. */
export function useSecretDraft() {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  return {
    editing,
    value,
    setValue,
    open: () => {
      setEditing(true);
    },
    close: () => {
      setEditing(false);
      setValue("");
    },
  };
}
