import { zodResolver } from "@hookform/resolvers/zod";
import type { MovieDetail, PatchedMovieUpdateRequest, SeriesDetail } from "@smart-iptv/api";
import {
  Button,
  Form,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@smart-iptv/ui";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import {
  CategoryChoices,
  DetailFields,
  GenreChoices,
  METADATA_FIELD_PATHS,
  RightsFields,
  Section,
  TitleTexts,
  dateInput,
  expiryOf,
  metadataSchema,
  type MetadataField,
  type MetadataInput,
  type MetadataValues,
} from "./metadata-fields";

/* The title editor (SPEC §8.3.6): English and Arabic side by side, the details, the
 * categories and genres, and the rights. */

export type TitleDetail = MovieDetail | SeriesDetail;

function formValues(title: TitleDetail): MetadataInput {
  const movie = "runtime_min" in title ? title : null;
  return {
    title: title.title,
    title_ar: title.title_ar,
    original_title: title.original_title,
    overview: title.overview,
    overview_ar: title.overview_ar,
    tagline: title.tagline,
    tagline_ar: title.tagline_ar,
    year: title.year === null ? "" : String(title.year),
    release_date: movie?.release_date ?? "",
    runtime_min: movie?.runtime_min ? String(movie.runtime_min) : "",
    certification: title.certification,
    original_language: title.original_language,
    countries: title.countries.join(", "),
    trailer_youtube_key: title.trailer_youtube_key,
    categories: title.categories.map((category) => category.id),
    genres: title.genres.map((genre) => genre.id),
    rights_holder: title.rights_holder,
    license_ref: title.license_ref,
    license_expires_at: dateInput(title.license_expires_at),
  };
}

const PLAIN_FIELDS = [
  "title",
  "title_ar",
  "original_title",
  "overview",
  "overview_ar",
  "tagline",
  "tagline_ar",
  "year",
  "runtime_min",
  "certification",
  "original_language",
  "countries",
  "trailer_youtube_key",
  "categories",
  "genres",
  "rights_holder",
  "license_ref",
] as const satisfies readonly MetadataField[];

/**
 * Only what the admin changed: every metadata field sent is locked against
 * metadata refreshes, so untouched fields must stay out of the request.
 */
function patchOf(
  values: MetadataValues,
  dirty: Partial<Record<MetadataField, unknown>>,
): PatchedMovieUpdateRequest {
  const patch: Record<string, unknown> = {};
  for (const field of PLAIN_FIELDS) {
    if (dirty[field]) patch[field] = values[field];
  }
  if (dirty.release_date) patch.release_date = values.release_date || null;
  if (dirty.license_expires_at) patch.license_expires_at = expiryOf(values.license_expires_at);
  return patch;
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
  const form = useForm<MetadataInput, unknown, MetadataValues>({
    resolver: zodResolver(metadataSchema),
    defaultValues: formValues(title),
  });
  // A manual title has every field locked: there is no provider to refresh from.
  const locked = new Set(title.metadata_source === "manual" ? [] : title.metadata_locked_fields);
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
      if (applyFieldErrors(error, form.setError, METADATA_FIELD_PATHS, t).length === 0) {
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
        <SheetBody className="grid content-start gap-6">
          <Section title={t("titles.editor.sections.texts")}>
            <TitleTexts locked={locked} />
          </Section>
          <Section title={t("titles.editor.sections.details")}>
            <DetailFields locked={locked} withDates={kind === "movie"} />
          </Section>
          <Section title={t("titles.editor.sections.classification")}>
            <CategoryChoices
              kind={kind === "movie" ? "vod" : "series"}
              locked={locked.has("categories")}
            />
            <GenreChoices locked={locked.has("genres")} />
          </Section>
          <Section title={t("titles.editor.sections.rights")}>
            <RightsFields locked={locked} />
          </Section>
          <p className="text-xs text-muted-foreground">
            {title.metadata_source === "manual"
              ? t("titles.editor.manualHelp")
              : t("titles.editor.lockHelp")}
          </p>
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
