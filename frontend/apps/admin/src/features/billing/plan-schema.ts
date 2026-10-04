import type { MaxQuality, Plan, PlanWriteRequest } from "@smart-iptv/api";
import { z } from "zod";

import { majorToMinor, minorToMajor } from "./money";

/* The plan editor (SPEC §8.3.12). Messages are translation keys. */

export const PLAN_QUALITIES = ["480", "720", "1080", "2160"] as const;
export const PLAN_POLICIES = ["reject", "kick_oldest"] as const;

const wholeNumber = (min: number, max: number, message: string) =>
  z
    .string()
    .trim()
    .regex(/^\d+$/u, message)
    .transform(Number)
    .pipe(z.number().min(min, message).max(max, message));

export const planFormSchema = z
  .object({
    code: z
      .string()
      .trim()
      .min(1, "plans.validation.code")
      .max(50, "plans.validation.code")
      .regex(/^[-a-zA-Z0-9_]+$/u, "plans.validation.code"),
    name_en: z.string().trim().min(1, "plans.validation.name").max(100, "plans.validation.name"),
    name_ar: z.string().trim().min(1, "plans.validation.name").max(100, "plans.validation.name"),
    description_en: z.string().max(2000, "plans.validation.description"),
    description_ar: z.string().max(2000, "plans.validation.description"),
    duration_months: wholeNumber(0, 120, "plans.validation.months"),
    duration_days: wholeNumber(0, 3660, "plans.validation.days"),
    price: z.string().trim(),
    currency: z
      .string()
      .trim()
      .regex(/^[A-Z]{3}$/u, "plans.validation.currency"),
    max_streams: wholeNumber(1, 50, "plans.validation.limit"),
    max_devices: wholeNumber(1, 50, "plans.validation.limit"),
    max_quality: z.enum(PLAN_QUALITIES),
    concurrency_policy: z.enum(PLAN_POLICIES),
    allow_movies: z.boolean(),
    allow_series: z.boolean(),
    allow_live: z.boolean(),
    allow_download: z.boolean(),
    bandwidth_cap_mbps: z
      .string()
      .trim()
      .regex(/^(\d+)?$/u, "plans.validation.bandwidth"),
    is_trial: z.boolean(),
    trial_limit_per_phone: z
      .string()
      .trim()
      .regex(/^(\d{1,2})?$/u, "plans.validation.trialLimit"),
    limit_categories: z.boolean(),
    category_ids: z.array(z.string()),
    active: z.boolean(),
  })
  .superRefine((values, context) => {
    if (majorToMinor(values.price, values.currency || "SAR") === null) {
      context.addIssue({ code: "custom", path: ["price"], message: "plans.validation.price" });
    }
    if (values.duration_months === 0 && values.duration_days === 0) {
      context.addIssue({
        code: "custom",
        path: ["duration_days"],
        message: "plans.validation.duration",
      });
    }
    const cap = values.bandwidth_cap_mbps === "" ? null : Number(values.bandwidth_cap_mbps);
    if (cap !== null && (cap < 1 || cap > 100_000)) {
      context.addIssue({
        code: "custom",
        path: ["bandwidth_cap_mbps"],
        message: "plans.validation.bandwidth",
      });
    }
    if (values.trial_limit_per_phone !== "" && Number(values.trial_limit_per_phone) > 10) {
      context.addIssue({
        code: "custom",
        path: ["trial_limit_per_phone"],
        message: "plans.validation.trialLimit",
      });
    }
    if (values.limit_categories && values.category_ids.length === 0) {
      context.addIssue({
        code: "custom",
        path: ["category_ids"],
        message: "customers.validation.categories",
      });
    }
  });

export type PlanFormInput = z.input<typeof planFormSchema>;
export type PlanFormValues = z.output<typeof planFormSchema>;

export const PLAN_DEFAULTS: PlanFormInput = {
  code: "",
  name_en: "",
  name_ar: "",
  description_en: "",
  description_ar: "",
  duration_months: "1",
  duration_days: "0",
  price: "",
  currency: "SAR",
  max_streams: "1",
  max_devices: "2",
  max_quality: "1080",
  concurrency_policy: "reject",
  allow_movies: true,
  allow_series: true,
  allow_live: true,
  allow_download: false,
  bandwidth_cap_mbps: "",
  is_trial: false,
  trial_limit_per_phone: "",
  limit_categories: false,
  category_ids: [],
  active: true,
};

export function planFormValues(plan: Plan): PlanFormInput {
  return {
    code: plan.code,
    name_en: plan.name_en,
    name_ar: plan.name_ar,
    description_en: plan.description_en,
    description_ar: plan.description_ar,
    duration_months: String(plan.duration_months),
    duration_days: String(plan.duration_days),
    price: minorToMajor(plan.price, plan.currency),
    currency: plan.currency,
    max_streams: String(plan.max_streams),
    max_devices: String(plan.max_devices),
    max_quality: String(plan.max_quality) as PlanFormInput["max_quality"],
    concurrency_policy: plan.concurrency_policy,
    allow_movies: plan.allow_movies,
    allow_series: plan.allow_series,
    allow_live: plan.allow_live,
    allow_download: plan.allow_download,
    bandwidth_cap_mbps: plan.bandwidth_cap_mbps === null ? "" : String(plan.bandwidth_cap_mbps),
    is_trial: plan.is_trial,
    trial_limit_per_phone:
      plan.trial_limit_per_phone === null ? "" : String(plan.trial_limit_per_phone),
    limit_categories: plan.category_ids.length > 0,
    category_ids: [...plan.category_ids],
    active: plan.active,
  };
}

export function planRequest(values: PlanFormValues): PlanWriteRequest {
  return {
    code: values.code,
    name_en: values.name_en,
    name_ar: values.name_ar,
    description_en: values.description_en.trim(),
    description_ar: values.description_ar.trim(),
    duration_months: values.duration_months,
    duration_days: values.duration_days,
    price: majorToMinor(values.price, values.currency) ?? 0,
    currency: values.currency,
    max_streams: values.max_streams,
    max_devices: values.max_devices,
    max_quality: Number(values.max_quality) as MaxQuality,
    concurrency_policy: values.concurrency_policy,
    allow_movies: values.allow_movies,
    allow_series: values.allow_series,
    allow_live: values.allow_live,
    allow_download: values.allow_download,
    bandwidth_cap_mbps: values.bandwidth_cap_mbps === "" ? null : Number(values.bandwidth_cap_mbps),
    is_trial: values.is_trial,
    trial_limit_per_phone:
      values.is_trial && values.trial_limit_per_phone !== ""
        ? Number(values.trial_limit_per_phone)
        : null,
    category_ids: values.limit_categories ? values.category_ids : [],
    active: values.active,
  };
}

/** API field → form field, for VALIDATION_ERROR messages. */
export const PLAN_FIELD_PATHS = {
  code: "code",
  name_en: "name_en",
  name_ar: "name_ar",
  description_en: "description_en",
  description_ar: "description_ar",
  duration_months: "duration_months",
  duration_days: "duration_days",
  price: "price",
  currency: "currency",
  max_streams: "max_streams",
  max_devices: "max_devices",
  max_quality: "max_quality",
  concurrency_policy: "concurrency_policy",
  bandwidth_cap_mbps: "bandwidth_cap_mbps",
  trial_limit_per_phone: "trial_limit_per_phone",
  category_ids: "category_ids",
} as const;
