import { zodResolver } from "@hookform/resolvers/zod";
import {
  ChannelOutput,
  ChannelTranscode,
  getLiveChannelsListQueryKey,
  useLiveChannelsCreate,
  useLiveChannelsLogo,
  useLiveChannelsTest,
  useLiveChannelsUpdate,
  useLiveSourceTest,
  type Category,
  type LiveChannel,
  type LiveChannelWriteRequest,
} from "@smart-iptv/api";
import {
  Button,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Switch,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { localName } from "../catalog/artwork";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { ProbeResult, useSourceTest } from "./source-test";

const SECONDS_PER_DAY = 86_400;

const channelSchema = z.object({
  name: z.string().trim().min(1, "liveTv.validation.nameRequired").max(255),
  name_ar: z.string().trim().max(255),
  group: z.string().min(1, "liveTv.validation.groupRequired"),
  source_url: z.string().trim().max(2048),
  epg_channel_id: z
    .string()
    .trim()
    .max(255)
    .refine((value) => !/[\s"]/u.test(value), "liveTv.validation.epgId"),
  output: z.enum(Object.values(ChannelOutput) as [ChannelOutput, ...ChannelOutput[]]),
  transcode: z.enum(Object.values(ChannelTranscode) as [ChannelTranscode, ...ChannelTranscode[]]),
  catchup_days: z
    .string()
    .trim()
    .regex(/^\d{1,2}$/u, "liveTv.validation.catchup")
    .transform(Number)
    .pipe(z.number().min(0, "liveTv.validation.catchup").max(30, "liveTv.validation.catchup")),
  always_on: z.boolean(),
  enabled: z.boolean(),
  rights_holder: z.string().trim().max(255),
  license_ref: z.string().trim().max(255),
  license_expires_at: z.string(),
});

type ChannelFormInput = z.input<typeof channelSchema>;
type ChannelFormValues = z.output<typeof channelSchema>;

const FIELD_PATHS = {
  name: "name",
  name_ar: "name_ar",
  group: "group",
  source_url: "source_url",
  epg_channel_id: "epg_channel_id",
  output: "output",
  transcode: "transcode",
  catchup_days: "catchup_days",
  rights_holder: "rights_holder",
  license_ref: "license_ref",
  license_expires_at: "license_expires_at",
} as const;

function defaults(channel: LiveChannel | null, group: string | undefined): ChannelFormInput {
  return {
    name: channel?.name ?? "",
    name_ar: channel?.name_ar ?? "",
    group: channel?.group.id ?? group ?? "",
    source_url: "",
    epg_channel_id: channel?.epg_channel_id ?? "",
    output: channel?.output ?? ChannelOutput.ts,
    transcode: channel?.transcode ?? ChannelTranscode.copy,
    catchup_days: String(channel?.catchup_days ?? 0),
    always_on: channel?.always_on ?? false,
    enabled: channel?.enabled ?? true,
    rights_holder: channel?.rights_holder ?? "",
    license_ref: channel?.license_ref ?? "",
    license_expires_at: channel?.license_expires_at?.slice(0, 10) ?? "",
  };
}

function request(values: ChannelFormValues, creating: boolean): LiveChannelWriteRequest {
  const data: LiveChannelWriteRequest = {
    name: values.name,
    name_ar: values.name_ar,
    group: values.group,
    epg_channel_id: values.epg_channel_id,
    output: values.output,
    transcode: values.transcode,
    catchup_days: values.catchup_days,
    always_on: values.always_on,
    enabled: values.enabled,
    rights_holder: values.rights_holder,
    license_ref: values.license_ref,
    license_expires_at: values.license_expires_at ? `${values.license_expires_at}T23:59:59Z` : null,
  };
  if (values.source_url || creating) data.source_url = values.source_url;
  return data;
}

/** The disk a catch-up window needs at the channel's bitrate (ADR-0017: make the cost visible). */
export function archiveEstimate(bitrateKbps: number, days: number): number {
  return Math.round(((bitrateKbps * 1000) / 8) * SECONDS_PER_DAY * days);
}

function ChannelForm({
  channel,
  groups,
  defaultGroup,
  budgetBytes,
  onDone,
}: {
  channel: LiveChannel | null;
  groups: readonly Category[];
  defaultGroup: string | undefined;
  budgetBytes: number;
  onDone: () => void;
}) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const create = useLiveChannelsCreate();
  const update = useLiveChannelsUpdate();
  const logo = useLiveChannelsLogo();
  const testUrl = useLiveSourceTest();
  const testSaved = useLiveChannelsTest();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const test = useSourceTest(requestId);
  const form = useForm<ChannelFormInput, unknown, ChannelFormValues>({
    resolver: zodResolver(channelSchema),
    defaultValues: defaults(channel, defaultGroup),
  });
  const days = Number(useWatch({ control: form.control, name: "catchup_days" })) || 0;
  const bitrate = Math.max(
    channel?.status.bitrate_kbps ?? 0,
    test.data?.result?.bitrate_kbps ?? 0,
    channel?.probe?.bitrate_kbps ?? 0,
  );

  async function runTest(): Promise<void> {
    const url = form.getValues("source_url").trim();
    try {
      if (url) {
        const queued = await testUrl.mutateAsync({ data: { url } });
        setRequestId(queued.request_id);
      } else if (channel) {
        const queued = await testSaved.mutateAsync({ id: channel.id });
        setRequestId(queued.request_id);
      }
    } catch (error) {
      if (applyFieldErrors(error, form.setError, { url: "source_url" }, t).length === 0) {
        notifyError(t, error);
      }
    }
  }

  const submit = form.handleSubmit(async (values) => {
    if (!channel && !values.source_url) {
      form.setError("source_url", {
        type: "required",
        message: t("liveTv.validation.sourceRequired"),
      });
      return;
    }
    try {
      const data = request(values, channel === null);
      const saved = channel
        ? await update.mutateAsync({ id: channel.id, data })
        : await create.mutateAsync({ data });
      if (logoFile) {
        await logo.mutateAsync({ id: saved.id, data: { file: logoFile } });
      }
      await queryClient.invalidateQueries({ queryKey: getLiveChannelsListQueryKey() });
      toast.success(
        channel
          ? t("liveTv.form.saved", { name: values.name })
          : t("liveTv.form.created", { name: values.name }),
      );
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, FIELD_PATHS, t).length === 0) {
        notifyError(t, error);
      }
    }
  });

  const testing =
    testUrl.isPending ||
    testSaved.isPending ||
    (requestId !== null && test.data?.status !== "done");

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
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.name")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="auto" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="name_ar"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.nameAr")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="rtl" lang="ar" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          <FormField
            control={form.control}
            name="group"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.group")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue placeholder={t("liveTv.form.groupPlaceholder")} />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {groups.map((group) => (
                      <SelectItem key={group.id} value={group.id}>
                        {localName(group, i18n.language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{t("liveTv.form.groupHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="source_url"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.source")}</FormLabel>
                <div className="flex gap-2">
                  <FormControl>
                    <Input
                      autoComplete="off"
                      autoCapitalize="off"
                      spellCheck={false}
                      dir="ltr"
                      className="font-mono"
                      placeholder={
                        channel?.source_info
                          ? `${channel.source_info.scheme}://${channel.source_info.host}/…`
                          : "rtsp://encoder.local:8554/studio"
                      }
                      {...field}
                    />
                  </FormControl>
                  <Button
                    type="button"
                    variant="secondary"
                    pending={testing}
                    disabled={!channel && !field.value}
                    onClick={() => {
                      void runTest();
                    }}
                  >
                    <FlaskConical aria-hidden="true" />
                    {t("liveTv.test.run")}
                  </Button>
                </div>
                <FormDescription>
                  {channel ? t("liveTv.form.sourceKeep") : t("liveTv.form.sourceHelp")}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          {test.data?.result ? <ProbeResult probe={test.data.result} /> : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="output"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.output")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {Object.values(ChannelOutput).map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`liveTv.outputs.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{t("liveTv.form.outputHelp")}</FormDescription>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="transcode"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.transcode")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {Object.values(ChannelTranscode).map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`liveTv.transcodes.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{t("liveTv.form.transcodeHelp")}</FormDescription>
                </FormItem>
              )}
            />
          </div>
          <FormField
            control={form.control}
            name="epg_channel_id"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.epgId")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="off"
                    spellCheck={false}
                    dir="ltr"
                    className="font-mono"
                    {...field}
                  />
                </FormControl>
                <FormDescription>{t("liveTv.form.epgIdHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="catchup_days"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.catchup")}</FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="off"
                    dir="ltr"
                    className="w-32 tabular-nums"
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  {days > 0
                    ? bitrate > 0
                      ? t("liveTv.form.catchupEstimate", {
                          size: format.bytes(archiveEstimate(bitrate, days)),
                          budget: format.bytes(budgetBytes),
                        })
                      : t("liveTv.form.catchupUnknown", { budget: format.bytes(budgetBytes) })
                    : t("liveTv.form.catchupHelp")}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          {(["always_on", "enabled"] as const).map((name) => (
            <FormField
              key={name}
              control={form.control}
              name={name}
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
                  <div className="grid gap-0.5">
                    <FormLabel>{t(`liveTv.form.${name}`)}</FormLabel>
                    <FormDescription>{t(`liveTv.form.${name}Help`)}</FormDescription>
                  </div>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
          ))}
          <Separator />
          <p className="text-sm font-medium">{t("liveTv.form.rights")}</p>
          <p className="-mt-2 text-xs text-muted-foreground">{t("liveTv.form.rightsHelp")}</p>
          <FormField
            control={form.control}
            name="rights_holder"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.rightsHolder")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="license_ref"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.licenseRef")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="auto" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="license_expires_at"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.form.licenseExpires")}</FormLabel>
                  <FormControl>
                    <Input type="date" dir="ltr" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          <div className="grid gap-1.5">
            <label htmlFor="channel-logo" className="text-sm font-medium">
              {t("liveTv.form.logo")}
            </label>
            <Input
              id="channel-logo"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => {
                setLogoFile(event.target.files?.[0] ?? null);
              }}
            />
            <p className="text-xs text-muted-foreground">{t("liveTv.form.logoHelp")}</p>
          </div>
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {channel ? t("common.save") : t("liveTv.form.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Add a live channel, or edit one. */
export function ChannelSheet({
  channel,
  groups,
  defaultGroup,
  budgetBytes,
  open,
  onOpenChange,
}: {
  channel: LiveChannel | null;
  groups: readonly Category[];
  defaultGroup: string | undefined;
  budgetBytes: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>
            {channel ? t("liveTv.form.editTitle") : t("liveTv.form.newTitle")}
          </SheetTitle>
          <SheetDescription>{t("liveTv.form.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <ChannelForm
            channel={channel}
            groups={groups}
            defaultGroup={defaultGroup}
            budgetBytes={budgetBytes}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
