import { zodResolver } from "@hookform/resolvers/zod";
import {
  useCategoriesList,
  type MovieDetail,
  type PatchedMovieUpdateRequest,
  type SeriesDetail,
} from "@smart-iptv/api";
import {
  Button,
  Checkbox,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Label,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Skeleton,
  Textarea,
} from "@smart-iptv/ui";
import { Lock } from "lucide-react";
import { useId, type ReactNode } from "react";
import { useForm, useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import { localName } from "../catalog/artwork";

/* The title editor (SPEC §8.3.6): English and Arabic side by side. */

const YEAR_MIN = 1870;
const YEAR_MAX = 2200;

const editorSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "titles.validation.titleRequired")
    .max(255, "titles.validation.tooLong"),
  title_ar: z.string().trim().max(255, "titles.validation.tooLong"),
  overview: z.string().trim(),
  overview_ar: z.string().trim(),
  tagline: z.string().trim().max(500, "titles.validation.tooLong"),
  tagline_ar: z.string().trim().max(500, "titles.validation.tooLong"),
  year: z
    .string()
    .trim()
    .refine(
      (value) =>
        value === "" ||
        (/^\d{4}$/u.test(value) && Number(value) >= YEAR_MIN && Number(value) <= YEAR_MAX),
      "titles.validation.year",
    )
    .transform((value) => (value === "" ? null : Number(value))),
  categories: z.array(z.string()),
});

type EditorInput = z.input<typeof editorSchema>;
type EditorValues = z.output<typeof editorSchema>;
type EditorField = keyof EditorInput;

const TEXT_FIELDS = [
  "title",
  "title_ar",
  "overview",
  "overview_ar",
  "tagline",
  "tagline_ar",
] as const satisfies readonly EditorField[];

const FIELD_PATHS: Record<string, EditorField> = {
  title: "title",
  title_ar: "title_ar",
  overview: "overview",
  overview_ar: "overview_ar",
  tagline: "tagline",
  tagline_ar: "tagline_ar",
  year: "year",
  categories: "categories",
};

export type TitleDetail = MovieDetail | SeriesDetail;

function formValues(title: TitleDetail): EditorInput {
  return {
    title: title.title,
    title_ar: title.title_ar,
    overview: title.overview,
    overview_ar: title.overview_ar,
    tagline: title.tagline,
    tagline_ar: title.tagline_ar,
    year: title.year === null ? "" : String(title.year),
    categories: title.categories.map((category) => category.id),
  };
}

/**
 * Only what the admin changed: every metadata field sent is locked against
 * metadata refreshes, so untouched fields must stay out of the request.
 */
function patchOf(
  values: EditorValues,
  dirty: Partial<Record<EditorField, unknown>>,
): PatchedMovieUpdateRequest {
  const patch: PatchedMovieUpdateRequest = {};
  for (const field of TEXT_FIELDS) {
    if (dirty[field]) patch[field] = values[field];
  }
  if (dirty.year) patch.year = values.year;
  if (dirty.categories) patch.categories = values.categories;
  return patch;
}

function LockedHint({ locked }: { locked: boolean }) {
  const { t } = useTranslation();
  if (!locked) return null;
  return (
    <Lock
      aria-label={t("titles.editor.locked")}
      role="img"
      className="size-3.5 text-muted-foreground"
    />
  );
}

function Pair({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2">{children}</div>;
}

function CategoryChoices({ kind }: { kind: "vod" | "series" }) {
  const { t, i18n } = useTranslation();
  const form = useFormContext<EditorInput>();
  const query = useCategoriesList({ kind, page_size: 100 });
  const baseId = useId();
  return (
    <FormField
      control={form.control}
      name="categories"
      render={({ field }) => (
        <FormItem>
          <fieldset className="grid gap-2">
            <legend className="mb-2 text-ui font-medium text-foreground">
              {t("titles.editor.categories")}
            </legend>
            {query.isPending ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {(query.data?.results ?? []).map((category) => {
                  const id = `${baseId}-${category.id}`;
                  return (
                    <Label
                      key={category.id}
                      htmlFor={id}
                      className="flex cursor-pointer items-center gap-2 rounded-input border border-border px-3 py-2 font-normal hover:bg-accent"
                    >
                      <Checkbox
                        id={id}
                        checked={field.value.includes(category.id)}
                        onCheckedChange={(next) => {
                          field.onChange(
                            next === true
                              ? [...field.value, category.id]
                              : field.value.filter((value) => value !== category.id),
                          );
                        }}
                      />
                      {localName(category, i18n.language)}
                    </Label>
                  );
                })}
              </div>
            )}
          </fieldset>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function EditorForm({
  title,
  kind,
  onSave,
  onDone,
}: {
  title: TitleDetail;
  kind: "movie" | "series";
  onSave: (patch: PatchedMovieUpdateRequest) => Promise<unknown>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const form = useForm<EditorInput, unknown, EditorValues>({
    resolver: zodResolver(editorSchema),
    defaultValues: formValues(title),
  });
  const locked = new Set(title.metadata_locked_fields);
  const submit = form.handleSubmit(async (values) => {
    const patch = patchOf(values, form.formState.dirtyFields);
    if (Object.keys(patch).length === 0) {
      onDone();
      return;
    }
    try {
      await onSave(patch);
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, FIELD_PATHS).length === 0) {
        notifyError(t, error);
      }
    }
  });

  function textField(name: (typeof TEXT_FIELDS)[number], multiline = false) {
    const arabic = name.endsWith("_ar");
    return (
      <FormField
        control={form.control}
        name={name}
        render={({ field }) => (
          <FormItem>
            <FormLabel className="flex items-center gap-1.5">
              {t(`titles.editor.fields.${name}`)}
              <LockedHint locked={locked.has(name)} />
            </FormLabel>
            <FormControl>
              {multiline ? (
                <Textarea
                  rows={5}
                  dir={arabic ? "rtl" : "ltr"}
                  lang={arabic ? "ar" : "en"}
                  {...field}
                />
              ) : (
                <Input
                  autoComplete="off"
                  dir={arabic ? "rtl" : "ltr"}
                  lang={arabic ? "ar" : "en"}
                  {...field}
                />
              )}
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    );
  }

  return (
    <Form {...form}>
      <form
        noValidate
        className="flex min-h-0 flex-1 flex-col"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <SheetBody className="grid content-start gap-5">
          <Pair>
            {textField("title")}
            {textField("title_ar")}
          </Pair>
          <Pair>
            {textField("overview", true)}
            {textField("overview_ar", true)}
          </Pair>
          <Pair>
            {textField("tagline")}
            {textField("tagline_ar")}
          </Pair>
          <FormField
            control={form.control}
            name="year"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="flex items-center gap-1.5">
                  {t("titles.editor.fields.year")}
                  <LockedHint locked={locked.has("year")} />
                </FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="off"
                    dir="ltr"
                    className="w-28 tabular-nums"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <CategoryChoices kind={kind === "movie" ? "vod" : "series"} />
          <p className="text-xs text-muted-foreground">{t("titles.editor.lockHelp")}</p>
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("common.save")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Edit a movie's or series' metadata; each changed field is locked against refreshes. */
export function TitleEditorSheet({
  title,
  kind,
  open,
  onOpenChange,
  onSave,
}: {
  title: TitleDetail;
  kind: "movie" | "series";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (patch: PatchedMovieUpdateRequest) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-3xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>{t("titles.editor.title")}</SheetTitle>
          <SheetDescription>{t("titles.editor.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <EditorForm
            title={title}
            kind={kind}
            onSave={onSave}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
