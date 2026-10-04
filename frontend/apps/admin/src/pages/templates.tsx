import {
  getTemplatesListQueryKey,
  useTemplatesList,
  useTemplatesPreview,
  useTemplatesReset,
  useTemplatesTest,
  useTemplatesUpdate,
  type Preview,
  type Template,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  PageHeader,
  Skeleton,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  cn,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Braces, Mail, RotateCcw, Send } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { useCan } from "../lib/auth";
import { notifyError, translatedFieldErrors } from "../lib/problems";
import { usePageTitle } from "../lib/page-title";
import { eventLabel } from "../features/billing/labels";

const route = getRouteApi("/app/templates");
const LOCALES = ["en", "ar"] as const;
type Locale = (typeof LOCALES)[number];
const PREVIEW_DELAY_MS = 400;

interface Draft {
  subject: string;
  body_text: string;
  body_html: string;
  enabled: boolean;
}

function draftOf(template: Template): Draft {
  return {
    subject: template.subject,
    body_text: template.body_text,
    body_html: template.body_html,
    enabled: template.enabled,
  };
}

/** Render the draft with the event's sample values as the admin types (debounced). */
function useLivePreview(key: string, locale: Locale, draft: Draft): Preview | null {
  const preview = useTemplatesPreview();
  const [result, setResult] = useState<Preview | null>(null);
  const mutate = useRef(preview.mutate);
  useEffect(() => {
    mutate.current = preview.mutate;
  });
  useEffect(() => {
    if (draft.subject.trim() === "" || draft.body_text.trim() === "") return undefined;
    const timer = setTimeout(() => {
      mutate.current(
        {
          data: {
            key,
            locale,
            subject: draft.subject,
            body_text: draft.body_text,
            body_html: draft.body_html,
          },
        },
        {
          onSuccess: setResult,
        },
      );
    }, PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [key, locale, draft.subject, draft.body_text, draft.body_html]);
  return result;
}

function TemplateEditor({ template }: { template: Template }) {
  const { t } = useTranslation();
  const can = useCan();
  const editable = can("notifications.manage");
  const queryClient = useQueryClient();
  const ids = { subject: useId(), text: useId(), html: useId(), enabled: useId() };
  const textArea = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState<Draft>(() => draftOf(template));
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const update = useTemplatesUpdate();
  const reset = useTemplatesReset();
  const test = useTemplatesTest();
  const preview = useLivePreview(template.key, template.locale, draft);
  const dirty =
    draft.subject !== template.subject ||
    draft.body_text !== template.body_text ||
    draft.body_html !== template.body_html ||
    draft.enabled !== template.enabled;
  const path = { key: template.key, channel: template.channel, locale: template.locale };
  const rtl = template.locale === "ar";

  function insertVariable(name: string): void {
    const tag = `{{ ${name} }}`;
    const area = textArea.current;
    if (area === null) {
      setDraft((current) => ({ ...current, body_text: `${current.body_text}${tag}` }));
      return;
    }
    const start = area.selectionStart;
    const end = area.selectionEnd;
    const next = `${draft.body_text.slice(0, start)}${tag}${draft.body_text.slice(end)}`;
    setDraft((current) => ({ ...current, body_text: next }));
    requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(start + tag.length, start + tag.length);
    });
  }

  async function save(): Promise<void> {
    setError(null);
    try {
      const saved = await update.mutateAsync({ ...path, data: draft });
      queryClient.setQueryData(getTemplatesListQueryKey(), (list: Template[] | undefined) =>
        list?.map((item) =>
          item.key === saved.key && item.locale === saved.locale ? saved : item,
        ),
      );
      toast.success(t("templates.saved"));
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    }
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <form
        noValidate
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Label
          htmlFor={ids.enabled}
          className="flex items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5 font-normal"
        >
          <span className="grid gap-0.5">
            <span className="font-medium text-foreground">{t("templates.enabled")}</span>
            <span className="text-xs text-muted-foreground">{t("templates.enabledHelp")}</span>
          </span>
          <Switch
            id={ids.enabled}
            checked={draft.enabled}
            disabled={!editable}
            onCheckedChange={(enabled) => {
              setDraft((current) => ({ ...current, enabled }));
            }}
          />
        </Label>
        <div className="grid gap-1.5">
          <Label htmlFor={ids.subject}>{t("templates.subject")}</Label>
          <Input
            id={ids.subject}
            dir={rtl ? "rtl" : "ltr"}
            maxLength={200}
            readOnly={!editable}
            value={draft.subject}
            onChange={(event) => {
              setDraft((current) => ({ ...current, subject: event.target.value }));
            }}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={ids.text}>{t("templates.bodyText")}</Label>
          <Textarea
            ref={textArea}
            id={ids.text}
            dir={rtl ? "rtl" : "ltr"}
            rows={10}
            maxLength={20000}
            readOnly={!editable}
            className="font-mono text-xs"
            value={draft.body_text}
            onChange={(event) => {
              setDraft((current) => ({ ...current, body_text: event.target.value }));
            }}
          />
        </div>
        <details className="rounded-input border border-border">
          <summary className="cursor-pointer px-3 py-2 text-ui font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {t("templates.bodyHtml")}
          </summary>
          <div className="grid gap-1.5 border-t border-border p-3">
            <Label htmlFor={ids.html} className="sr-only">
              {t("templates.bodyHtml")}
            </Label>
            <Textarea
              id={ids.html}
              dir="ltr"
              rows={10}
              maxLength={20000}
              readOnly={!editable}
              className="font-mono text-xs"
              value={draft.body_html}
              onChange={(event) => {
                setDraft((current) => ({ ...current, body_html: event.target.value }));
              }}
            />
            <p className="text-xs text-muted-foreground">{t("templates.bodyHtmlHelp")}</p>
          </div>
        </details>
        {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
        {editable ? (
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!dirty} pending={update.isPending}>
              {t("common.save")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!dirty}
              onClick={() => {
                setDraft(draftOf(template));
                setError(null);
              }}
            >
              {t("templates.discard")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              pending={test.isPending}
              disabled={dirty}
              title={dirty ? t("templates.saveFirst") : undefined}
              onClick={() => {
                test.mutate(path, {
                  onSuccess: () => {
                    toast.success(t("templates.testSent"));
                  },
                  onError: (failure) => {
                    notifyError(t, failure);
                  },
                });
              }}
            >
              <Send aria-hidden="true" className="rtl:-scale-x-100" />
              {t("templates.test")}
            </Button>
            {template.is_default ? null : (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setResetting(true);
                }}
              >
                <RotateCcw aria-hidden="true" />
                {t("templates.reset")}
              </Button>
            )}
          </div>
        ) : null}
      </form>
      <div className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Braces aria-hidden="true" className="size-4 text-muted-foreground" />
              {t("templates.variables")}
            </CardTitle>
            <CardDescription>
              {editable ? t("templates.variablesHelp") : t("templates.variablesReadOnly")}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5">
            {template.variables.map((name) => (
              <Button
                key={name}
                type="button"
                variant="secondary"
                size="xs"
                className="font-mono"
                dir="ltr"
                disabled={!editable}
                onClick={() => {
                  insertVariable(name);
                }}
              >
                {name}
              </Button>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("templates.preview")}</CardTitle>
            <CardDescription>{t("templates.previewHelp")}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {preview === null ? (
              <Skeleton className="h-40 w-full" />
            ) : (
              <>
                <p className="text-ui font-medium text-foreground" dir={rtl ? "rtl" : "ltr"}>
                  {preview.subject}
                </p>
                {preview.html ? (
                  <iframe
                    title={t("templates.preview")}
                    sandbox=""
                    srcDoc={preview.html}
                    className="h-80 w-full rounded-input border border-border bg-white"
                  />
                ) : (
                  <pre
                    className="max-h-80 overflow-auto whitespace-pre-wrap rounded-input bg-muted p-3 text-xs"
                    dir={rtl ? "rtl" : "ltr"}
                  >
                    {preview.text}
                  </pre>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        tone="danger"
        title={t("templates.resetTitle")}
        description={t("templates.resetDescription")}
        confirmLabel={t("templates.reset")}
        onConfirm={async () => {
          try {
            const restored = await reset.mutateAsync(path);
            setDraft(draftOf(restored));
            await queryClient.invalidateQueries({ queryKey: getTemplatesListQueryKey() });
            toast.success(t("templates.restored"));
          } catch (failure) {
            notifyError(t, failure);
            throw failure;
          }
        }}
      />
    </div>
  );
}

function Templates() {
  const { t } = useTranslation();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const query = useTemplatesList();
  const templates = query.data ?? [];
  const keys = [...new Set(templates.map((template) => template.key))];
  const key = search.key && keys.includes(search.key) ? search.key : keys[0];
  const locale: Locale = search.locale ?? "en";
  const current = templates.find((template) => template.key === key && template.locale === locale);
  const description = templates.find((template) => template.key === key)?.description ?? "";

  return (
    <>
      <PageHeader title={t("templates.title")} description={t("templates.description")} />
      {query.isPending ? (
        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]" role="status">
          <span className="sr-only">{t("layout.loading")}</span>
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : query.isError ? (
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : keys.length === 0 || key === undefined ? (
        <EmptyState icon={<Mail />} title={t("templates.empty")} />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <nav aria-label={t("templates.events.label")}>
            <ul className="grid gap-1">
              {keys.map((item) => {
                const changed = templates.some(
                  (template) => template.key === item && !template.is_default,
                );
                return (
                  <li key={item}>
                    <button
                      type="button"
                      aria-current={item === key ? "true" : undefined}
                      onClick={() => {
                        void navigate({
                          search: (previous) => ({ ...previous, key: item }),
                          replace: true,
                        });
                      }}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-input px-3 py-2 text-start text-ui outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                        item === key && "bg-primary/10 font-medium text-primary",
                      )}
                    >
                      <span className="truncate">{eventLabel(t, item)}</span>
                      {changed ? <Badge tone="info">{t("templates.custom")}</Badge> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
          <Card>
            <CardHeader>
              <CardTitle>{eventLabel(t, key)}</CardTitle>
              <CardDescription>
                {t(`templates.eventHelp.${key}`, { defaultValue: description })}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs
                value={locale}
                onValueChange={(value) => {
                  void navigate({
                    search: (previous) => ({ ...previous, locale: value === "ar" ? "ar" : "en" }),
                    replace: true,
                  });
                }}
              >
                <TabsList className="mb-4">
                  {LOCALES.map((item) => {
                    const template = templates.find(
                      (candidate) => candidate.key === key && candidate.locale === item,
                    );
                    return (
                      <TabsTrigger key={item} value={item}>
                        {t(`customers.locales.${item}`)}
                        {template && !template.enabled ? <Badge>{t("templates.off")}</Badge> : null}
                      </TabsTrigger>
                    );
                  })}
                </TabsList>
                {LOCALES.map((item) => (
                  <TabsContent key={item} value={item}>
                    {current && item === locale ? (
                      <TemplateEditor
                        key={`${current.key}-${current.locale}-${current.updated_at ?? "default"}`}
                        template={current}
                      />
                    ) : null}
                  </TabsContent>
                ))}
              </Tabs>
              {current?.secret ? (
                <p className="mt-4 text-xs text-muted-foreground">{t("templates.secretHelp")}</p>
              ) : null}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}

export function TemplatesPage() {
  const { t } = useTranslation();
  usePageTitle(t("templates.title"));
  return (
    <RequirePermission permission="notifications.view">
      <Templates />
    </RequirePermission>
  );
}
