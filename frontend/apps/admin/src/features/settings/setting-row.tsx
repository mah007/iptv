import {
  getSettingsListQueryKey,
  isApiError,
  useSettingsUpdate,
  type SettingEntry,
} from "@smart-iptv/api";
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
import { useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { notifyError } from "../../lib/problems";

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

function displayValue(value: SettingEntry["value"]): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** Parse the typed text for the setting's kind; null when it is not a valid value. */
function parseDraft(entry: SettingEntry, draft: string): Value | null {
  if (entry.kind === "str") return draft;
  const trimmed = draft.trim();
  if (trimmed === "") return null;
  const number = Number(trimmed);
  if (!Number.isFinite(number)) return null;
  if (entry.kind === "int" && !Number.isInteger(number)) return null;
  return number;
}

function outOfRange(entry: SettingEntry, value: Value | null): boolean {
  if (typeof value !== "number") return false;
  return (
    (entry.min_value !== null && value < entry.min_value) ||
    (entry.max_value !== null && value > entry.max_value)
  );
}

/** One setting: label, description, a typed control, its default and who changed it last. */
export function SettingRow({ entry, canEdit }: { entry: SettingEntry; canEdit: boolean }) {
  const { t } = useTranslation();
  const id = useId();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => (entry.sensitive ? "" : displayValue(entry.value)));
  const [savedValue, setSavedValue] = useState(entry.value);
  const [error, setError] = useState<string | null>(null);
  const update = useSettingsUpdate();

  // Adopt a newer server value (another admin, a reset) unless the admin is typing.
  if (entry.value !== savedValue) {
    setSavedValue(entry.value);
    if (!entry.sensitive) setDraft(displayValue(entry.value));
  }

  const label = t(`settings.keys.${entry.key}.label`, { defaultValue: humanize(entry.key) });
  const description = t(`settings.keys.${entry.key}.description`, {
    defaultValue: entry.description,
  });
  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;

  function save(value: Value): void {
    setError(null);
    update.mutate(
      { key: entry.key, data: { value } },
      {
        onSuccess: (saved) => {
          queryClient.setQueryData(getSettingsListQueryKey(), (list: SettingEntry[] | undefined) =>
            list?.map((item) => (item.key === saved.key ? saved : item)),
          );
          if (entry.sensitive) setDraft("");
          toast.success(t("settings.saved", { name: label }));
        },
        onError: (failure) => {
          if (isApiError(failure) && failure.code === "VALIDATION_ERROR") {
            const messages = Object.values(failure.fieldErrors).flat();
            setError(messages[0] ?? failure.detail);
          } else {
            notifyError(t, failure);
          }
        },
      },
    );
  }

  const parsed = parseDraft(entry, draft);
  const dirty = entry.sensitive ? draft !== "" : draft !== displayValue(entry.value);
  const invalid = dirty && (parsed === null || outOfRange(entry, parsed));
  const disabled = !canEdit || update.isPending;

  let control;
  if (entry.kind === "bool") {
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
              {choice}
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
        type={entry.sensitive ? "password" : entry.kind === "str" ? "text" : "number"}
        dir="ltr"
        autoComplete="off"
        {...(entry.kind === "float" ? { step: "any" } : {})}
        {...(entry.min_value === null ? {} : { min: entry.min_value })}
        {...(entry.max_value === null ? {} : { max: entry.max_value })}
        {...(entry.sensitive ? { placeholder: t("settings.newSecret") } : {})}
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
            pending={update.isPending}
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
      className="grid gap-x-8 gap-y-3 border-b border-border py-4 last:border-b-0 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
    >
      <div className="grid content-start gap-1">
        <label
          htmlFor={id}
          className="flex flex-wrap items-center gap-2 text-ui font-medium text-foreground"
        >
          {label}
          {entry.sensitive ? (
            <Badge tone={entry.value === null || entry.value === "" ? "neutral" : "success"}>
              {entry.value === null || entry.value === ""
                ? t("settings.secretNotSet")
                : t("settings.secretSet")}
            </Badge>
          ) : null}
        </label>
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {description}
        </p>
        <code className="w-fit font-mono text-[11px] text-muted-foreground/80" dir="ltr">
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
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {entry.is_default || entry.value === entry.default ? (
            <span>{t("settings.isDefault")}</span>
          ) : (
            <>
              {entry.sensitive ? null : (
                <span className={cn("min-w-0 truncate")}>
                  {t("settings.default", { value: displayValue(entry.default) || "—" })}
                </span>
              )}
              {entry.updated_at ? (
                <span>
                  {entry.updated_by
                    ? t("settings.changedBy", { name: entry.updated_by })
                    : t("settings.changed")}{" "}
                  <RelativeTime value={entry.updated_at} />
                </span>
              ) : null}
              {canEdit && !entry.sensitive && entry.default !== null ? (
                <Button
                  type="button"
                  variant="link"
                  size="xs"
                  className="h-auto px-0 text-xs"
                  disabled={update.isPending}
                  onClick={() => {
                    if (entry.default !== null) save(entry.default);
                  }}
                >
                  <RotateCcw aria-hidden="true" />
                  {t("settings.reset")}
                </Button>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
