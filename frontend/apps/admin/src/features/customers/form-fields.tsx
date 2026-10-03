import { useCategoriesList, type Category, type CategoryKind } from "@smart-iptv/api";
import {
  Checkbox,
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
  Skeleton,
  Switch,
  Textarea,
  cn,
  useFormatters,
} from "@smart-iptv/ui";
import { useId, useMemo, type ReactNode } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";

import {
  APP_HINTS,
  EXPIRY_CHOICES,
  LOCALES,
  POLICIES,
  QUALITIES,
  expiresAt,
  type AccessFormInput,
  type DeviceFormInput,
  type ProfileFormInput,
} from "./schemas";

/** A radio button drawn as a selectable chip or card; the whole tile is the hit area. */
function ChoiceTile({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <Label
      htmlFor={id}
      className={cn(
        "flex cursor-pointer items-center gap-2.5 rounded-input border border-border px-3 py-2 font-normal transition-colors hover:bg-accent has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5",
        className,
      )}
    >
      <RadioGroupItem id={id} value={value} />
      {children}
    </Label>
  );
}

function FieldGroup({
  title,
  description,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-3 grid gap-0.5">
        <span className="text-sm font-semibold text-foreground">{title}</span>
        {description ? <span className="text-ui text-muted-foreground">{description}</span> : null}
      </legend>
      {children}
    </fieldset>
  );
}

/** Name, contact details, language and notes. */
export function ProfileFields() {
  const { t } = useTranslation();
  const form = useFormContext<ProfileFormInput>();
  return (
    <div className="grid gap-4">
      <FormField
        control={form.control}
        name="profile.name"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("customers.fields.name")}</FormLabel>
            <FormControl>
              <Input autoComplete="off" dir="auto" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <FormField
          control={form.control}
          name="profile.email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("customers.fields.email")}</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="off" dir="ltr" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="profile.phone"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("customers.fields.phone")}</FormLabel>
              <FormControl>
                <Input
                  type="tel"
                  inputMode="tel"
                  autoComplete="off"
                  dir="ltr"
                  placeholder="+966 5x xxx xxxx"
                  {...field}
                />
              </FormControl>
              <FormDescription>{t("customers.fields.phoneHelp")}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
      <FormField
        control={form.control}
        name="profile.locale"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("customers.fields.locale")}</FormLabel>
            <FormControl>
              <RadioGroup
                value={field.value}
                onValueChange={field.onChange}
                aria-label={t("customers.fields.locale")}
                className="grid grid-cols-2 gap-2"
              >
                {LOCALES.map((locale) => (
                  <ChoiceTile key={locale} value={locale}>
                    {t(`customers.locales.${locale}`)}
                  </ChoiceTile>
                ))}
              </RadioGroup>
            </FormControl>
            <FormDescription>{t("customers.fields.localeHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="profile.notes"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("customers.fields.notes")}</FormLabel>
            <FormControl>
              <Textarea rows={3} dir="auto" {...field} />
            </FormControl>
            <FormDescription>{t("customers.fields.notesHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  );
}

const KIND_ORDER: readonly CategoryKind[] = ["vod", "series", "live"];

function categoryName(category: Pick<Category, "name_en" | "name_ar">, language: string): string {
  return language.startsWith("ar") && category.name_ar ? category.name_ar : category.name_en;
}

/** Category checkboxes grouped by kind; an empty selection is not allowed here. */
function CategoryPicker() {
  const { t, i18n } = useTranslation();
  const form = useFormContext<AccessFormInput>();
  const categories = useCategoriesList({ page_size: 100 });
  const groups = useMemo(
    () =>
      KIND_ORDER.map((kind) => ({
        kind,
        items: (categories.data?.results ?? []).filter((category) => category.kind === kind),
      })).filter((group) => group.items.length > 0),
    [categories.data],
  );

  if (categories.isPending) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-52" />
        <Skeleton className="h-4 w-36" />
      </div>
    );
  }
  if (categories.isError) {
    return <p className="text-ui text-danger-text">{t("customers.access.categoriesError")}</p>;
  }
  if (groups.length === 0) {
    return <p className="text-ui text-muted-foreground">{t("customers.access.noCategories")}</p>;
  }
  return (
    <FormField
      control={form.control}
      name="access.category_ids"
      render={({ field }) => (
        <FormItem>
          <div className="grid gap-4 rounded-input border border-border p-3 sm:grid-cols-3">
            {groups.map((group) => (
              <div key={group.kind} className="grid content-start gap-2">
                <p className="text-xs font-medium uppercase text-muted-foreground ltr:tracking-wide">
                  {t(`customers.categoryKinds.${group.kind}`)}
                </p>
                {group.items.map((category) => {
                  const checked = field.value.includes(category.id);
                  return (
                    <Label key={category.id} className="flex items-center gap-2 font-normal">
                      <Checkbox
                        checked={checked}
                        onCheckedChange={(value) => {
                          field.onChange(
                            value === true
                              ? [...field.value, category.id]
                              : field.value.filter((id) => id !== category.id),
                          );
                        }}
                      />
                      <span className="truncate">{categoryName(category, i18n.language)}</span>
                    </Label>
                  );
                })}
              </div>
            ))}
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** The manual access profile (instead of plans until the commercial slice; ADR-0006). */
export function AccessFields({ timeZone }: { timeZone: string }) {
  const { t } = useTranslation();
  const format = useFormatters(timeZone);
  const form = useFormContext<AccessFormInput>();
  const expiry = useWatch({ control: form.control, name: "access.expiry" });
  const expiryDate = useWatch({ control: form.control, name: "access.expiryDate" });
  const limitCategories = useWatch({ control: form.control, name: "access.limitCategories" });

  let endsAt: string | null | undefined;
  try {
    endsAt = expiresAt({ expiry, expiryDate }, timeZone);
  } catch {
    endsAt = undefined; // a custom date not picked yet
  }

  return (
    <div className="grid gap-7">
      <FieldGroup title={t("customers.access.length")}>
        <FormField
          control={form.control}
          name="access.expiry"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <RadioGroup
                  value={field.value}
                  onValueChange={field.onChange}
                  aria-label={t("customers.access.length")}
                  className="grid grid-cols-2 gap-2 sm:grid-cols-3"
                >
                  {EXPIRY_CHOICES.map((choice) => (
                    <ChoiceTile key={choice} value={choice}>
                      {t(`customers.access.expiry.${choice}`)}
                    </ChoiceTile>
                  ))}
                </RadioGroup>
              </FormControl>
            </FormItem>
          )}
        />
        {expiry === "custom" ? (
          <FormField
            control={form.control}
            name="access.expiryDate"
            render={({ field }) => (
              <FormItem className="sm:max-w-56">
                <FormLabel>{t("customers.access.endDate")}</FormLabel>
                <FormControl>
                  <Input type="date" dir="ltr" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ) : null}
        <p className="text-ui text-muted-foreground" aria-live="polite">
          {endsAt === null
            ? t("customers.access.neverEnds")
            : endsAt === undefined
              ? t("customers.access.pickDate")
              : t("customers.access.endsOn", { date: format.dateTime(endsAt) })}
        </p>
      </FieldGroup>

      <FieldGroup title={t("customers.access.limits")}>
        <div className="grid items-start gap-4 sm:grid-cols-2">
          <FormField
            control={form.control}
            name="access.max_streams"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("customers.access.maxStreams")}</FormLabel>
                <FormControl>
                  <Input type="number" inputMode="numeric" min={1} max={50} dir="ltr" {...field} />
                </FormControl>
                <FormDescription>{t("customers.access.maxStreamsHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="access.max_devices"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("customers.access.maxDevices")}</FormLabel>
                <FormControl>
                  <Input type="number" inputMode="numeric" min={1} max={50} dir="ltr" {...field} />
                </FormControl>
                <FormDescription>{t("customers.access.maxDevicesHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField
          control={form.control}
          name="access.max_quality"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("customers.access.maxQuality")}</FormLabel>
              <FormControl>
                <RadioGroup
                  value={field.value}
                  onValueChange={field.onChange}
                  aria-label={t("customers.access.maxQuality")}
                  className="grid grid-cols-2 gap-2"
                >
                  {QUALITIES.map((quality) => (
                    <ChoiceTile key={quality} value={quality}>
                      {t(`customers.quality.${quality}`)}
                    </ChoiceTile>
                  ))}
                </RadioGroup>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="access.concurrency_policy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("customers.access.policy")}</FormLabel>
              <FormControl>
                <RadioGroup
                  value={field.value}
                  onValueChange={field.onChange}
                  aria-label={t("customers.access.policy")}
                  className="grid gap-2 sm:grid-cols-2"
                >
                  {POLICIES.map((policy) => (
                    <ChoiceTile key={policy} value={policy} className="items-start py-2.5">
                      <span className="grid gap-0.5">
                        <span className="font-medium text-foreground">
                          {t(`customers.policy.${policy}.title`)}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {t(`customers.policy.${policy}.description`)}
                        </span>
                      </span>
                    </ChoiceTile>
                  ))}
                </RadioGroup>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </FieldGroup>

      <FieldGroup title={t("customers.access.content")}>
        <div className="grid gap-2 sm:grid-cols-3">
          {(["allow_movies", "allow_series", "allow_live"] as const).map((name) => (
            <FormField
              key={name}
              control={form.control}
              name={`access.${name}`}
              render={({ field }) => (
                <FormItem className="flex items-center justify-between gap-3 rounded-input border border-border px-3 py-2">
                  <FormLabel className="font-normal">{t(`customers.access.${name}`)}</FormLabel>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
          ))}
        </div>
        <FormField
          control={form.control}
          name="access.limitCategories"
          render={({ field }) => (
            <FormItem className="flex items-start justify-between gap-3">
              <div className="grid gap-0.5">
                <FormLabel>{t("customers.access.limitCategories")}</FormLabel>
                <FormDescription>{t("customers.access.limitCategoriesHelp")}</FormDescription>
              </div>
              <FormControl>
                <Switch checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
            </FormItem>
          )}
        />
        {limitCategories ? <CategoryPicker /> : null}
      </FieldGroup>
    </div>
  );
}

/** Name and app of an IPTV device; `optional` adds the "issue it now" switch (wizard). */
export function DeviceFields({ optional = false }: { optional?: boolean }) {
  const { t } = useTranslation();
  const form = useFormContext<DeviceFormInput>();
  const create = useWatch({ control: form.control, name: "device.create" });
  return (
    <div className="grid gap-4">
      {optional ? (
        <FormField
          control={form.control}
          name="device.create"
          render={({ field }) => (
            <FormItem className="flex items-start justify-between gap-3 rounded-input border border-border px-3 py-2.5">
              <div className="grid gap-0.5">
                <FormLabel>{t("devices.form.createNow")}</FormLabel>
                <FormDescription>{t("devices.form.createNowHelp")}</FormDescription>
              </div>
              <FormControl>
                <Switch checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
            </FormItem>
          )}
        />
      ) : null}
      {create ? (
        <>
          <FormField
            control={form.control}
            name="device.name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("devices.form.name")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="off"
                    dir="auto"
                    placeholder={t("devices.form.namePlaceholder")}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="device.app_hint"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("devices.form.app")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {APP_HINTS.map((hint) => (
                      <SelectItem key={hint} value={hint}>
                        {t(`devices.apps.${hint}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{t("devices.form.appHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <p className="rounded-input bg-muted/60 px-3 py-2 text-ui text-muted-foreground">
            {t("devices.form.generated")}
          </p>
        </>
      ) : null}
    </div>
  );
}
