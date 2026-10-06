import { zodResolver } from "@hookform/resolvers/zod";
import {
  getLiveChannelsListQueryKey,
  getLiveEpgSourcesListQueryKey,
  getLiveEpgUnmatchedQueryKey,
  useLiveChannelsList,
  useLiveChannelsProgrammes,
  useLiveChannelsUpdate,
  useLiveEpgSourcesCreate,
  useLiveEpgSourcesDelete,
  useLiveEpgSourcesList,
  useLiveEpgSourcesRefresh,
  useLiveEpgSourcesUpdate,
  useLiveEpgUnmatched,
  type EpgSource,
  type EpgSourceWriteRequest,
  type UnmatchedChannel,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Label,
  PageHeader,
  RadioGroup,
  RadioGroupItem,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Skeleton,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
  useNow,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import {
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useId, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { QueryError, RequirePermission } from "../components/states";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact } from "../lib/search";
import { applyFieldErrors, notifyError } from "../lib/problems";

const route = getRouteApi("/app/epg");
const DAY_MS = 86_400_000;

const sourceSchema = z.object({
  name: z.string().trim().min(1, "epg.validation.nameRequired").max(100),
  kind: z.enum(["url", "upload"]),
  url: z.string().trim().max(2048),
  refresh_cron: z.string().trim().min(1, "epg.validation.cronRequired").max(100),
  priority: z
    .string()
    .trim()
    .regex(/^\d{1,5}$/u, "epg.validation.priority")
    .transform(Number)
    .pipe(z.number().min(0, "epg.validation.priority").max(10_000, "epg.validation.priority")),
  enabled: z.boolean(),
});

type SourceInput = z.input<typeof sourceSchema>;
type SourceValues = z.output<typeof sourceSchema>;
const SOURCE_FIELDS = {
  name: "name",
  url: "url",
  refresh_cron: "refresh_cron",
  priority: "priority",
} as const;

function SourceForm({ source, onDone }: { source: EpgSource | null; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useLiveEpgSourcesCreate();
  const update = useLiveEpgSourcesUpdate();
  const fileId = useId();
  const [file, setFile] = useState<File | null>(null);
  const form = useForm<SourceInput, unknown, SourceValues>({
    resolver: zodResolver(sourceSchema),
    defaultValues: {
      name: source?.name ?? "",
      kind: source?.kind ?? "url",
      url: "",
      refresh_cron: source?.refresh_cron ?? "0 */6 * * *",
      priority: String(source?.priority ?? 100),
      enabled: source?.enabled ?? true,
    },
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const submit = form.handleSubmit(async (values) => {
    const data: EpgSourceWriteRequest = {
      name: values.name,
      refresh_cron: values.refresh_cron,
      priority: values.priority,
      enabled: values.enabled,
    };
    if (values.kind === "url" && values.url) data.url = values.url;
    if (values.kind === "upload" && file) data.file = file;
    if (!source && !data.url && !data.file) {
      form.setError("url", { type: "required", message: t("epg.validation.urlOrFile") });
      return;
    }
    try {
      if (source) {
        await update.mutateAsync({ id: source.id, data });
      } else {
        await create.mutateAsync({ data });
      }
      await queryClient.invalidateQueries({ queryKey: getLiveEpgSourcesListQueryKey() });
      toast.success(t("epg.form.saved", { name: values.name }));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, SOURCE_FIELDS, t).length === 0) {
        notifyError(t, error);
      }
    }
  });
  return (
    <Form {...form}>
      <form
        noValidate
        className="flex min-h-0 flex-1 flex-col"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <SheetBody className="grid content-start gap-4">
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("epg.form.name")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="kind"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("epg.form.kind")}</FormLabel>
                <FormControl>
                  <RadioGroup
                    value={field.value}
                    onValueChange={field.onChange}
                    className="flex gap-4"
                  >
                    {(["url", "upload"] as const).map((value) => (
                      <Label key={value} className="flex items-center gap-2 font-normal">
                        <RadioGroupItem value={value} />
                        {t(`epg.kinds.${value}`)}
                      </Label>
                    ))}
                  </RadioGroup>
                </FormControl>
              </FormItem>
            )}
          />
          {kind === "url" ? (
            <FormField
              control={form.control}
              name="url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("epg.form.url")}</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      spellCheck={false}
                      dir="ltr"
                      className="font-mono"
                      placeholder={
                        source?.url
                          ? `${source.url.scheme}://${source.url.host}/…`
                          : "https://guide.example.com/xmltv.xml.gz"
                      }
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    {source ? t("epg.form.urlKeep") : t("epg.form.urlHelp")}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : (
            <div className="grid gap-1.5">
              <label htmlFor={fileId} className="text-sm font-medium">
                {t("epg.form.file")}
              </label>
              <Input
                id={fileId}
                type="file"
                accept=".xml,.gz,application/xml,application/gzip"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                }}
              />
              <p className="text-xs text-muted-foreground">{t("epg.form.fileHelp")}</p>
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="refresh_cron"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("epg.form.cron")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="ltr" className="font-mono" {...field} />
                  </FormControl>
                  <FormDescription>{t("epg.form.cronHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="priority"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("epg.form.priority")}</FormLabel>
                  <FormControl>
                    <Input inputMode="numeric" dir="ltr" className="w-32 tabular-nums" {...field} />
                  </FormControl>
                  <FormDescription>{t("epg.form.priorityHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          <FormField
            control={form.control}
            name="enabled"
            render={({ field }) => (
              <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
                <div className="grid gap-0.5">
                  <FormLabel>{t("epg.form.enabled")}</FormLabel>
                  <FormDescription>{t("epg.form.enabledHelp")}</FormDescription>
                </div>
                <FormControl>
                  <Switch checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
              </FormItem>
            )}
          />
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {source ? t("common.save") : t("epg.form.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

function stat(source: EpgSource, name: string): number {
  const value = (source.stats as Record<string, unknown> | null)?.[name];
  return typeof value === "number" ? value : 0;
}

function Sources({ manage }: { manage: boolean }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const query = useLiveEpgSourcesList(undefined, { query: { refetchInterval: 10_000 } });
  const refresh = useLiveEpgSourcesRefresh();
  const remove = useLiveEpgSourcesDelete();
  const [sheet, setSheet] = useState<{ open: boolean; source: EpgSource | null }>({
    open: false,
    source: null,
  });
  const [deleting, setDeleting] = useState<EpgSource | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const newButton = manage ? (
    <Button
      onClick={() => {
        setSheet({ open: true, source: null });
      }}
    >
      <Plus aria-hidden="true" />
      {t("epg.new")}
    </Button>
  ) : null;
  return (
    <Card className="p-0">
      <CardHeader className="flex flex-row items-center justify-between gap-2 p-4">
        <CardTitle>{t("epg.sources.title")}</CardTitle>
        {newButton}
      </CardHeader>
      {query.isPending ? (
        <div className="grid gap-2 p-4" role="status" aria-live="polite">
          <span className="sr-only">{t("layout.loading")}</span>
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : query.isError ? (
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : query.data.length === 0 ? (
        <EmptyState
          icon={<CalendarClock />}
          title={t("epg.sources.empty")}
          description={t("epg.sources.emptyDescription")}
          action={newButton}
        />
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("epg.sources.columns.name")}</TableHead>
                <TableHead>{t("epg.sources.columns.from")}</TableHead>
                <TableHead>{t("epg.sources.columns.schedule")}</TableHead>
                <TableHead>{t("epg.sources.columns.lastOk")}</TableHead>
                <TableHead>{t("epg.sources.columns.guide")}</TableHead>
                <TableHead>
                  <span className="sr-only">{t("epg.sources.columns.actions")}</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.map((source) => (
                <TableRow key={source.id}>
                  <TableCell>
                    <div className="grid gap-0.5">
                      <span className="font-medium">{source.name}</span>
                      {source.enabled ? null : (
                        <Badge tone="neutral" className="w-fit">
                          {t("epg.sources.disabled")}
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <bdi dir="ltr" className="font-mono text-xs">
                      {source.kind === "url" && source.url
                        ? `${source.url.scheme}://${source.url.host}`
                        : source.upload_name || t("epg.kinds.upload")}
                    </bdi>
                  </TableCell>
                  <TableCell>
                    <bdi dir="ltr" className="font-mono text-xs">
                      {source.refresh_cron}
                    </bdi>
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-0.5">
                      {source.last_ok_at ? (
                        <RelativeTime value={source.last_ok_at} />
                      ) : (
                        <span className="text-muted-foreground">{t("epg.sources.never")}</span>
                      )}
                      {source.last_error ? (
                        <span className="text-xs text-danger-text">{source.last_error}</span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="tabular-nums text-sm">
                    {t("epg.sources.counts", {
                      channels: format.number(source.channel_count),
                      programmes: format.number(stat(source, "programmes")),
                    })}
                  </TableCell>
                  <TableCell>
                    {manage ? (
                      <div className="flex items-center justify-end gap-0.5">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("epg.sources.refresh", { name: source.name })}
                          onClick={() => {
                            refresh.mutate(
                              { id: source.id },
                              {
                                onSuccess: () => {
                                  toast.success(
                                    t("epg.sources.refreshQueued", { name: source.name }),
                                  );
                                },
                                onError: (error) => {
                                  notifyError(t, error);
                                },
                              },
                            );
                          }}
                        >
                          <RefreshCw aria-hidden="true" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("epg.sources.edit", { name: source.name })}
                          onClick={() => {
                            setSheet({ open: true, source });
                          }}
                        >
                          <Pencil aria-hidden="true" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="text-danger-text"
                          aria-label={t("epg.sources.delete", { name: source.name })}
                          onClick={() => {
                            setDeleting(source);
                            setDeleteOpen(true);
                          }}
                        >
                          <Trash2 aria-hidden="true" />
                        </Button>
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <Sheet
        open={sheet.open}
        onOpenChange={(open) => {
          setSheet((previous) => ({ ...previous, open }));
        }}
      >
        <SheetContent
          className="max-w-lg"
          onInteractOutside={(event) => {
            event.preventDefault();
          }}
        >
          <SheetHeader>
            <SheetTitle>
              {sheet.source ? t("epg.form.editTitle") : t("epg.form.newTitle")}
            </SheetTitle>
            <SheetDescription>{t("epg.form.description")}</SheetDescription>
          </SheetHeader>
          {sheet.open ? (
            <SourceForm
              source={sheet.source}
              onDone={() => {
                setSheet((previous) => ({ ...previous, open: false }));
              }}
            />
          ) : null}
        </SheetContent>
      </Sheet>
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        tone="danger"
        title={t("epg.sources.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("epg.sources.deleteDescription")}
        confirmLabel={t("epg.sources.deleteConfirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getLiveEpgSourcesListQueryKey() });
          }
        }}
      />
    </Card>
  );
}

function UnmatchedRow({ item, manage }: { item: UnmatchedChannel; manage: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const update = useLiveChannelsUpdate();
  return (
    <TableRow>
      <TableCell>
        <bdi className="font-medium">{item.name}</bdi>
      </TableCell>
      <TableCell>
        <Badge tone={item.reason === "no_id" ? "neutral" : "warning"}>
          {t(`epg.unmatched.reasons.${item.reason}`)}
        </Badge>
        {item.epg_channel_id ? (
          <bdi dir="ltr" className="ms-2 font-mono text-xs text-muted-foreground">
            {item.epg_channel_id}
          </bdi>
        ) : null}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1.5">
          {item.suggestions.length === 0 ? (
            <span className="text-xs text-muted-foreground">{t("epg.unmatched.noSuggestion")}</span>
          ) : (
            item.suggestions.map((suggestion) => (
              <Button
                key={suggestion.id}
                size="xs"
                variant="secondary"
                disabled={!manage || update.isPending}
                aria-label={t("epg.unmatched.use", { id: suggestion.xmltv_id, name: item.name })}
                onClick={() => {
                  update.mutate(
                    { id: item.id, data: { epg_channel_id: suggestion.xmltv_id } },
                    {
                      onSuccess: () => {
                        toast.success(t("epg.unmatched.mapped", { name: item.name }));
                      },
                      onError: (error) => {
                        notifyError(t, error);
                      },
                      onSettled: () => {
                        void queryClient.invalidateQueries({
                          queryKey: getLiveEpgUnmatchedQueryKey(),
                        });
                        void queryClient.invalidateQueries({
                          queryKey: getLiveChannelsListQueryKey(),
                        });
                      },
                    },
                  );
                }}
              >
                <bdi dir="ltr" className="font-mono">
                  {suggestion.xmltv_id}
                </bdi>
              </Button>
            ))
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function Unmatched({ manage }: { manage: boolean }) {
  const { t } = useTranslation();
  const query = useLiveEpgUnmatched();
  return (
    <Card className="p-0">
      <CardHeader className="p-4">
        <CardTitle>{t("epg.unmatched.title")}</CardTitle>
      </CardHeader>
      {query.isPending ? (
        <div className="p-4" role="status" aria-live="polite">
          <span className="sr-only">{t("layout.loading")}</span>
          <Skeleton className="h-8 w-full" />
        </div>
      ) : query.isError ? (
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <CardContent className="grid gap-4 p-4 pt-0">
          {query.data.channels.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("epg.unmatched.allMatched")}</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("epg.unmatched.columns.channel")}</TableHead>
                    <TableHead>{t("epg.unmatched.columns.reason")}</TableHead>
                    <TableHead>{t("epg.unmatched.columns.suggestions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data.channels.map((item) => (
                    <UnmatchedRow key={item.id} item={item} manage={manage} />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          {query.data.guide_channels.length > 0 ? (
            <div className="grid gap-1.5">
              <p className="text-sm font-medium">{t("epg.unmatched.unused")}</p>
              <div className="flex flex-wrap gap-1.5">
                {query.data.guide_channels.map((item) => (
                  <Badge key={`${item.source_id}:${item.xmltv_id}`} tone="neutral">
                    <bdi dir="ltr" className="font-mono">
                      {item.xmltv_id}
                    </bdi>
                    <span className="ms-1">{item.name}</span>
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
        </CardContent>
      )}
    </Card>
  );
}

function dayBounds(day: string | undefined): { start: Date; label: string } {
  const base = day ? new Date(`${day}T00:00:00Z`) : new Date();
  const start = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate()));
  return { start, label: start.toISOString().slice(0, 10) };
}

function Preview() {
  const { t } = useTranslation();
  const format = useFormatters();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const channels = useLiveChannelsList({ page_size: 100 });
  const withGuide = (channels.data?.results ?? []).filter((channel) => channel.epg_channel_id);
  const channelId = search.channel ?? withGuide[0]?.id ?? "";
  const { start, label } = dayBounds(search.day);
  const end = new Date(start.getTime() + DAY_MS);
  const programmes = useLiveChannelsProgrammes(
    channelId,
    { from: start.toISOString(), to: end.toISOString() },
    { query: { enabled: channelId !== "" } },
  );
  const now = useNow(30_000);

  function goTo(offset: number): void {
    const next = new Date(start.getTime() + offset * DAY_MS).toISOString().slice(0, 10);
    void navigate({ search: (previous) => ({ ...previous, day: next }), replace: true });
  }

  return (
    <Card className="p-0">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 p-4">
        <CardTitle className="me-auto">{t("epg.preview.title")}</CardTitle>
        <Select
          value={channelId}
          onValueChange={(value) => {
            void navigate({
              search: (previous) => ({ ...previous, channel: value }),
              replace: true,
            });
          }}
        >
          <SelectTrigger className="w-56" aria-label={t("epg.preview.channel")}>
            <SelectValue placeholder={t("epg.preview.pick")} />
          </SelectTrigger>
          <SelectContent>
            {withGuide.map((channel) => (
              <SelectItem key={channel.id} value={channel.id}>
                {channel.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("epg.preview.previous")}
            onClick={() => {
              goTo(-1);
            }}
          >
            <ChevronLeft aria-hidden="true" className="rtl:rotate-180" />
          </Button>
          <span className="tabular-nums text-sm" dir="ltr">
            {format.date(start.toISOString())}
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("epg.preview.next")}
            onClick={() => {
              goTo(1);
            }}
          >
            <ChevronRight aria-hidden="true" className="rtl:rotate-180" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              void navigate({
                search: (previous) => compact({ ...previous, day: undefined }),
                replace: true,
              });
            }}
          >
            {t("epg.preview.today")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        {channelId === "" ? (
          <p className="text-sm text-muted-foreground">{t("epg.preview.noChannels")}</p>
        ) : programmes.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : programmes.isError ? (
          <QueryError
            error={programmes.error}
            onRetry={() => {
              void programmes.refetch();
            }}
          />
        ) : programmes.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("epg.preview.empty", { day: label })}</p>
        ) : (
          <ol className="grid gap-1.5" aria-label={t("epg.preview.title")}>
            {programmes.data.map((programme) => {
              const onAir =
                new Date(programme.start).getTime() <= now &&
                now < new Date(programme.stop).getTime();
              return (
                <li
                  key={programme.id}
                  className="grid grid-cols-[auto_1fr] items-start gap-3 rounded-input border border-border px-3 py-2"
                >
                  <span className="tabular-nums text-sm text-muted-foreground" dir="ltr">
                    {format.dateTime(programme.start, { dateStyle: undefined, timeStyle: "short" })}
                  </span>
                  <span className="grid gap-0.5">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <bdi className="font-medium">{programme.title}</bdi>
                      {programme.title_ar ? (
                        <bdi lang="ar" className="text-muted-foreground">
                          {programme.title_ar}
                        </bdi>
                      ) : null}
                      {onAir ? <Badge tone="success">{t("epg.preview.onAir")}</Badge> : null}
                    </span>
                    {programme.description ? (
                      <span className="text-xs text-muted-foreground">{programme.description}</span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function Epg() {
  const { t } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  return (
    <>
      <PageHeader title={t("epg.title")} description={t("epg.description")} />
      <div className="grid gap-4">
        <Sources manage={manage} />
        <Unmatched manage={manage} />
        <Preview />
      </div>
    </>
  );
}

export function EpgPage() {
  const { t } = useTranslation();
  usePageTitle(t("epg.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Epg />
    </RequirePermission>
  );
}
