import { zodResolver } from "@hookform/resolvers/zod";
import {
  getLibrariesListQueryKey,
  useLibrariesCreate,
  useLibrariesUpdate,
  type Library,
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
  Label,
  RadioGroup,
  RadioGroupItem,
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
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import {
  LIBRARY_DEFAULTS,
  LIBRARY_FIELD_PATHS,
  LIBRARY_KINDS,
  POLICIES,
  libraryFormSchema,
  libraryFormValues,
  libraryRequest,
  type LibraryFormInput,
  type LibraryFormValues,
} from "./schemas";

function PolicyChoice({ value }: { value: string }) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <Label
      htmlFor={id}
      className="flex cursor-pointer items-start gap-2.5 rounded-input border border-border px-3 py-2.5 font-normal transition-colors hover:bg-accent has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5"
    >
      <RadioGroupItem id={id} value={value} className="mt-0.5" />
      <span className="grid gap-0.5">
        <span className="font-medium text-foreground">
          {t(`libraries.policies.${value}.title`)}
        </span>
        <span className="text-xs text-muted-foreground">
          {t(`libraries.policies.${value}.description`)}
        </span>
      </span>
    </Label>
  );
}

function LibraryForm({ library, onDone }: { library: Library | null; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const form = useForm<LibraryFormInput, unknown, LibraryFormValues>({
    resolver: zodResolver(libraryFormSchema),
    defaultValues: library ? libraryFormValues(library) : LIBRARY_DEFAULTS,
  });
  const create = useLibrariesCreate();
  const update = useLibrariesUpdate();
  const submit = form.handleSubmit(async (values) => {
    try {
      const data = libraryRequest(values);
      if (library) {
        await update.mutateAsync({ id: library.id, data });
      } else {
        await create.mutateAsync({ data });
      }
      await queryClient.invalidateQueries({ queryKey: getLibrariesListQueryKey() });
      toast.success(
        library
          ? t("libraries.form.saved", { name: values.name })
          : t("libraries.form.created", { name: values.name }),
      );
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, LIBRARY_FIELD_PATHS, t).length === 0) {
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
                <FormLabel>{t("libraries.form.name")}</FormLabel>
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
                <FormLabel>{t("libraries.form.kind")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {LIBRARY_KINDS.map((kind) => (
                      <SelectItem key={kind} value={kind}>
                        {t(`libraries.kinds.${kind}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="path"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("libraries.form.path")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    dir="ltr"
                    className="font-mono"
                    {...field}
                  />
                </FormControl>
                <FormDescription>{t("libraries.form.pathHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="processing_policy"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("libraries.form.policy")}</FormLabel>
                <FormControl>
                  <RadioGroup
                    value={field.value}
                    onValueChange={field.onChange}
                    className="grid gap-2"
                  >
                    {POLICIES.map((policy) => (
                      <PolicyChoice key={policy} value={policy} />
                    ))}
                  </RadioGroup>
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="scan_interval_min"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("libraries.form.interval")}</FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="off"
                    dir="ltr"
                    className="w-32 tabular-nums"
                    {...field}
                  />
                </FormControl>
                <FormDescription>{t("libraries.form.intervalHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="enabled"
            render={({ field }) => (
              <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
                <div className="grid gap-0.5">
                  <FormLabel>{t("libraries.form.enabled")}</FormLabel>
                  <FormDescription>{t("libraries.form.enabledHelp")}</FormDescription>
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
            {library ? t("common.save") : t("libraries.form.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Add a library, or edit one (`library`). */
export function LibrarySheet({
  library,
  open,
  onOpenChange,
}: {
  library: Library | null;
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
            {library ? t("libraries.form.editTitle") : t("libraries.form.newTitle")}
          </SheetTitle>
          <SheetDescription>{t("libraries.form.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <LibraryForm
            key={library?.id ?? "new"}
            library={library}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
