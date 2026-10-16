import { zodResolver } from "@hookform/resolvers/zod";
import {
  getImportJobsListQueryKey,
  useImportLinksCreate,
  type ImportStatus,
} from "@smart-iptv/api";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import {
  TARGET_DEFAULTS,
  TARGET_FIELD_PATHS,
  TargetFields,
  targetBody,
  targetSchema,
} from "./target-fields";

const schema = targetSchema.extend({
  url: z.string().trim().min(1, "imports.validation.urlRequired").max(4000),
  file_name: z.string().trim().max(255),
  checksum: z.string().trim().max(128),
});

type Values = z.output<typeof schema>;

const FIELD_PATHS = {
  ...TARGET_FIELD_PATHS,
  url: "url",
  file_name: "file_name",
  checksum: "checksum",
} as const;

function LinkForm({ status, onDone }: { status: ImportStatus | undefined; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useImportLinksCreate();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { ...TARGET_DEFAULTS, url: "", file_name: "", checksum: "" },
  });
  const submit = form.handleSubmit(async (values) => {
    try {
      await create.mutateAsync({
        data: {
          url: values.url,
          file_name: values.file_name,
          checksum: values.checksum,
          ...targetBody(values),
        },
      });
      await queryClient.invalidateQueries({ queryKey: getImportJobsListQueryKey() });
      toast.success(t("imports.form.started"));
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
        className="grid gap-4"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <FormField
          control={form.control}
          name="url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("imports.link.url")}</FormLabel>
              <FormControl>
                <Input
                  type="url"
                  autoComplete="off"
                  spellCheck={false}
                  dir="ltr"
                  className="font-mono"
                  placeholder="https://archive.org/download/…"
                  {...field}
                />
              </FormControl>
              <FormDescription>{t("imports.link.urlHelp")}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            control={form.control}
            name="file_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("imports.link.fileName")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormDescription>{t("imports.link.fileNameHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="checksum"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("imports.link.checksum")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="off"
                    spellCheck={false}
                    dir="ltr"
                    className="font-mono"
                    {...field}
                  />
                </FormControl>
                <FormDescription>{t("imports.link.checksumHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <TargetFields status={status} />
        <p className="text-xs text-muted-foreground">{t("imports.form.rightsNote")}</p>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("imports.form.start")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

/** One file from a direct HTTPS link (a public-domain film on the Internet Archive...). */
export function LinkDialog({
  status,
  open,
  onOpenChange,
}: {
  status: ImportStatus | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("imports.link.title")}</DialogTitle>
          <DialogDescription>{t("imports.link.description")}</DialogDescription>
        </DialogHeader>
        {open ? (
          <LinkForm
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
