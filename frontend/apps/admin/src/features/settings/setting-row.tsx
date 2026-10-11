import { isApiError, type SettingEntry } from "@smart-iptv/api";
import {
  Badge,
  Button,
  Input,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
  cn,
  toast,
} from "@smart-iptv/ui";
import { RotateCcw } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { notifyError, translatedFieldErrors } from "../../lib/problems";
import { LogoControl } from "./logo-control";
import {
  ReplaceButton,
  SecretActions,
  SecretInput,
  SecretStatus,
  useSecretDraft,
} from "./secret-control";
import { useSettingActions } from "./use-setting-actions";

type Value = boolean | number | string;

/** "service_name_en" → "Service name en": a fallback label for keys without a translation. */
function humanize(key: string): string {
  const last = key.split(".").pop() ?? key;
  const words = last.replace(/_/gu, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Long or structured values (e.g. a JSON map) get a multi-line monospace editor. */
function isLong(entry: SettingEntry): boolean {
  const sample = String(entry.value ?? entry.default ?? "");
  return sample.length > 60 || sample.startsWith("{") || sample.startsWith("[");
}

export function displayValue(value: SettingEntry["value"]): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** Parse the typed text for the setting's kind; null when it is not a valid value. */
export function parseDraft(entry: SettingEntry, draft: string): Value | null {
  if (entry.kind === "str") return draft;
  const trimmed = draft.trim();
  if (trimmed === "") return null;
  const number = Number(trimmed);
  if (!Number.isFinite(number)) return null;
  if (entry.kind === "int" && !Number.isInteger(number)) return null;
  return number;
}

export function outOfRange(entry: SettingEntry, value: Value | null): boolean {
  if (typeof value !== "number") return false;
  return (
    (entry.min_value !== null && value < entry.min_value) ||
    (entry.max_value !== null && value > entry.max_value)
  );
}

/** The translated label of a setting, and whether a translation exists. */
export function useSettingLabel(entry: SettingEntry): {
  label: string;
  description: string;
  untranslated: boolean;
} {
  const { t, i18n } = useTranslation();
  return {
    untranslated: !i18n.exists(`settings.keys.${entry.key}.label`),
    label: t(`settings.keys.${entry.key}.label`, { defaultValue: humanize(entry.key) }),
    description: t(`settings.keys.${entry.key}.description`, { defaultValue: entry.description }),
  };
}

/** One setting: label, description, a typed control, where its value comes from. */
export function SettingRow({ entry, canEdit }: { entry: SettingEntry; canEdit: boolean }) {
  const { t } = useTranslation();
  const id = useId();
  const actions = useSettingActions();
  const secret = useSecretDraft();
  const [draft, setDraft] = useState(() => (entry.sensitive ? "" : displayValue(entry.value)));
  const [savedValue, setSavedValue] = useState(entry.value);
  const [error, setError] = useState<string | null>(null);
  const { label, description, untranslated } = useSettingLabel(entry);

  // Adopt a newer server value (another admin, a reset) unless the admin is typing.
  if (entry.value !== savedValue) {
    setSavedValue(entry.value);
    if (!entry.sensitive) setDraft(displayValue(entry.value));
  }

  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;

  function failed(failure: unknown): void {
    if (isApiError(failure) && failure.code === "VALIDATION_ERROR") {
      setError(translatedFieldErrors(t, failure)[0] ?? failure.detail);
    } else {
      notifyError(t, failure);
    }
  }

  function save(value: Value): void {
    setError(null);
    actions.set(entry.key, value).then(() => {
      secret.close();
      toast.success(t("settings.saved", { name: label }));
    }, failed);
  }

  function run(action: Promise<unknown>, message: string): void {
    setError(null);
    action.then(() => toast.success(message), failed);
  }

  const parsed = parseDraft(entry, draft);
  const dirty = draft !== displayValue(entry.value);
  const invalid = dirty && (parsed === null || outOfRange(entry, parsed));
  const disabled = !canEdit || actions.pending;

  let control;
  if (entry.sensitive) {
    control = (
      <div className="grid gap-2">
        <SecretStatus entry={entry} id={descriptionId} />
        {secret.editing ? (
          <form
            noValidate
            className="flex flex-wrap items-start gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (secret.value !== "") save(secret.value);
            }}
          >
            <div className="min-w-0 flex-1">
              <SecretInput
                id={id}
                value={secret.value}
                describedBy={error ? `${descriptionId} ${errorId}` : descriptionId}
                invalid={error !== null}
                onChange={(value) => {
                  secret.setValue(value);
                  setError(null);
                }}
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={secret.value === ""}
              pending={actions.pending}
            >
              {t("common.save")}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={secret.close}>
              {t("common.cancel")}
            </Button>
          </form>
        ) : canEdit ? (
          <div className="flex flex-wrap items-center gap-2">
            <ReplaceButton entry={entry} disabled={disabled} onClick={secret.open} />
            <SecretActions
              entry={entry}
              label={label}
              canEdit={canEdit}
              pending={actions.pending}
              onClear={() => {
                run(actions.reset(entry.key), t("settings.secret.cleared", { name: label }));
              }}
              onAdopt={() => {
                run(actions.adopt(entry.key), t("settings.secret.adopted", { name: label }));
              }}
              onTurnOff={() => {
                run(actions.set(entry.key, ""), t("settings.secret.turnedOff", { name: label }));
              }}
            />
          </div>
        ) : null}
      </div>
    );
  } else if (entry.widget === "image") {
    control = (
      <LogoControl entry={entry} label={label} canEdit={canEdit} describedBy={descriptionId} />
    );
  } else if (entry.kind === "bool") {
    control = (
      <Switch
        id={id}
        aria-describedby={descriptionId}
        checked={entry.value === true}
        disabled={disabled}
        onCheckedChange={(checked) => {
          save(checked);
        }}
      />
    );
  } else if (entry.choices && entry.choices.length > 0) {
    control = (
      <Select
        value={displayValue(entry.value)}
        disabled={disabled}
        onValueChange={(value) => {
          save(value);
        }}
      >
        <SelectTrigger id={id} aria-describedby={descriptionId} className="w-full sm:w-64">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {entry.choices.map((choice) => (
            <SelectItem key={choice} value={choice}>
              {t(`settings.choices.${choice}`, { defaultValue: choice })}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else {
    const common = {
      id,
      value: draft,
      disabled: !canEdit,
      "aria-describedby": error ? `${descriptionId} ${errorId}` : descriptionId,
      "aria-invalid": invalid || error !== null ? true : undefined,
      onChange: (event: { target: { value: string } }) => {
        setDraft(event.target.value);
        setError(null);
      },
    };
    const field = isLong(entry) ? (
      <Textarea {...common} rows={4} dir="ltr" spellCheck={false} className="font-mono text-xs" />
    ) : (
      <Input
        {...common}
        type={entry.kind === "str" ? "text" : "number"}
        dir="ltr"
        autoComplete="off"
        {...(entry.kind === "float" ? { step: "any" } : {})}
        {...(entry.min_value === null ? {} : { min: entry.min_value })}
        {...(entry.max_value === null ? {} : { max: entry.max_value })}
        className="sm:w-64"
      />
    );
    control = (
      <form
        noValidate
        className="flex flex-wrap items-start gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (parsed !== null && dirty && !invalid) save(parsed);
        }}
      >
        <div className="min-w-0 flex-1">{field}</div>
        {canEdit && dirty ? (
          <Button
            type="submit"
            size="sm"
            className="mt-0.5"
            disabled={invalid}
            pending={actions.pending}
          >
            {t("common.save")}
          </Button>
        ) : null}
      </form>
    );
  }

  const isColor = entry.key.endsWith("_color") && /^#[0-9a-f]{6}$/iu.test(String(entry.value));

  return (
    <div
      data-setting={entry.key}
      data-untranslated={untranslated ? "" : undefined}
      className="grid gap-x-8 gap-y-3 border-b border-border py-4 last:border-b-0 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
    >
      <div className="grid content-start gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={id} className="text-ui font-medium text-foreground">
            {label}
          </label>
          {!entry.sensitive && entry.source === "environment" ? (
            <Badge tone="info">{t("settings.source.environment")}</Badge>
          ) : null}
        </div>
        <p
          id={entry.sensitive ? undefined : descriptionId}
          className="text-xs text-muted-foreground"
        >
          {description}
        </p>
        <code className="w-fit font-mono text-[11px] text-muted-foreground" dir="ltr">
          {entry.key}
        </code>
      </div>
      <div className="grid content-start gap-1.5">
        <div className="flex items-center gap-2">
          {isColor ? (
            <span
              aria-hidden="true"
              className="size-6 shrink-0 rounded-badge border border-border"
              style={{ backgroundColor: String(entry.value) }}
            />
          ) : null}
          <div className="min-w-0 flex-1">{control}</div>
        </div>
        {invalid ? (
          <p className="text-xs font-medium text-danger-text">
            {parsed !== null && entry.min_value !== null && entry.max_value !== null
              ? t("settings.range", { min: entry.min_value, max: entry.max_value })
              : t("settings.invalid")}
          </p>
        ) : null}
        {error ? (
          <p id={errorId} className="text-xs font-medium text-danger-text">
            {error}
          </p>
        ) : null}
        <SettingFooter
          entry={entry}
          canEdit={canEdit}
          pending={actions.pending}
          onReset={() => {
            run(actions.reset(entry.key), t("settings.resetDone", { name: label }));
          }}
          onAdopt={() => {
            run(actions.adopt(entry.key), t("settings.secret.adopted", { name: label }));
          }}
        />
      </div>
    </div>
  );
}

/** Where the value comes from and who changed it; reset and move-in actions. */
function SettingFooter({
  entry,
  canEdit,
  pending,
  onReset,
  onAdopt,
}: {
  entry: SettingEntry;
  canEdit: boolean;
  pending: boolean;
  onReset: () => void;
  onAdopt: () => void;
}) {
  const { t } = useTranslation();
  if (entry.sensitive) {
    return entry.updated_at && entry.source === "database" ? (
      <p className="text-xs text-muted-foreground">
        {entry.updated_by
          ? t("settings.changedBy", { name: entry.updated_by })
          : t("settings.changed")}{" "}
        <RelativeTime value={entry.updated_at} />
      </p>
    ) : null;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {entry.source === "default" ? <span>{t("settings.isDefault")}</span> : null}
      {entry.source === "environment" ? (
        <>
          <span dir="auto">
            {t("settings.source.fromVariable", { variable: entry.env.join(", ") })}
          </span>
          {canEdit && entry.widget === null ? (
            <Button
              type="button"
              variant="link"
              size="xs"
              className="h-auto px-0 text-xs"
              disabled={pending}
              onClick={onAdopt}
            >
              {t("settings.secret.adopt")}
            </Button>
          ) : null}
        </>
      ) : null}
      {entry.source === "database" ? (
        <>
          {entry.widget === null ? (
            <span className={cn("min-w-0 truncate")}>
              {t("settings.default", { value: displayValue(entry.default) || "—" })}
            </span>
          ) : null}
          {entry.updated_at ? (
            <span>
              {entry.updated_by
                ? t("settings.changedBy", { name: entry.updated_by })
                : t("settings.changed")}{" "}
              <RelativeTime value={entry.updated_at} />
            </span>
          ) : null}
          {canEdit && entry.widget === null ? (
            <Button
              type="button"
              variant="link"
              size="xs"
              className="h-auto px-0 text-xs"
              disabled={pending}
              onClick={onReset}
            >
              <RotateCcw aria-hidden="true" />
              {entry.env.length > 0 ? t("settings.resetToServer") : t("settings.reset")}
            </Button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
