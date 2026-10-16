import { useCategoriesList, useGenresList } from "@smart-iptv/api";
import {
  Badge,
  Checkbox,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Label,
  Skeleton,
  Textarea,
} from "@smart-iptv/ui";
import { Lock } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { localName } from "../catalog/artwork";

/*
 * The metadata fields the title editor and the manual movie form share (ADR-0026):
 * titles and texts in English and Arabic side by side, the details, the categories and
 * genres, and the rights. Schema messages are translation keys (FormMessage).
 */

const YEAR_MIN = 1870;
const YEAR_MAX = 2200;
const RUNTIME_MAX = 2000;
const TOO_LONG = "titles.validation.tooLong";

/** "SA, eg سعودية" → ["SA", "EG"]: two-letter codes separated by commas or spaces. */
export function parseCountries(value: string): string[] {
  const codes = value
    .split(/[\s,،]+/u)
    .map((code) => code.trim().toUpperCase())
    .filter((code) => code !== "");
  return [...new Set(codes)];
}

const yearField = z
  .string()
  .trim()
  .refine(
    (value) =>
      value === "" ||
      (/^\d{4}$/u.test(value) && Number(value) >= YEAR_MIN && Number(value) <= YEAR_MAX),
    "titles.validation.year",
  )
  .transform((value) => (value === "" ? null : Number(value)));

export const metadataSchema = z.object({
  title: z.string().trim().min(1, "titles.validation.titleRequired").max(255, TOO_LONG),
  title_ar: z.string().trim().max(255, TOO_LONG),
  original_title: z.string().trim().max(255, TOO_LONG),
  overview: z.string().trim(),
  overview_ar: z.string().trim(),
  tagline: z.string().trim().max(500, TOO_LONG),
  tagline_ar: z.string().trim().max(500, TOO_LONG),
  year: yearField,
  release_date: z.string(),
  runtime_min: z
    .string()
    .trim()
    .refine(
      (value) =>
        value === "" ||
        (/^\d{1,4}$/u.test(value) && Number(value) >= 1 && Number(value) <= RUNTIME_MAX),
      "titles.validation.runtime",
    )
    .transform((value) => (value === "" ? null : Number(value))),
  certification: z.string().trim().max(16, TOO_LONG),
  original_language: z
    .string()
    .trim()
    .refine((value) => value === "" || /^[a-z]{2,3}$/iu.test(value), "titles.validation.language")
    .transform((value) => value.toLowerCase()),
  countries: z
    .string()
    .refine(
      (value) => parseCountries(value).every((code) => /^[A-Z]{2}$/u.test(code)),
      "titles.validation.countries",
    )
    .transform(parseCountries),
  trailer_youtube_key: z.string().trim().max(32, TOO_LONG),
  categories: z.array(z.string()),
  genres: z.array(z.string()),
  rights_holder: z.string().trim().max(255, TOO_LONG),
  license_ref: z.string().trim().max(255, TOO_LONG),
  license_expires_at: z.string(),
});

export type MetadataInput = z.input<typeof metadataSchema>;
export type MetadataValues = z.output<typeof metadataSchema>;
export type MetadataField = keyof MetadataInput;

/** Every API field these inputs show, as its form path (for the API's field errors). */
export const METADATA_FIELD_PATHS = {
  title: "title",
  title_ar: "title_ar",
  original_title: "original_title",
  overview: "overview",
  overview_ar: "overview_ar",
  tagline: "tagline",
  tagline_ar: "tagline_ar",
  year: "year",
  release_date: "release_date",
  runtime_min: "runtime_min",
  certification: "certification",
  original_language: "original_language",
  countries: "countries",
  trailer_youtube_key: "trailer_youtube_key",
  categories: "categories",
  genres: "genres",
  rights_holder: "rights_holder",
  license_ref: "license_ref",
  license_expires_at: "license_expires_at",
} as const satisfies Record<string, MetadataField>;

/** A licence ends with the day the admin picked (UTC), as the imports do. */
export function expiryOf(value: string): string | null {
  return value ? `${value}T23:59:59Z` : null;
}

/** The date part of an API timestamp, for a date input. */
export function dateInput(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "";
}

export function LockedHint({ locked }: { locked: boolean }) {
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

export function Pair({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2">{children}</div>;
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="grid gap-4">
      <h3 id={id} className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

type TextName =
  | "title"
  | "title_ar"
  | "original_title"
  | "overview"
  | "overview_ar"
  | "tagline"
  | "tagline_ar"
  | "certification"
  | "trailer_youtube_key"
  | "rights_holder"
  | "license_ref";

/** One text input; Arabic fields are right-to-left, the others left-to-right or auto. */
export function TextField({
  name,
  locked = false,
  multiline = false,
  description,
  direction,
}: {
  name: TextName;
  locked?: boolean;
  multiline?: boolean;
  description?: string | undefined;
  direction?: "ltr" | "auto";
}) {
  const { t } = useTranslation();
  const form = useFormContext<MetadataInput>();
  const arabic = name.endsWith("_ar");
  const dir = arabic ? "rtl" : (direction ?? "ltr");
  const lang = arabic ? "ar" : direction === "auto" ? undefined : "en";
  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel className="flex items-center gap-1.5">
            {t(`titles.editor.fields.${name}`)}
            <LockedHint locked={locked} />
          </FormLabel>
          <FormControl>
            {multiline ? (
              <Textarea rows={5} dir={dir} lang={lang} {...field} />
            ) : (
              <Input autoComplete="off" dir={dir} lang={lang} {...field} />
            )}
          </FormControl>
          {description ? <FormDescription>{description}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Titles, overviews and taglines in English and Arabic, and the original title. */
export function TitleTexts({ locked }: { locked: ReadonlySet<string> }) {
  return (
    <>
      <Pair>
        <TextField name="title" locked={locked.has("title")} />
        <TextField name="title_ar" locked={locked.has("title_ar")} />
      </Pair>
      <TextField name="original_title" locked={locked.has("original_title")} direction="auto" />
      <Pair>
        <TextField name="overview" multiline locked={locked.has("overview")} />
        <TextField name="overview_ar" multiline locked={locked.has("overview_ar")} />
      </Pair>
      <Pair>
        <TextField name="tagline" locked={locked.has("tagline")} />
        <TextField name="tagline_ar" locked={locked.has("tagline_ar")} />
      </Pair>
    </>
  );
}

type ShortName = "year" | "runtime_min" | "original_language" | "release_date";

function ShortField({
  name,
  locked,
  type = "text",
  numeric = false,
  description,
}: {
  name: ShortName;
  locked: boolean;
  type?: "text" | "date";
  numeric?: boolean;
  description?: string | undefined;
}) {
  const { t } = useTranslation();
  const form = useFormContext<MetadataInput>();
  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel className="flex items-center gap-1.5">
            {t(`titles.editor.fields.${name}`)}
            <LockedHint locked={locked} />
          </FormLabel>
          <FormControl>
            <Input
              type={type}
              inputMode={numeric ? "numeric" : undefined}
              autoComplete="off"
              dir="ltr"
              className="tabular-nums"
              {...field}
            />
          </FormControl>
          {description ? <FormDescription>{description}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function regionNames(language: string): Intl.DisplayNames | null {
  try {
    return new Intl.DisplayNames([language], { type: "region" });
  } catch {
    return null;
  }
}

/** Production countries as two-letter codes, with their names in the admin's language. */
function CountriesField({ locked }: { locked: boolean }) {
  const { t, i18n } = useTranslation();
  const form = useFormContext<MetadataInput>();
  const value = useWatch({ control: form.control, name: "countries" });
  const names = useMemo(() => {
    const display = regionNames(i18n.language);
    return parseCountries(value)
      .filter((code) => /^[A-Z]{2}$/u.test(code))
      .map((code) => display?.of(code) ?? code);
  }, [value, i18n.language]);
  return (
    <FormField
      control={form.control}
      name="countries"
      render={({ field }) => (
        <FormItem>
          <FormLabel className="flex items-center gap-1.5">
            {t("titles.editor.fields.countries")}
            <LockedHint locked={locked} />
          </FormLabel>
          <FormControl>
            <Input autoComplete="off" dir="ltr" placeholder="SA, EG" {...field} />
          </FormControl>
          <FormDescription>
            {names.length > 0
              ? names.join(t("titles.listSeparator"))
              : t("titles.editor.countriesHelp")}
          </FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Year, release date, runtime, certification, original language, countries, trailer. */
export function DetailFields({
  locked,
  withDates = true,
  runtimeHelp,
}: {
  locked: ReadonlySet<string>;
  /** A movie's release date and runtime (a series has its own air dates). */
  withDates?: boolean;
  runtimeHelp?: string | undefined;
}) {
  const { t } = useTranslation();
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <ShortField name="year" numeric locked={locked.has("year")} />
        {withDates ? (
          <>
            <ShortField name="release_date" type="date" locked={locked.has("release_date")} />
            <ShortField
              name="runtime_min"
              numeric
              locked={locked.has("runtime_min")}
              description={runtimeHelp}
            />
          </>
        ) : null}
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <TextField name="certification" locked={locked.has("certification")} />
        <ShortField
          name="original_language"
          locked={locked.has("original_language")}
          description={t("titles.editor.languageHelp")}
        />
        <TextField
          name="trailer_youtube_key"
          locked={locked.has("trailer_youtube_key")}
          description={t("titles.editor.trailerHelp")}
        />
      </div>
      <CountriesField locked={locked.has("countries")} />
    </>
  );
}

function ChoiceList({
  legend,
  locked,
  loading,
  empty,
  items,
  value,
  onChange,
}: {
  legend: string;
  locked: boolean;
  loading: boolean;
  empty: string;
  items: readonly { id: string; label: string; adult?: boolean }[];
  value: readonly string[];
  onChange: (next: string[]) => void;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  return (
    <fieldset className="grid gap-2">
      <legend className="mb-2 flex items-center gap-1.5 text-ui font-medium text-foreground">
        {legend}
        <LockedHint locked={locked} />
      </legend>
      {loading ? (
        <Skeleton className="h-20 w-full" />
      ) : items.length === 0 ? (
        <p className="text-ui text-muted-foreground">{empty}</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {items.map((item) => {
            const id = `${baseId}-${item.id}`;
            return (
              <Label
                key={item.id}
                htmlFor={id}
                className="flex min-w-0 cursor-pointer items-center gap-2 rounded-input border border-border px-3 py-2 font-normal hover:bg-accent"
              >
                <Checkbox
                  id={id}
                  checked={value.includes(item.id)}
                  onCheckedChange={(next) => {
                    onChange(
                      next === true
                        ? [...value, item.id]
                        : value.filter((chosen) => chosen !== item.id),
                    );
                  }}
                />
                <span className="truncate">{item.label}</span>
                {item.adult ? (
                  <Badge tone="danger" className="ms-auto shrink-0">
                    {t("titles.editor.adult")}
                  </Badge>
                ) : null}
              </Label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}

/** The categories of the title's kind; adult ones are marked (they're opt-in for customers). */
export function CategoryChoices({
  kind,
  locked,
  description,
}: {
  kind: "vod" | "series";
  locked: boolean;
  description?: string | undefined;
}) {
  const { t, i18n } = useTranslation();
  const form = useFormContext<MetadataInput>();
  const query = useCategoriesList({ kind, page_size: 100 });
  return (
    <FormField
      control={form.control}
      name="categories"
      render={({ field }) => (
        <FormItem>
          <ChoiceList
            legend={t("titles.editor.categories")}
            locked={locked}
            loading={query.isPending}
            empty={t("titles.editor.noCategories")}
            items={(query.data?.results ?? []).map((category) => ({
              id: category.id,
              label: localName(category, i18n.language),
              adult: category.is_adult,
            }))}
            value={field.value}
            onChange={field.onChange}
          />
          {description ? <FormDescription>{description}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

export function GenreChoices({ locked }: { locked: boolean }) {
  const { t, i18n } = useTranslation();
  const form = useFormContext<MetadataInput>();
  const query = useGenresList({ page_size: 100 });
  return (
    <FormField
      control={form.control}
      name="genres"
      render={({ field }) => (
        <FormItem>
          <ChoiceList
            legend={t("titles.editor.genres")}
            locked={locked}
            loading={query.isPending}
            empty={t("titles.editor.noGenres")}
            items={(query.data?.results ?? []).map((genre) => ({
              id: genre.id,
              label: localName(genre, i18n.language),
            }))}
            value={field.value}
            onChange={field.onChange}
          />
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Who holds the rights, the licence reference and when it ends (SPEC §1.1). */
export function RightsFields({
  locked,
  required = false,
}: {
  locked: ReadonlySet<string>;
  required?: boolean;
}) {
  const { t } = useTranslation();
  const form = useFormContext<MetadataInput>();
  return (
    <>
      <Pair>
        <TextField
          name="rights_holder"
          direction="auto"
          locked={locked.has("rights_holder")}
          description={required ? t("titles.editor.rightsHolderHelp") : undefined}
        />
        <TextField name="license_ref" direction="auto" locked={locked.has("license_ref")} />
      </Pair>
      <FormField
        control={form.control}
        name="license_expires_at"
        render={({ field }) => (
          <FormItem>
            <FormLabel className="flex items-center gap-1.5">
              {t("titles.editor.fields.license_expires_at")}
              <LockedHint locked={locked.has("license_expires_at")} />
            </FormLabel>
            <FormControl>
              <Input type="date" dir="ltr" className="w-48" {...field} />
            </FormControl>
            <FormDescription>{t("titles.editor.expiryHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    </>
  );
}
