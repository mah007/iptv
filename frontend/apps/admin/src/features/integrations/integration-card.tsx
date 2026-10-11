import {
  isApiError,
  useIntegrationsTest,
  type Integration,
  type IntegrationTestResult,
  type SettingEntry,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  CopyField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  toast,
  type BadgeTone,
} from "@smart-iptv/ui";
import { FlaskConical } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { notifyError } from "../../lib/problems";
import {
  ReplaceButton,
  SecretActions,
  SecretInput,
  SecretStatus,
} from "../settings/secret-control";
import { displayValue, outOfRange, parseDraft, useSettingLabel } from "../settings/setting-row";
import { useSettingActions } from "../settings/use-setting-actions";

type Draft = Record<string, string | boolean>;
type Value = boolean | number | string;

const STATUS_TONES: Record<Integration["status"], BadgeTone> = {
  configured: "success",
  server_config: "info",
  partial: "warning",
  not_set: "neutral",
};

function initialDraft(settings: readonly SettingEntry[]): Draft {
  const draft: Draft = {};
  for (const entry of settings) {
    if (entry.sensitive) continue;
    draft[entry.key] = entry.kind === "bool" ? entry.value === true : displayValue(entry.value);
  }
  return draft;
}

/** What the draft would store: changed plain values, and secrets typed in. */
function changes(
  settings: readonly SettingEntry[],
  draft: Draft,
  secrets: Record<string, string>,
): { values: Record<string, Value>; invalid: Set<string> } {
  const values: Record<string, Value> = {};
  const invalid = new Set<string>();
  for (const entry of settings) {
    if (entry.sensitive) {
      const typed = secrets[entry.key] ?? "";
      if (typed !== "") values[entry.key] = typed;
      continue;
    }
    const current = draft[entry.key];
    if (typeof current === "boolean") {
      if (current !== (entry.value === true)) values[entry.key] = current;
      continue;
    }
    const text = current ?? "";
    if (text === displayValue(entry.value)) continue;
    const parsed = parseDraft(entry, text);
    if (parsed === null || outOfRange(entry, parsed)) invalid.add(entry.key);
    else values[entry.key] = parsed;
  }
  return { values, invalid };
}

/** One integration: status, its settings, Save, then Test (ADR-0019). */
export function IntegrationCard({
  integration,
  canEdit,
}: {
  integration: Integration;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const actions = useSettingActions();
  const test = useIntegrationsTest();
  const [draft, setDraft] = useState<Draft>(() => initialDraft(integration.settings));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<ReadonlySet<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<IntegrationTestResult | null>(null);
  const signature = integration.settings
    .map((entry) => `${entry.key}=${String(entry.value)}:${entry.source}:${String(entry.is_set)}`)
    .join("|");
  const [saved, setSaved] = useState(signature);

  // Newer values from the server (a save here, another admin) replace the draft.
  if (saved !== signature) {
    setSaved(signature);
    setDraft(initialDraft(integration.settings));
  }

  const { values, invalid } = changes(integration.settings, draft, secrets);
  const dirty = Object.keys(values).length > 0 || invalid.size > 0;
  const name = integration.name;
  const title = t(`integrations.cards.${name}.title`);

  function closeSecret(key: string): void {
    setEditing((open) => new Set([...open].filter((item) => item !== key)));
    setSecrets((current) =>
      Object.fromEntries(Object.entries(current).filter(([item]) => item !== key)),
    );
  }

  function save(): void {
    if (invalid.size > 0 || Object.keys(values).length === 0) return;
    setErrors({});
    actions.setMany(values).then(
      () => {
        setSecrets({});
        setEditing(new Set());
        setResult(null);
        toast.success(t("integrations.saved", { name: title }));
      },
      (failure: unknown) => {
        if (isApiError(failure) && failure.code === "VALIDATION_ERROR") {
          const next: Record<string, string> = {};
          for (const [field, messages] of Object.entries(failure.fieldErrors)) {
            const key = field.replace(/^values\./u, "");
            if (messages[0] !== undefined) next[key] = messages[0];
          }
          setErrors(next);
        } else {
          notifyError(t, failure);
        }
      },
    );
  }

  function runTest(): void {
    setResult(null);
    test.mutate(
      { name },
      {
        onSuccess: (outcome) => {
          setResult(outcome);
        },
        onError: (failure) => {
          notifyError(t, failure);
        },
      },
    );
  }

  function immediate(action: Promise<unknown>, message: string): void {
    action.then(
      () => {
        setResult(null);
        toast.success(message);
      },
      (failure: unknown) => {
        notifyError(t, failure);
      },
    );
  }

  return (
    <Card data-integration={name} className="flex flex-col">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{title}</CardTitle>
          <Badge tone={STATUS_TONES[integration.status]} dot>
            {t(`integrations.status.${integration.status}`)}
          </Badge>
        </div>
        <CardDescription>{t(`integrations.cards.${name}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="grid flex-1 content-start gap-4">
        {integration.settings.map((entry) => (
          <Field
            key={entry.key}
            entry={entry}
            canEdit={canEdit}
            pending={actions.pending}
            error={errors[entry.key] ?? (invalid.has(entry.key) ? t("settings.invalid") : null)}
            onAdopt={() => {
              immediate(actions.adopt(entry.key), t("settings.secret.adopted", { name: title }));
            }}
          >
            {(id, describedBy) =>
              entry.sensitive ? (
                <div className="grid gap-2">
                  <SecretStatus entry={entry} id={describedBy} />
                  {editing.has(entry.key) ? (
                    <div className="flex flex-wrap items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <SecretInput
                          id={id}
                          value={secrets[entry.key] ?? ""}
                          describedBy={describedBy}
                          invalid={entry.key in errors}
                          onChange={(value) => {
                            setSecrets((current) => ({ ...current, [entry.key]: value }));
                          }}
                        />
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          closeSecret(entry.key);
                        }}
                      >
                        {t("common.cancel")}
                      </Button>
                    </div>
                  ) : canEdit ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <ReplaceButton
                        entry={entry}
                        disabled={actions.pending}
                        onClick={() => {
                          setEditing((open) => new Set([...open, entry.key]));
                        }}
                      />
                      <SecretActions
                        entry={entry}
                        label={t(`settings.keys.${entry.key}.label`)}
                        canEdit={canEdit}
                        pending={actions.pending}
                        onClear={() => {
                          immediate(
                            actions.reset(entry.key),
                            t("settings.secret.cleared", { name: title }),
                          );
                        }}
                        onAdopt={() => {
                          immediate(
                            actions.adopt(entry.key),
                            t("settings.secret.adopted", { name: title }),
                          );
                        }}
                        onTurnOff={() => {
                          immediate(
                            actions.set(entry.key, ""),
                            t("settings.secret.turnedOff", { name: title }),
                          );
                        }}
                      />
                    </div>
                  ) : null}
                </div>
              ) : entry.kind === "bool" ? (
                <Switch
                  id={id}
                  aria-describedby={describedBy}
                  checked={draft[entry.key] === true}
                  disabled={!canEdit}
                  onCheckedChange={(checked) => {
                    setDraft((current) => ({ ...current, [entry.key]: checked }));
                  }}
                />
              ) : entry.choices && entry.choices.length > 0 ? (
                <Select
                  value={String(draft[entry.key] ?? "")}
                  disabled={!canEdit}
                  onValueChange={(value) => {
                    setDraft((current) => ({ ...current, [entry.key]: value }));
                  }}
                >
                  <SelectTrigger id={id} aria-describedby={describedBy} className="w-full sm:w-64">
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
              ) : (
                <Input
                  id={id}
                  value={String(draft[entry.key] ?? "")}
                  type={entry.kind === "str" ? "text" : "number"}
                  dir="ltr"
                  autoComplete="off"
                  disabled={!canEdit}
                  aria-describedby={describedBy}
                  aria-invalid={entry.key in errors || invalid.has(entry.key) ? true : undefined}
                  {...(entry.min_value === null ? {} : { min: entry.min_value })}
                  {...(entry.max_value === null ? {} : { max: entry.max_value })}
                  className="sm:w-72"
                  onChange={(event) => {
                    const text = event.target.value;
                    setDraft((current) => ({ ...current, [entry.key]: text }));
                  }}
                />
              )
            }
          </Field>
        ))}
        {integration.webhook_url ? (
          <CopyField label={t("integrations.webhookUrl")} value={integration.webhook_url} />
        ) : null}
        {result ? <TestOutcome name={name} result={result} /> : null}
      </CardContent>
      {canEdit ? (
        <CardFooter className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          <Button
            type="button"
            disabled={!dirty || invalid.size > 0}
            pending={actions.pending}
            onClick={save}
          >
            {t("common.save")}
          </Button>
          {dirty ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setDraft(initialDraft(integration.settings));
                setSecrets({});
                setEditing(new Set());
                setErrors({});
              }}
            >
              {t("integrations.discard")}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            className="ms-auto"
            disabled={dirty}
            pending={test.isPending}
            onClick={runTest}
          >
            <FlaskConical aria-hidden="true" />
            {t(`integrations.cards.${name}.test`)}
          </Button>
          {dirty ? (
            <p className="w-full text-xs text-muted-foreground">{t("integrations.saveFirst")}</p>
          ) : null}
        </CardFooter>
      ) : null}
    </Card>
  );
}

/** A labelled field with its description and error, wired for screen readers. */
function Field({
  entry,
  canEdit,
  pending,
  error,
  onAdopt,
  children,
}: {
  entry: SettingEntry;
  canEdit: boolean;
  pending: boolean;
  error: string | null;
  onAdopt: () => void;
  children: (id: string, describedBy: string) => ReactNode;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { label, description, untranslated } = useSettingLabel(entry);
  const descriptionId = `${id}-description`;
  return (
    <div
      className="grid gap-1.5"
      data-setting={entry.key}
      data-untranslated={untranslated ? "" : undefined}
    >
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-ui font-medium">
          {label}
        </label>
        {!entry.sensitive && entry.source === "environment" ? (
          <Badge tone="info">{t("settings.source.environment")}</Badge>
        ) : null}
      </div>
      {entry.sensitive ? null : (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {description}
        </p>
      )}
      {children(id, descriptionId)}
      {!entry.sensitive && entry.source === "environment" && canEdit ? (
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span dir="auto">
            {t("settings.source.fromVariable", { variable: entry.env.join(", ") })}
          </span>
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
        </p>
      ) : null}
      {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
    </div>
  );
}

/** The last Test's outcome, in the admin's language, with the server's own words. */
function TestOutcome({ name, result }: { name: string; result: IntegrationTestResult }) {
  const { t } = useTranslation();
  const details = result.details;
  const facts: string[] = [];
  if (typeof details.recipient === "string") {
    facts.push(t("integrations.facts.recipient", { address: details.recipient }));
  }
  if (typeof details.reference === "string") {
    facts.push(t("integrations.facts.reference", { reference: details.reference }));
  }
  if (typeof details.bot === "string" && details.bot !== "") {
    facts.push(t("integrations.facts.bot", { bot: details.bot }));
  }
  if (details.mode === "test" || details.mode === "live") {
    facts.push(t(`integrations.facts.mode.${details.mode}`));
  }
  if (typeof details.webhook_registered === "string") {
    facts.push(t(`integrations.facts.webhook.${details.webhook_registered}`));
  }
  if (details.webhook_secret_set === false) {
    facts.push(t("integrations.facts.noWebhookSecret"));
  }
  return (
    <Alert
      tone={result.ok ? "success" : "danger"}
      title={
        result.ok ? t(`integrations.cards.${name}.passed`) : t(`integrations.codes.${result.code}`)
      }
      role="status"
      data-test-result={result.code}
    >
      <ul className="grid gap-0.5">
        {facts.map((fact) => (
          <li key={fact}>{fact}</li>
        ))}
        {result.ok ? null : (
          <li dir="ltr" className="text-xs opacity-80">
            {result.message}
          </li>
        )}
      </ul>
    </Alert>
  );
}
