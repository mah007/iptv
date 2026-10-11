import { zodResolver } from "@hookform/resolvers/zod";
import {
  getLiveIntegrationsListQueryKey,
  LiveIntegrationKind,
  useLiveIntegrationsCreate,
  useLiveIntegrationsUpdate,
  type LiveIntegration,
  type LiveIntegrationWriteRequest,
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
  PasswordInput,
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
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { applyFieldErrors, notifyError } from "../../lib/problems";

const schema = z.object({
  kind: z.enum(
    Object.values(LiveIntegrationKind) as [LiveIntegrationKind, ...LiveIntegrationKind[]],
  ),
  name: z.string().trim().min(1, "liveTv.validation.nameRequired").max(100),
  base_url: z.string().trim().min(1, "liveTv.validation.urlRequired").max(500),
  stream_base_url: z.string().trim().max(500),
  rights_holder: z.string().trim().min(1, "liveTv.validation.rightsRequired").max(255),
  license_ref: z.string().trim().max(255),
  username: z.string().max(200),
  password: z.string().max(500),
  access_token: z.string().max(2000),
});

type Input = z.input<typeof schema>;
type Values = z.output<typeof schema>;

const FIELD_PATHS = {
  kind: "kind",
  name: "name",
  base_url: "base_url",
  stream_base_url: "stream_base_url",
  rights_holder: "rights_holder",
  license_ref: "license_ref",
} as const;

function IntegrationForm({
  integration,
  onDone,
}: {
  integration: LiveIntegration | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useLiveIntegrationsCreate();
  const update = useLiveIntegrationsUpdate();
  const form = useForm<Input, unknown, Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      kind: integration?.kind ?? LiveIntegrationKind.ersatztv,
      name: integration?.name ?? "",
      base_url: integration?.base_url ?? "",
      stream_base_url: "",
      rights_holder: integration?.rights_holder ?? "",
      license_ref: integration?.license_ref ?? "",
      username: "",
      password: "",
      access_token: "",
    },
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const submit = form.handleSubmit(async (values) => {
    const data: LiveIntegrationWriteRequest = {
      name: values.name,
      base_url: values.base_url,
      rights_holder: values.rights_holder,
      license_ref: values.license_ref,
    };
    if (!integration) data.kind = values.kind;
    if (values.stream_base_url || !integration) data.stream_base_url = values.stream_base_url;
    for (const name of ["username", "password", "access_token"] as const) {
      if (values[name]) data[name] = values[name];
    }
    try {
      if (integration) {
        await update.mutateAsync({ id: integration.id, data });
      } else {
        await create.mutateAsync({ data });
      }
      await queryClient.invalidateQueries({ queryKey: getLiveIntegrationsListQueryKey() });
      toast.success(t("liveTv.integrations.saved", { name: values.name }));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, FIELD_PATHS, t).length === 0) {
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
            name="kind"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.integrations.kind")}</FormLabel>
                <Select
                  value={field.value}
                  onValueChange={field.onChange}
                  disabled={integration !== null}
                >
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {Object.values(LiveIntegrationKind).map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`liveTv.integrations.kinds.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{t(`liveTv.integrations.kindHelp.${kind}`)}</FormDescription>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.integrations.name")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="base_url"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.integrations.baseUrl")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="off"
                    spellCheck={false}
                    dir="ltr"
                    className="font-mono"
                    placeholder={
                      kind === "ersatztv" ? "http://ersatztv:8409" : "http://mediamtx:9997"
                    }
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {kind === "mediamtx" ? (
            <FormField
              control={form.control}
              name="stream_base_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.integrations.streamBase")}</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      spellCheck={false}
                      dir="ltr"
                      className="font-mono"
                      placeholder={
                        integration?.stream_base
                          ? `${integration.stream_base.scheme}://${integration.stream_base.host}`
                          : "rtsp://mediamtx:8554"
                      }
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>{t("liveTv.integrations.streamBaseHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          {kind === "mediamtx" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="username"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("liveTv.integrations.username")}</FormLabel>
                    <FormControl>
                      <Input autoComplete="off" dir="ltr" {...field} />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("liveTv.integrations.password")}</FormLabel>
                    <FormControl>
                      <PasswordInput autoComplete="new-password" {...field} />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>
          ) : (
            <FormField
              control={form.control}
              name="access_token"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveTv.integrations.accessToken")}</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="off" {...field} />
                  </FormControl>
                  <FormDescription>{t("liveTv.integrations.secretHelp")}</FormDescription>
                </FormItem>
              )}
            />
          )}
          <FormField
            control={form.control}
            name="rights_holder"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("liveTv.form.rightsHolder")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormDescription>{t("liveTv.integrations.rightsHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
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
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {integration ? t("common.save") : t("liveTv.integrations.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Add an ErsatzTV or MediaMTX instance, or edit one. */
export function IntegrationSheet({
  integration,
  open,
  onOpenChange,
}: {
  integration: LiveIntegration | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-lg"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>
            {integration ? t("liveTv.integrations.editTitle") : t("liveTv.integrations.newTitle")}
          </SheetTitle>
          <SheetDescription>{t("liveTv.integrations.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <IntegrationForm
            integration={integration}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
