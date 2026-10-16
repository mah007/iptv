import { zodResolver } from "@hookform/resolvers/zod";
import {
  getImportSourcesListQueryKey,
  type DriveTypeEnum,
  ImportSourceKind,
  SourceOptionsProviderEnum,
  useImportSourcesCreate,
  useImportSourcesUpdate,
  VendorEnum,
  type ImportSecretName,
  type ImportSource,
  type ImportSourceWriteRequest,
  type SourceOptionsRequest,
  type SourceSecretsRequest,
} from "@smart-iptv/api";
import {
  Alert,
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
  Switch,
  Textarea,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch, type Path } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { applyFieldErrors, notifyError } from "../../lib/problems";

const KINDS = Object.values(ImportSourceKind);
const PROVIDERS = Object.values(SourceOptionsProviderEnum);
const VENDORS = Object.values(VendorEnum);
const DRIVE_TYPES = ["personal", "business", "documentLibrary"] as const;
const OAUTH = new Set<string>(["drive", "dropbox", "onedrive"]);

const schema = z.object({
  kind: z.enum(KINDS as [ImportSourceKind, ...ImportSourceKind[]]),
  name: z.string().trim().min(1, "imports.validation.nameRequired").max(100),
  root: z.string().trim().max(1000),
  provider: z.enum(PROVIDERS as [SourceOptionsProviderEnum, ...SourceOptionsProviderEnum[]]),
  endpoint: z.string().trim().max(500),
  region: z.string().trim().max(40),
  path_style: z.boolean(),
  host: z.string().trim().max(253),
  port: z.string().trim().max(5),
  username: z.string().trim().max(128),
  host_keys: z.string().trim().max(8000),
  url: z.string().trim().max(500),
  vendor: z.enum(VENDORS as [VendorEnum, ...VendorEnum[]]),
  root_folder_id: z.string().trim().max(200),
  client_id: z.string().trim().max(200),
  drive_id: z.string().trim().max(200),
  drive_type: z.string(),
  access_key_id: z.string().max(200),
  secret_access_key: z.string().max(500),
  password: z.string().max(500),
  private_key: z.string().max(20000),
  key_passphrase: z.string().max(500),
  token: z.string().max(20000),
  client_secret: z.string().max(500),
  account_username: z.string().max(200),
});

type Values = z.output<typeof schema>;
type Field = keyof Values;

/** The options and secrets each kind uses (apps/imports/remotes.py). */
const OPTIONS: Record<ImportSourceKind, readonly Field[]> = {
  s3: ["provider", "endpoint", "region", "path_style", "root"],
  sftp: ["host", "port", "username", "host_keys", "root"],
  webdav: ["url", "vendor", "username", "root"],
  drive: ["root_folder_id", "client_id", "root"],
  dropbox: ["client_id", "root"],
  onedrive: ["drive_id", "drive_type", "client_id", "root"],
  xtream: ["url"],
};
const SECRETS: Record<ImportSourceKind, readonly ImportSecretName[]> = {
  s3: ["access_key_id", "secret_access_key"],
  sftp: ["password", "private_key", "key_passphrase"],
  webdav: ["password"],
  drive: ["token", "client_secret"],
  dropbox: ["token", "client_secret"],
  onedrive: ["token", "client_secret"],
  xtream: ["account_username", "password"],
};

/** API field names are the form's own (errors land on the field they name). */
const FIELD_PATHS = Object.fromEntries(
  Object.keys(schema.shape).map((name) => [name, name as Path<Values>]),
) as Record<string, Path<Values>>;

function defaults(source: ImportSource | null): Values {
  const options = source?.options ?? {};
  return {
    kind: source?.kind ?? "s3",
    name: source?.name ?? "",
    root: options.root ?? "",
    provider: options.provider ?? "aws",
    endpoint: options.endpoint ?? "",
    region: options.region ?? "",
    path_style: options.path_style ?? false,
    host: options.host ?? "",
    port: options.port ? String(options.port) : "22",
    username: options.username ?? "",
    host_keys: options.host_keys ?? "",
    url: options.url ?? "",
    vendor: options.vendor ?? "other",
    root_folder_id: options.root_folder_id ?? "",
    client_id: options.client_id ?? "",
    drive_id: options.drive_id ?? "",
    drive_type: options.drive_type ?? "",
    access_key_id: "",
    secret_access_key: "",
    password: "",
    private_key: "",
    key_passphrase: "",
    token: "",
    client_secret: "",
    account_username: "",
  };
}

function body(values: Values, creating: boolean): ImportSourceWriteRequest {
  const options: SourceOptionsRequest = {};
  for (const name of OPTIONS[values.kind]) {
    if (name === "port") options.port = Number(values.port) || 22;
    else if (name === "path_style") options.path_style = values.path_style;
    else if (name === "provider") options.provider = values.provider;
    else if (name === "vendor") options.vendor = values.vendor;
    else if (name === "drive_type") {
      // Empty: found by the first Test (sent as nothing, not as a blank choice).
      if (values.drive_type) options.drive_type = values.drive_type as DriveTypeEnum;
    } else (options as Record<string, string>)[name] = values[name];
  }
  const secrets: SourceSecretsRequest = {};
  for (const name of SECRETS[values.kind]) {
    if (values[name]) secrets[name] = values[name];
  }
  return {
    name: values.name,
    ...(creating ? { kind: values.kind } : {}),
    options,
    secrets,
  };
}

function SecretField({
  name,
  label,
  saved,
  multiline = false,
  help,
}: {
  name: ImportSecretName;
  label: string;
  saved: boolean;
  multiline?: boolean;
  help?: string | undefined;
}) {
  const { t } = useTranslation();
  return (
    <FormField
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            {multiline ? (
              <Textarea
                rows={4}
                dir="ltr"
                spellCheck={false}
                autoComplete="off"
                className="font-mono text-xs"
                placeholder={saved ? t("imports.sheet.secretSaved") : undefined}
                {...field}
              />
            ) : (
              <PasswordInput
                autoComplete="new-password"
                placeholder={saved ? t("imports.sheet.secretSaved") : undefined}
                {...field}
              />
            )}
          </FormControl>
          {help ? <FormDescription>{help}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function TextField({
  name,
  label,
  help,
  placeholder,
  ltr = true,
}: {
  name: Field;
  label: string;
  help?: string | undefined;
  placeholder?: string | undefined;
  ltr?: boolean;
}) {
  return (
    <FormField
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input
              autoComplete="off"
              spellCheck={false}
              dir={ltr ? "ltr" : "auto"}
              className={ltr ? "font-mono" : undefined}
              placeholder={placeholder}
              {...field}
              value={String(field.value ?? "")}
            />
          </FormControl>
          {help ? <FormDescription>{help}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function SourceForm({ source, onDone }: { source: ImportSource | null; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useImportSourcesCreate();
  const update = useImportSourcesUpdate();
  const form = useForm<z.input<typeof schema>, unknown, Values>({
    resolver: zodResolver(schema),
    defaultValues: defaults(source),
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const provider = useWatch({ control: form.control, name: "provider" });
  const sourceUrl = useWatch({ control: form.control, name: "url" });
  const saved = new Set<string>(source?.secrets_set ?? []);
  const submit = form.handleSubmit(async (values) => {
    try {
      if (source) {
        await update.mutateAsync({ id: source.id, data: body(values, false) });
      } else {
        await create.mutateAsync({ data: body(values, true) });
      }
      await queryClient.invalidateQueries({ queryKey: getImportSourcesListQueryKey() });
      toast.success(t("imports.sheet.saved", { name: values.name }));
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
                <FormLabel>{t("imports.sheet.kind")}</FormLabel>
                <Select
                  value={field.value}
                  onValueChange={field.onChange}
                  disabled={source !== null}
                >
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {KINDS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`imports.kinds.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{t(`imports.sheet.kindHelp.${kind}`)}</FormDescription>
              </FormItem>
            )}
          />
          <TextField name="name" label={t("imports.sheet.name")} ltr={false} />
          {kind === "s3" ? (
            <>
              <FormField
                control={form.control}
                name="provider"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("imports.sheet.provider")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {PROVIDERS.map((value) => (
                          <SelectItem key={value} value={value}>
                            {t(`imports.providers.${value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              {provider === "aws" ? null : (
                <TextField
                  name="endpoint"
                  label={t("imports.sheet.endpoint")}
                  help={t(`imports.sheet.endpointHelp.${provider}`)}
                  placeholder="https://"
                />
              )}
              <TextField
                name="region"
                label={t("imports.sheet.region")}
                help={t("imports.sheet.regionHelp")}
              />
              <FormField
                control={form.control}
                name="path_style"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
                    <div className="grid gap-0.5">
                      <FormLabel>{t("imports.sheet.pathStyle")}</FormLabel>
                      <FormDescription>{t("imports.sheet.pathStyleHelp")}</FormDescription>
                    </div>
                    <FormControl>
                      <Switch checked={field.value} onCheckedChange={field.onChange} />
                    </FormControl>
                  </FormItem>
                )}
              />
              <SecretField
                name="access_key_id"
                label={t("imports.sheet.accessKeyId")}
                saved={saved.has("access_key_id")}
              />
              <SecretField
                name="secret_access_key"
                label={t("imports.sheet.secretAccessKey")}
                saved={saved.has("secret_access_key")}
              />
            </>
          ) : null}
          {kind === "sftp" ? (
            <>
              <div className="grid gap-4 sm:grid-cols-[1fr_7rem]">
                <TextField name="host" label={t("imports.sheet.host")} />
                <TextField name="port" label={t("imports.sheet.port")} />
              </div>
              <TextField name="username" label={t("imports.sheet.username")} />
              <SecretField
                name="password"
                label={t("imports.sheet.password")}
                saved={saved.has("password")}
                help={t("imports.sheet.passwordOrKey")}
              />
              <SecretField
                name="private_key"
                label={t("imports.sheet.privateKey")}
                saved={saved.has("private_key")}
                multiline
              />
              <SecretField
                name="key_passphrase"
                label={t("imports.sheet.keyPassphrase")}
                saved={saved.has("key_passphrase")}
              />
              <FormField
                control={form.control}
                name="host_keys"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("imports.sheet.hostKeys")}</FormLabel>
                    <FormControl>
                      <Textarea
                        rows={2}
                        dir="ltr"
                        spellCheck={false}
                        className="font-mono text-xs"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>{t("imports.sheet.hostKeysHelp")}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </>
          ) : null}
          {kind === "webdav" ? (
            <>
              <TextField name="url" label={t("imports.sheet.url")} placeholder="https://" />
              <FormField
                control={form.control}
                name="vendor"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("imports.sheet.vendor")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {VENDORS.map((value) => (
                          <SelectItem key={value} value={value}>
                            {t(`imports.vendors.${value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <TextField name="username" label={t("imports.sheet.username")} />
              <SecretField
                name="password"
                label={t("imports.sheet.password")}
                saved={saved.has("password")}
              />
            </>
          ) : null}
          {kind === "xtream" ? (
            <>
              <TextField
                name="url"
                label={t("imports.sheet.xtreamUrl")}
                help={t("imports.sheet.xtreamUrlHelp")}
                placeholder="https://provider.example:8080"
              />
              {sourceUrl.trim().toLowerCase().startsWith("http://") ? (
                <Alert tone="warning" title={t("imports.sheet.insecureHttpTitle")}>
                  {t("imports.sheet.insecureHttpDescription")}
                </Alert>
              ) : null}
              <SecretField
                name="account_username"
                label={t("imports.sheet.username")}
                saved={saved.has("account_username")}
              />
              <SecretField
                name="password"
                label={t("imports.sheet.password")}
                saved={saved.has("password")}
              />
            </>
          ) : null}
          {OAUTH.has(kind) ? (
            <>
              <SecretField
                name="token"
                label={t("imports.sheet.token")}
                saved={saved.has("token")}
                multiline
                help={t(`imports.sheet.tokenHelp.${kind}`)}
              />
              {kind === "drive" ? (
                <TextField
                  name="root_folder_id"
                  label={t("imports.sheet.rootFolderId")}
                  help={t("imports.sheet.rootFolderIdHelp")}
                />
              ) : null}
              {kind === "onedrive" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    name="drive_id"
                    label={t("imports.sheet.driveId")}
                    help={t("imports.sheet.driveIdHelp")}
                  />
                  <FormField
                    control={form.control}
                    name="drive_type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t("imports.sheet.driveType")}</FormLabel>
                        <Select
                          value={field.value || "auto"}
                          onValueChange={(value) => {
                            field.onChange(value === "auto" ? "" : value);
                          }}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="auto">{t("imports.driveTypes.auto")}</SelectItem>
                            {DRIVE_TYPES.map((value) => (
                              <SelectItem key={value} value={value}>
                                {t(`imports.driveTypes.${value}`)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FormItem>
                    )}
                  />
                </div>
              ) : null}
              <TextField
                name="client_id"
                label={t("imports.sheet.clientId")}
                help={t("imports.sheet.clientIdHelp")}
              />
              <SecretField
                name="client_secret"
                label={t("imports.sheet.clientSecret")}
                saved={saved.has("client_secret")}
              />
            </>
          ) : null}
          {kind === "xtream" ? null : (
            <TextField
              name="root"
              label={t("imports.sheet.root")}
              help={t(kind === "s3" ? "imports.sheet.rootHelpS3" : "imports.sheet.rootHelp")}
            />
          )}
          <p className="text-xs text-muted-foreground">{t("imports.sheet.stepUpNote")}</p>
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {source ? t("common.save") : t("imports.sheet.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Add an import source, or change one (its kind stays). */
export function SourceSheet({
  source,
  open,
  onOpenChange,
}: {
  source: ImportSource | null;
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
            {source ? t("imports.sheet.editTitle") : t("imports.sheet.newTitle")}
          </SheetTitle>
          <SheetDescription>{t("imports.sheet.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <SourceForm
            source={source}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
