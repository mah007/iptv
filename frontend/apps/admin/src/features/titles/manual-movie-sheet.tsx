import { zodResolver } from "@hookform/resolvers/zod";
import {
  apiFetch,
  getMoviesCreateManualUrl,
  isApiError,
  useMoviesManualDefaults,
  type ManualDefaults,
  type MovieDetail,
  type Review,
} from "@smart-iptv/api";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  FormControl,
  FormDescription,
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
  useFormatters,
} from "@smart-iptv/ui";
import { X } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { QueryError } from "../../components/states";
import {
  applyFieldErrors,
  fieldErrorCodes,
  fieldErrorMessage,
  notifyError,
} from "../../lib/problems";
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
} from "./metadata-fields";

/*
 * A movie described by hand (ADR-0026) for a file no metadata provider knows: the
 * review queue's "Create movie manually". The rights holder, the licence reference and
 * the admin's confirmation are required; the artwork is optional (without it, a frame of
 * the video is used). Sent as multipart, as the title page's image uploads are.
 */

/** The image pipeline's limit (apps.metadata.services.MANUAL_IMAGE_MAX_BYTES). */
const IMAGE_MAX_BYTES = 15 * 1024 * 1024;
const IMAGE_TYPES = "image/jpeg,image/png,image/webp,image/avif,image/gif";
const NO_LOCKS: ReadonlySet<string> = new Set();

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

const manualSchema = metadataSchema.extend({
  rights_holder: z
    .string()
    .trim()
    .min(1, "titles.manual.validation.rightsHolder")
    .max(255, "titles.validation.tooLong"),
  license_ref: z
    .string()
    .trim()
    .min(1, "titles.manual.validation.licenseRef")
    .max(255, "titles.validation.tooLong"),
  license_expires_at: z
    .string()
    .refine((value) => value === "" || value > today(), "titles.manual.validation.expiry"),
  rights_confirmed: z.boolean().refine((value) => value, "titles.manual.validation.confirm"),
});

type ManualInput = z.input<typeof manualSchema>;
type ManualValues = z.output<typeof manualSchema>;
type ImageKind = "poster" | "backdrop";

const FIELD_PATHS = { ...METADATA_FIELD_PATHS, rights_confirmed: "rights_confirmed" } as const;

function initialValues(defaults: ManualDefaults): ManualInput {
  return {
    title: defaults.title,
    title_ar: "",
    original_title: "",
    overview: "",
    overview_ar: "",
    tagline: "",
    tagline_ar: "",
    year: defaults.year === null ? "" : String(defaults.year),
    release_date: "",
    runtime_min: defaults.runtime_min === null ? "" : String(defaults.runtime_min),
    certification: "",
    original_language: "",
    countries: "",
    trailer_youtube_key: "",
    categories: [...defaults.categories],
    genres: [],
    rights_holder: defaults.rights_holder,
    license_ref: defaults.license_ref,
    license_expires_at: dateInput(defaults.license_expires_at),
    rights_confirmed: false,
  };
}

/** The request: what the admin filled in, the arrays as repeated fields, the images. */
function formData(
  reviewId: string,
  values: ManualValues,
  images: Readonly<Record<ImageKind, File | null>>,
): FormData {
  const body = new FormData();
  body.append("review", reviewId);
  const texts = [
    "title",
    "title_ar",
    "original_title",
    "overview",
    "overview_ar",
    "tagline",
    "tagline_ar",
    "certification",
    "original_language",
    "trailer_youtube_key",
    "rights_holder",
    "license_ref",
    "release_date",
  ] as const;
  for (const name of texts) {
    if (values[name] !== "") body.append(name, values[name]);
  }
  if (values.year !== null) body.append("year", String(values.year));
  if (values.runtime_min !== null) body.append("runtime_min", String(values.runtime_min));
  const expiry = expiryOf(values.license_expires_at);
  if (expiry !== null) body.append("license_expires_at", expiry);
  for (const code of values.countries) body.append("countries", code);
  for (const id of values.categories) body.append("categories", id);
  for (const id of values.genres) body.append("genres", id);
  body.append("rights_confirmed", "true");
  for (const kind of ["poster", "backdrop"] as const) {
    const file = images[kind];
    if (file !== null) body.append(kind, file);
  }
  return body;
}

function ImagePicker({
  kind,
  file,
  error,
  onChange,
}: {
  kind: ImageKind;
  file: File | null;
  error: string | null;
  onChange: (file: File | null) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const preview = useMemo(() => (file === null ? null : URL.createObjectURL(file)), [file]);
  useEffect(
    () => () => {
      if (preview !== null) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const errorId = `${id}-error`;
  return (
    <div className="grid content-start gap-2">
      <Label htmlFor={id}>{t(`titles.manual.images.${kind}`)}</Label>
      {preview ? (
        <div className="flex items-start gap-2">
          <img
            src={preview}
            alt={t("titles.manual.images.preview", { kind: t(`titles.manual.images.${kind}`) })}
            className={
              kind === "poster"
                ? "aspect-[2/3] w-24 rounded-input object-cover ring-1 ring-border"
                : "aspect-video w-40 rounded-input object-cover ring-1 ring-border"
            }
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("titles.manual.images.remove", {
              kind: t(`titles.manual.images.${kind}`),
            })}
            onClick={() => {
              onChange(null);
            }}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
      ) : null}
      <Input
        id={id}
        type="file"
        accept={IMAGE_TYPES}
        aria-invalid={error === null ? undefined : true}
        aria-describedby={error === null ? undefined : errorId}
        onChange={(event) => {
          onChange(event.target.files?.[0] ?? null);
          event.target.value = "";
        }}
      />
      <p className="text-xs text-muted-foreground">{t(`titles.manual.images.${kind}Help`)}</p>
      {error === null ? null : (
        <p id={errorId} className="text-xs font-medium text-danger-text">
          {error}
        </p>
      )}
    </div>
  );
}

function ManualForm({
  review,
  defaults,
  onCreated,
  onCancel,
}: {
  review: Review;
  defaults: ManualDefaults;
  onCreated: (movie: MovieDetail) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const form = useForm<ManualInput, unknown, ManualValues>({
    resolver: zodResolver(manualSchema),
    defaultValues: initialValues(defaults),
  });
  const [images, setImages] = useState<Record<ImageKind, File | null>>({
    poster: null,
    backdrop: null,
  });
  const [imageErrors, setImageErrors] = useState<Record<ImageKind, string | null>>({
    poster: null,
    backdrop: null,
  });

  function pick(kind: ImageKind, file: File | null): void {
    const tooLarge = file !== null && file.size > IMAGE_MAX_BYTES;
    setImageErrors((previous) => ({
      ...previous,
      [kind]: tooLarge ? t("titles.manual.images.tooLarge") : null,
    }));
    setImages((previous) => ({ ...previous, [kind]: tooLarge ? null : file }));
  }

  const submit = form.handleSubmit(async (values) => {
    try {
      const movie = await apiFetch<MovieDetail>(getMoviesCreateManualUrl(), {
        method: "POST",
        body: formData(review.id, values, images),
      });
      onCreated(movie);
    } catch (error) {
      const applied = applyFieldErrors(error, form.setError, FIELD_PATHS, t);
      let imageError = false;
      if (isApiError(error)) {
        for (const kind of ["poster", "backdrop"] as const) {
          const message = error.fieldErrors[kind]?.[0];
          if (message === undefined) continue;
          imageError = true;
          setImageErrors((previous) => ({
            ...previous,
            [kind]: fieldErrorMessage(t, fieldErrorCodes(error, kind)[0], message),
          }));
        }
      }
      if (applied.length === 0 && !imageError) notifyError(t, error);
    }
  });

  const minutes = defaults.runtime_min;
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
          <p className="break-all text-ui text-muted-foreground">
            {t("titles.manual.forFile")}{" "}
            <bdi dir="ltr" className="font-mono text-foreground">
              {review.media_file.relative_path}
            </bdi>
          </p>
          <Section title={t("titles.editor.sections.texts")}>
            <TitleTexts locked={NO_LOCKS} />
          </Section>
          <Section title={t("titles.editor.sections.details")}>
            <DetailFields
              locked={NO_LOCKS}
              runtimeHelp={
                minutes === null
                  ? undefined
                  : t("titles.manual.runtimeHelp", { minutes: format.number(minutes) })
              }
            />
          </Section>
          <Section title={t("titles.editor.sections.classification")}>
            <CategoryChoices
              kind="vod"
              locked={false}
              description={t("titles.manual.categoriesHelp")}
            />
            <GenreChoices locked={false} />
          </Section>
          <Section title={t("titles.manual.images.title")}>
            <div className="grid gap-4 sm:grid-cols-2">
              {(["poster", "backdrop"] as const).map((kind) => (
                <ImagePicker
                  key={kind}
                  kind={kind}
                  file={images[kind]}
                  error={imageErrors[kind]}
                  onChange={(file) => {
                    pick(kind, file);
                  }}
                />
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{t("titles.manual.images.frameHelp")}</p>
          </Section>
          <Section title={t("titles.editor.sections.rights")}>
            <RightsFields locked={NO_LOCKS} required />
            <FormField
              control={form.control}
              name="rights_confirmed"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-start gap-2 rounded-input border border-border p-3">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={(next) => {
                          field.onChange(next === true);
                        }}
                        onBlur={field.onBlur}
                        ref={field.ref}
                        className="mt-0.5"
                      />
                    </FormControl>
                    <div className="grid gap-1">
                      <FormLabel className="font-medium">{t("titles.manual.confirm")}</FormLabel>
                      <FormDescription>{t("titles.manual.confirmHelp")}</FormDescription>
                    </div>
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />
          </Section>
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("titles.manual.submit")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

function ManualBody({
  review,
  onCreated,
  onCancel,
}: {
  review: Review;
  onCreated: (movie: MovieDetail) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const defaults = useMoviesManualDefaults({ review: review.id }, { query: { gcTime: 0 } });
  if (defaults.isPending) {
    return (
      <SheetBody className="grid content-start gap-4" aria-busy="true">
        <span className="sr-only">{t("layout.loading")}</span>
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-10 w-2/3" />
      </SheetBody>
    );
  }
  if (defaults.isError) {
    return (
      <SheetBody>
        <QueryError
          error={defaults.error}
          onRetry={() => {
            void defaults.refetch();
          }}
        />
      </SheetBody>
    );
  }
  return (
    <>
      {review.kind === "tv" ? (
        <div className="px-(--density-card) pt-2">
          <Alert tone="info">{t("titles.manual.seriesNote")}</Alert>
        </div>
      ) : null}
      <ManualForm
        review={review}
        defaults={defaults.data}
        onCreated={onCreated}
        onCancel={onCancel}
      />
    </>
  );
}

/** "Create movie manually": the form sheet, for an open or skipped review's file. */
export function ManualMovieSheet({
  review,
  open,
  onOpenChange,
  onCreated,
}: {
  review: Review;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (movie: MovieDetail) => void;
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
          <SheetTitle>{t("titles.manual.title")}</SheetTitle>
          <SheetDescription>{t("titles.manual.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <ManualBody
            review={review}
            onCreated={onCreated}
            onCancel={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
