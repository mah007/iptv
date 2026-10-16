import { zodResolver } from "@hookform/resolvers/zod";
import {
  getImportJobsListQueryKey,
  useImportJobsCreate,
  useImportSourcesBrowse,
  type ImportSource,
  type ImportStatus,
  type RemoteEntry,
} from "@smart-iptv/api";
import {
  Alert,
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Form,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { File, Folder, FolderUp } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import { remoteErrorText, useRemoteAnswer } from "./remote";
import {
  TARGET_DEFAULTS,
  TARGET_FIELD_PATHS,
  TargetFields,
  targetBody,
  targetSchema,
  type TargetValues,
} from "./target-fields";

type Picked = Map<string, RemoteEntry>;

function parentOf(path: string): string {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

/** One folder of the source, listed by the importer, with checkboxes. */
function Listing({
  source,
  path,
  onOpen,
  picked,
  onToggle,
}: {
  source: ImportSource;
  path: string;
  onOpen: (path: string) => void;
  picked: Picked;
  onToggle: (entry: RemoteEntry, on: boolean) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const browse = useImportSourcesBrowse();
  const [request, setRequest] = useState<{ id: string; at: number } | null>(null);
  const answer = useRemoteAnswer(request?.id ?? null, request?.at ?? null);
  const { mutate } = browse;

  useEffect(() => {
    mutate(
      { id: source.id, data: { path } },
      {
        onSuccess: (queued) => {
          setRequest({ id: queued.request_id, at: Date.now() });
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  }, [mutate, source.id, path, t]);

  const result = answer.result;
  const crumbs = path.split("/").filter(Boolean);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          size="xs"
          disabled={path === ""}
          onClick={() => {
            onOpen(parentOf(path));
          }}
        >
          <FolderUp aria-hidden="true" />
          {t("imports.browser.up")}
        </Button>
        <Breadcrumb aria-label={t("imports.browser.location")}>
          <BreadcrumbList>
            <BreadcrumbItem>
              {crumbs.length === 0 ? (
                <BreadcrumbPage>{t("imports.browser.top")}</BreadcrumbPage>
              ) : (
                <BreadcrumbLink asChild>
                  <button
                    type="button"
                    onClick={() => {
                      onOpen("");
                    }}
                  >
                    {t("imports.browser.top")}
                  </button>
                </BreadcrumbLink>
              )}
            </BreadcrumbItem>
            {crumbs.map((crumb, index) => (
              <Fragment key={`${String(index)}-${crumb}`}>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  {index === crumbs.length - 1 ? (
                    <BreadcrumbPage>
                      <bdi>{crumb}</bdi>
                    </BreadcrumbPage>
                  ) : (
                    <BreadcrumbLink asChild>
                      <button
                        type="button"
                        onClick={() => {
                          onOpen(crumbs.slice(0, index + 1).join("/"));
                        }}
                      >
                        <bdi>{crumb}</bdi>
                      </button>
                    </BreadcrumbLink>
                  )}
                </BreadcrumbItem>
              </Fragment>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      </div>
      {answer.timedOut ? (
        <Alert tone="danger" title={t("imports.browser.failed")}>
          {t("imports.browser.noAnswer")}
        </Alert>
      ) : result === null ? (
        <div role="status" aria-live="polite" className="grid gap-2">
          <span className="sr-only">{t("imports.browser.loading")}</span>
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      ) : !result.ok ? (
        <Alert tone="danger" title={t("imports.browser.failed")}>
          {remoteErrorText(t, result.code)}
          {result.message ? (
            <span className="mt-1 block font-mono text-xs" dir="ltr">
              {result.message}
            </span>
          ) : null}
        </Alert>
      ) : (result.entries ?? []).length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">
          {t("imports.browser.empty")}
        </p>
      ) : (
        <div className="max-h-[45vh] overflow-auto rounded-input border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <span className="sr-only">{t("imports.browser.choose")}</span>
                </TableHead>
                <TableHead>{t("imports.browser.name")}</TableHead>
                <TableHead className="text-end">{t("imports.browser.size")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(result.entries ?? []).map((entry) => (
                <TableRow
                  key={entry.path}
                  data-state={picked.has(entry.path) ? "selected" : undefined}
                >
                  <TableCell>
                    <Checkbox
                      aria-label={t("imports.browser.select", { name: entry.name })}
                      disabled={!entry.importable}
                      checked={picked.has(entry.path)}
                      onCheckedChange={(checked) => {
                        onToggle(entry, checked === true);
                      }}
                    />
                  </TableCell>
                  <TableCell className="min-w-0">
                    {entry.is_dir ? (
                      <Button
                        type="button"
                        variant="link"
                        size="xs"
                        className="h-auto max-w-full justify-start px-0"
                        onClick={() => {
                          onOpen(entry.path);
                        }}
                      >
                        <Folder aria-hidden="true" className="shrink-0" />
                        <bdi className="truncate">{entry.name}</bdi>
                      </Button>
                    ) : (
                      <span className="flex min-w-0 items-center gap-1.5">
                        <File
                          aria-hidden="true"
                          className="size-3.5 shrink-0 text-muted-foreground"
                        />
                        <bdi className="truncate">{entry.name}</bdi>
                        {entry.importable ? null : (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {t("imports.browser.notImportable")}
                          </span>
                        )}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-end tabular-nums whitespace-nowrap">
                    {entry.size === null ? "—" : format.bytes(entry.size)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {result?.truncated ? (
        <p className="text-xs text-muted-foreground">{t("imports.browser.truncated")}</p>
      ) : null}
    </div>
  );
}

function ImportForm({
  source,
  picked,
  status,
  onBack,
  onDone,
}: {
  source: ImportSource;
  picked: Picked;
  status: ImportStatus | undefined;
  onBack: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useImportJobsCreate();
  const form = useForm<TargetValues>({
    resolver: zodResolver(targetSchema),
    defaultValues: TARGET_DEFAULTS,
  });
  const submit = form.handleSubmit(async (values) => {
    try {
      await create.mutateAsync({
        data: {
          source: source.id,
          items: [...picked.values()].map((entry) => ({ path: entry.path, is_dir: entry.is_dir })),
          ...targetBody(values),
        },
      });
      await queryClient.invalidateQueries({ queryKey: getImportJobsListQueryKey() });
      toast.success(t("imports.form.started"));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, TARGET_FIELD_PATHS, t).length === 0) {
        notifyError(t, error);
      }
    }
  });
  return (
    <Form {...form}>
      <form
        noValidate
        className="grid gap-4"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <TargetFields status={status} />
        <p className="text-xs text-muted-foreground">{t("imports.form.rightsNote")}</p>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onBack}>
            {t("imports.browser.back")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("imports.form.start")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

/** Browse a source, pick files or folders, then say where they go and under which rights. */
function BrowserBody({
  source,
  status,
  onDone,
}: {
  source: ImportSource;
  status: ImportStatus | undefined;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const [path, setPath] = useState("");
  const [picked, setPicked] = useState<Picked>(new Map());
  const [step, setStep] = useState<"browse" | "target">("browse");
  const files = [...picked.values()].filter((entry) => !entry.is_dir);
  const size = files.reduce((total, entry) => total + (entry.size ?? 0), 0);
  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {step === "browse"
            ? t("imports.browser.title", { name: source.name })
            : t("imports.form.title")}
        </DialogTitle>
        <DialogDescription>
          {step === "browse"
            ? t("imports.browser.description")
            : t("imports.form.description", { count: picked.size })}
        </DialogDescription>
      </DialogHeader>
      {step === "browse" ? (
        <>
          <Listing
            key={path}
            source={source}
            path={path}
            onOpen={setPath}
            picked={picked}
            onToggle={(entry, on) => {
              setPicked((previous) => {
                const next = new Map(previous);
                if (on) next.set(entry.path, entry);
                else next.delete(entry.path);
                return next;
              });
            }}
          />
          <DialogFooter className="items-center sm:justify-between">
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {picked.size === 0
                ? t("imports.browser.noneSelected")
                : t("imports.browser.selected", { count: picked.size, size: format.bytes(size) })}
            </p>
            <Button
              type="button"
              disabled={picked.size === 0}
              onClick={() => {
                setStep("target");
              }}
            >
              {t("imports.browser.next")}
            </Button>
          </DialogFooter>
        </>
      ) : (
        <ImportForm
          source={source}
          picked={picked}
          status={status}
          onBack={() => {
            setStep("browse");
          }}
          onDone={onDone}
        />
      )}
    </>
  );
}

export function BrowserDialog({
  source,
  status,
  open,
  onOpenChange,
}: {
  source: ImportSource | null;
  status: ImportStatus | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        {open && source !== null ? (
          <BrowserBody
            key={source.id}
            source={source}
            status={status}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
