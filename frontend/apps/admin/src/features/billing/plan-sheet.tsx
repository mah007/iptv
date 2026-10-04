import { zodResolver } from "@hookform/resolvers/zod";
import {
  getDashboardBillingQueryKey,
  getPlansListQueryKey,
  usePlansCreate,
  usePlansUpdate,
  type Plan,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
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
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { useForm, useWatch, type Control } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import { CategoryChecklist } from "../catalog/category-checklist";
import { planDuration } from "./labels";
import { majorToMinor } from "./money";
import {
  PLAN_DEFAULTS,
  PLAN_FIELD_PATHS,
  PLAN_POLICIES,
  PLAN_QUALITIES,
  planFormSchema,
  planFormValues,
  planRequest,
  type PlanFormInput,
  type PlanFormValues,
} from "./plan-schema";

type FormControlType = Control<PlanFormInput, unknown, PlanFormValues>;

function TextField({
  control,
  name,
  label,
  help,
  dir = "auto",
  className,
}: {
  control: FormControlType;
  name:
    | "code"
    | "name_en"
    | "name_ar"
    | "price"
    | "currency"
    | "bandwidth_cap_mbps"
    | "trial_limit_per_phone"
    | "duration_months"
    | "duration_days"
    | "max_streams"
    | "max_devices";
  label: string;
  help?: string;
  dir?: "ltr" | "rtl" | "auto";
  className?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input autoComplete="off" dir={dir} {...field} />
          </FormControl>
          {help ? <FormDescription>{help}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function ToggleField({
  control,
  name,
  label,
  help,
}: {
  control: FormControlType;
  name:
    | "allow_movies"
    | "allow_series"
    | "allow_live"
    | "allow_download"
    | "is_trial"
    | "active"
    | "limit_categories";
  label: string;
  help?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
          <div className="grid gap-0.5">
            <FormLabel>{label}</FormLabel>
            {help ? <FormDescription>{help}</FormDescription> : null}
          </div>
          <FormControl>
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </FormControl>
        </FormItem>
      )}
    />
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-3 text-sm font-semibold text-foreground">{title}</legend>
      {children}
    </fieldset>
  );
}

/** How the plan appears in the portal's checkout (SPEC §8.3.12 live preview). */
function CheckoutPreview({ control }: { control: FormControlType }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const values = useWatch({ control });
  const arabic = i18n.language === "ar";
  const name =
    [arabic ? values.name_ar : values.name_en, values.name_en, values.code].find(Boolean) ?? "";
  const description = (arabic ? values.description_ar : values.description_en) ?? "";
  const currency = values.currency && /^[A-Z]{3}$/u.test(values.currency) ? values.currency : "SAR";
  const minor = majorToMinor(values.price ?? "", currency);
  const months = Number(values.duration_months) || 0;
  const days = Number(values.duration_days) || 0;
  const features = [
    t("plans.preview.streams", { count: Number(values.max_streams) || 1 }),
    t("plans.preview.devices", { count: Number(values.max_devices) || 1 }),
    t(`customers.quality.${values.max_quality ?? "1080"}`),
    ...(["allow_movies", "allow_series", "allow_live"] as const)
      .filter((key) => values[key] === true)
      .map((key) => t(`customers.access.${key}`)),
  ];
  return (
    <section aria-labelledby="plan-preview" className="grid gap-2">
      <h3 id="plan-preview" className="text-xs font-medium text-muted-foreground">
        {t("plans.preview.title")}
      </h3>
      <div className="grid gap-3 rounded-card border border-primary/40 bg-card p-4 shadow-xs">
        <div className="flex items-start justify-between gap-2">
          <span className="text-base font-semibold text-foreground">
            <bdi>{name || t("plans.preview.unnamed")}</bdi>
          </span>
          {values.is_trial ? <Badge tone="violet">{t("ui:status.trial")}</Badge> : null}
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-2xl font-semibold text-foreground">
            {values.is_trial && minor === 0
              ? t("plans.preview.free")
              : minor === null
                ? "—"
                : format.money(minor, currency)}
          </span>
          <span className="text-xs text-muted-foreground">{planDuration(t, months, days)}</span>
        </div>
        {description ? (
          <p className="text-ui text-muted-foreground" dir="auto">
            {description}
          </p>
        ) : null}
        <ul className="grid gap-1.5">
          {features.map((feature) => (
            <li key={feature} className="flex items-center gap-2 text-ui text-foreground">
              <Check aria-hidden="true" className="size-4 text-primary" />
              {feature}
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">{t("plans.preview.vat")}</p>
      </div>
    </section>
  );
}

function PlanForm({ plan, onDone }: { plan: Plan | null; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const form = useForm<PlanFormInput, unknown, PlanFormValues>({
    resolver: zodResolver(planFormSchema),
    defaultValues: plan ? planFormValues(plan) : PLAN_DEFAULTS,
  });
  const isTrial = useWatch({ control: form.control, name: "is_trial" });
  const limitCategories = useWatch({ control: form.control, name: "limit_categories" });
  const create = usePlansCreate();
  const update = usePlansUpdate();

  const submit = form.handleSubmit(async (values) => {
    try {
      const data = planRequest(values);
      if (plan) await update.mutateAsync({ id: plan.id, data });
      else await create.mutateAsync({ data });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getPlansListQueryKey() }),
        queryClient.invalidateQueries({ queryKey: getDashboardBillingQueryKey() }),
      ]);
      toast.success(plan ? t("plans.form.saved") : t("plans.form.created"));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, PLAN_FIELD_PATHS, t).length === 0) {
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
        <SheetBody className="grid content-start gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="grid content-start gap-6">
            {plan && plan.subscribers > 0 ? (
              <Alert tone="info" title={t("plans.form.versionTitle")}>
                {t("plans.form.versionNotice", { count: plan.subscribers })}
              </Alert>
            ) : null}
            <Section title={t("plans.form.identity")}>
              <TextField
                control={form.control}
                name="code"
                label={t("plans.fields.code")}
                help={t("plans.fields.codeHelp")}
                dir="ltr"
              />
              <div className="grid items-start gap-3 sm:grid-cols-2">
                <TextField
                  control={form.control}
                  name="name_en"
                  label={t("plans.fields.nameEn")}
                  dir="ltr"
                />
                <TextField
                  control={form.control}
                  name="name_ar"
                  label={t("plans.fields.nameAr")}
                  dir="rtl"
                />
              </div>
              <div className="grid items-start gap-3 sm:grid-cols-2">
                {(["description_en", "description_ar"] as const).map((name) => (
                  <FormField
                    key={name}
                    control={form.control}
                    name={name}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          {name === "description_en"
                            ? t("plans.fields.descriptionEn")
                            : t("plans.fields.descriptionAr")}
                        </FormLabel>
                        <FormControl>
                          <Textarea
                            rows={3}
                            dir={name === "description_en" ? "ltr" : "rtl"}
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ))}
              </div>
            </Section>
            <Section title={t("plans.form.price")}>
              <div className="grid items-start gap-3 sm:grid-cols-3">
                <TextField
                  control={form.control}
                  name="price"
                  label={t("plans.fields.price")}
                  help={t("plans.fields.priceHelp")}
                  dir="ltr"
                />
                <TextField
                  control={form.control}
                  name="currency"
                  label={t("plans.fields.currency")}
                  dir="ltr"
                />
              </div>
              <div className="grid items-start gap-3 sm:grid-cols-3">
                <TextField
                  control={form.control}
                  name="duration_months"
                  label={t("plans.fields.months")}
                  dir="ltr"
                />
                <TextField
                  control={form.control}
                  name="duration_days"
                  label={t("plans.fields.days")}
                  dir="ltr"
                />
              </div>
            </Section>
            <Section title={t("plans.form.limits")}>
              <div className="grid items-start gap-3 sm:grid-cols-3">
                <TextField
                  control={form.control}
                  name="max_streams"
                  label={t("customers.access.maxStreams")}
                  dir="ltr"
                />
                <TextField
                  control={form.control}
                  name="max_devices"
                  label={t("customers.access.maxDevices")}
                  dir="ltr"
                />
                <FormField
                  control={form.control}
                  name="max_quality"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("customers.access.maxQuality")}</FormLabel>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {PLAN_QUALITIES.map((quality) => (
                            <SelectItem key={quality} value={quality}>
                              {t(`customers.quality.${quality}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
              </div>
              <FormField
                control={form.control}
                name="concurrency_policy"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("customers.access.policy")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {PLAN_POLICIES.map((policy) => (
                          <SelectItem key={policy} value={policy}>
                            {t(`customers.policy.${policy}.title`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      {t(`customers.policy.${field.value}.description`)}
                    </FormDescription>
                  </FormItem>
                )}
              />
              <TextField
                control={form.control}
                name="bandwidth_cap_mbps"
                label={t("plans.fields.bandwidth")}
                help={t("plans.fields.bandwidthHelp")}
                dir="ltr"
                className="sm:max-w-56"
              />
            </Section>
            <Section title={t("customers.access.content")}>
              <div className="grid gap-2 sm:grid-cols-2">
                <ToggleField
                  control={form.control}
                  name="allow_movies"
                  label={t("customers.access.allow_movies")}
                />
                <ToggleField
                  control={form.control}
                  name="allow_series"
                  label={t("customers.access.allow_series")}
                />
                <ToggleField
                  control={form.control}
                  name="allow_live"
                  label={t("customers.access.allow_live")}
                />
                <ToggleField
                  control={form.control}
                  name="allow_download"
                  label={t("plans.fields.download")}
                />
              </div>
              <ToggleField
                control={form.control}
                name="limit_categories"
                label={t("customers.access.limitCategories")}
                help={t("customers.access.limitCategoriesHelp")}
              />
              {limitCategories ? (
                <FormField
                  control={form.control}
                  name="category_ids"
                  render={({ field }) => (
                    <FormItem>
                      <CategoryChecklist
                        label={t("plans.fields.categories")}
                        value={field.value}
                        onChange={field.onChange}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ) : null}
            </Section>
            <Section title={t("plans.form.availability")}>
              <ToggleField
                control={form.control}
                name="active"
                label={t("plans.fields.active")}
                help={t("plans.fields.activeHelp")}
              />
              <ToggleField
                control={form.control}
                name="is_trial"
                label={t("plans.fields.trial")}
                help={t("plans.fields.trialHelp")}
              />
              {isTrial ? (
                <TextField
                  control={form.control}
                  name="trial_limit_per_phone"
                  label={t("plans.fields.trialLimit")}
                  help={t("plans.fields.trialLimitHelp")}
                  dir="ltr"
                  className="sm:max-w-56"
                />
              ) : null}
            </Section>
          </div>
          <div className="lg:sticky lg:top-0 lg:self-start">
            <CheckoutPreview control={form.control} />
          </div>
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {plan ? t("common.save") : t("plans.form.create")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** Create a plan, or edit one. Edits apply to new subscriptions only. */
export function PlanSheet({
  plan,
  open,
  onOpenChange,
}: {
  plan: Plan | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-4xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>{plan ? t("plans.form.editTitle") : t("plans.form.newTitle")}</SheetTitle>
          <SheetDescription>{t("plans.form.description")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <PlanForm
            key={plan?.id ?? "new"}
            plan={plan}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
