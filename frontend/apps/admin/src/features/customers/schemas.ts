import type {
  AccessProfile,
  AccessProfileRequest,
  AppHint,
  ConcurrencyPolicy,
  CustomerCreateRequest,
  CredentialResetRequest,
  CustomerDetail,
  DeviceCreateRequest,
  Locale,
  MaxQuality,
  PatchedAccessProfileRequest,
  PatchedCustomerProfileRequest,
} from "@smart-iptv/api";
import { z } from "zod";

import { addMonths, endOfDayIn, isoDateIn } from "../../lib/time";
import {
  ACCOUNT_USERNAME_PATTERN,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_PATTERN,
  USERNAME_PATTERN,
} from "./credentials";

/*
 * Forms of the create-customer wizard and the customer editors. Messages are
 * translation keys; number fields are typed as text and parsed here.
 */

export const LOCALES = ["ar", "en"] as const satisfies readonly Locale[];
export const QUALITIES = ["480", "720", "1080", "2160"] as const;
export type QualityOption = (typeof QUALITIES)[number];
export const POLICIES = ["reject", "kick_oldest"] as const satisfies readonly ConcurrencyPolicy[];
export const APP_HINTS = [
  "smarters",
  "tivimate",
  "ibo",
  "xciptv",
  "ott_navigator",
  "smartone",
  "iptvnator",
  "uhf",
  "other",
] as const satisfies readonly AppHint[];

/** Access length presets, in months; "none" never ends, "custom" picks a date. */
export const EXPIRY_PRESETS = { "1m": 1, "3m": 3, "6m": 6, "12m": 12 } as const;
export const EXPIRY_CHOICES = ["1m", "3m", "6m", "12m", "none", "custom"] as const;
export type ExpiryChoice = (typeof EXPIRY_CHOICES)[number];

const LIMIT_MAX = 50;

const limit = z
  .string()
  .trim()
  .regex(/^\d+$/u, "customers.validation.wholeNumber")
  .transform(Number)
  .pipe(
    z
      .number()
      .min(1, "customers.validation.limitRange")
      .max(LIMIT_MAX, "customers.validation.limitRange"),
  );

const emailSchema = z.email();

export const profileSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "customers.validation.nameRequired")
    .max(150, "customers.validation.nameTooLong"),
  email: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || emailSchema.safeParse(value).success,
      "customers.validation.email",
    ),
  // Spaces and dashes are only for readability; the API stores E.164 (+966 by default).
  phone: z
    .string()
    .transform((value) => value.replace(/[\s-]/gu, ""))
    .pipe(
      z
        .string()
        .regex(/^\+?\d*$/u, "customers.validation.phone")
        .max(16, "customers.validation.phone"),
    ),
  locale: z.enum(LOCALES),
  notes: z.string().max(2000, "customers.validation.notesTooLong"),
});

export const accessSchema = z
  .object({
    expiry: z.enum(EXPIRY_CHOICES),
    /** YYYY-MM-DD, used when `expiry` is "custom". */
    expiryDate: z.string(),
    max_streams: limit,
    max_devices: limit,
    max_quality: z.enum(QUALITIES),
    concurrency_policy: z.enum(POLICIES),
    allow_movies: z.boolean(),
    allow_series: z.boolean(),
    allow_live: z.boolean(),
    /** Off: every category (the API's empty list). */
    limitCategories: z.boolean(),
    category_ids: z.array(z.string()),
  })
  .superRefine((access, context) => {
    if (access.expiry === "custom" && !/^\d{4}-\d{2}-\d{2}$/u.test(access.expiryDate)) {
      context.addIssue({
        code: "custom",
        path: ["expiryDate"],
        message: "customers.validation.expiryDate",
      });
    }
    if (access.limitCategories && access.category_ids.length === 0) {
      context.addIssue({
        code: "custom",
        path: ["category_ids"],
        message: "customers.validation.categories",
      });
    }
  });

/** How an IPTV app login gets its username and password. */
export const CREDENTIAL_MODES = ["generate", "manual"] as const;
export type CredentialMode = (typeof CREDENTIAL_MODES)[number];

const credentialShape = {
  credentials: z.enum(CREDENTIAL_MODES),
  username: z.string().trim(),
  password: z.string(),
};

/** "Set manually" needs both values, valid by the API's rules (it checks uniqueness). */
function checkCredentials(
  login: { credentials: CredentialMode; username: string; password: string },
  context: z.RefinementCtx,
): void {
  if (login.credentials !== "manual") return;
  const issue = (path: string, message: string) => {
    context.addIssue({ code: "custom", path: [path], message });
  };
  if (!USERNAME_PATTERN.test(login.username)) issue("username", "credential.validation.username");
  const { password } = login;
  if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    issue("password", "credential.validation.passwordLength");
  } else if (!PASSWORD_PATTERN.test(password)) {
    issue("password", "credential.validation.passwordCharacters");
  } else if (password.toLowerCase() === login.username.toLowerCase()) {
    issue("password", "credential.validation.passwordIsUsername");
  }
}

export const deviceSchema = z
  .object({
    /** Issue the first Xtream credential with the customer. */
    create: z.boolean(),
    name: z.string().trim().max(100, "customers.validation.deviceNameTooLong"),
    app_hint: z.enum(APP_HINTS),
    ...credentialShape,
  })
  .superRefine((device, context) => {
    if (device.create) checkCredentials(device, context);
  });

/** The wizard's profile adds the optional account username (generated when empty). */
const wizardProfileSchema = profileSchema.extend({
  username: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || ACCOUNT_USERNAME_PATTERN.test(value),
      "customers.validation.username",
    ),
});

export const wizardSchema = z.object({
  profile: wizardProfileSchema,
  access: accessSchema,
  device: deviceSchema,
});
export type WizardInput = z.input<typeof wizardSchema>;
export type WizardValues = z.output<typeof wizardSchema>;

export const accessFormSchema = z.object({ access: accessSchema });
export type AccessFormInput = z.input<typeof accessFormSchema>;
export type AccessFormValues = z.output<typeof accessFormSchema>;
type AccessValues = AccessFormValues["access"];

export const profileFormSchema = z.object({ profile: profileSchema });
export type ProfileFormInput = z.input<typeof profileFormSchema>;
export type ProfileFormValues = z.output<typeof profileFormSchema>;
type ProfileValues = ProfileFormValues["profile"];

export const deviceFormSchema = z.object({ device: deviceSchema });
export type DeviceFormInput = z.input<typeof deviceFormSchema>;
export type DeviceFormValues = z.output<typeof deviceFormSchema>;
type DeviceValues = DeviceFormValues["device"];

/** Resetting a login: generate a new password, or set it (and optionally a new username). */
export const resetFormSchema = z.object({
  device: z.object(credentialShape).superRefine(checkCredentials),
});
export type ResetFormInput = z.input<typeof resetFormSchema>;
export type ResetFormValues = z.output<typeof resetFormSchema>;
/** The fields `CredentialFields` edits, shared by the device and reset forms. */
export type CredentialFormInput = ResetFormInput;

export const ACCESS_DEFAULTS: AccessFormInput["access"] = {
  expiry: "1m",
  expiryDate: "",
  max_streams: "1",
  max_devices: "2",
  max_quality: "1080",
  concurrency_policy: "reject",
  allow_movies: true,
  allow_series: true,
  allow_live: true,
  limitCategories: false,
  category_ids: [],
};

export const DEVICE_DEFAULTS: DeviceFormInput["device"] = {
  create: true,
  name: "",
  app_hint: "other",
  credentials: "generate",
  username: "",
  password: "",
};

export function resetDefaults(username: string): ResetFormInput {
  return { device: { credentials: "generate", username, password: "" } };
}

export function wizardDefaults(locale: Locale): WizardInput {
  return {
    profile: { username: "", name: "", email: "", phone: "", locale, notes: "" },
    access: ACCESS_DEFAULTS,
    device: DEVICE_DEFAULTS,
  };
}

/** When access ends for the chosen preset or date (end of that day in `timeZone`); null = never. */
export function expiresAt(
  access: Pick<AccessValues, "expiry" | "expiryDate">,
  timeZone: string,
  now: Date = new Date(),
): string | null {
  if (access.expiry === "none") return null;
  if (access.expiry === "custom") return endOfDayIn(access.expiryDate, timeZone);
  return endOfDayIn(addMonths(isoDateIn(now, timeZone), EXPIRY_PRESETS[access.expiry]), timeZone);
}

function qualityOf(option: QualityOption): MaxQuality {
  return Number(option) as MaxQuality;
}

/** The whole access profile, for a new customer. */
export function accessRequest(
  access: AccessValues,
  timeZone: string,
  now?: Date,
): AccessProfileRequest {
  return {
    expires_at: expiresAt(access, timeZone, now),
    max_streams: access.max_streams,
    max_devices: access.max_devices,
    max_quality: qualityOf(access.max_quality),
    concurrency_policy: access.concurrency_policy,
    allow_movies: access.allow_movies,
    allow_series: access.allow_series,
    allow_live: access.allow_live,
    category_ids: access.limitCategories ? access.category_ids : [],
  };
}

/**
 * Only what the admin changed: an untouched expiry keeps its exact time
 * instead of being rounded to the end of its day.
 */
export function accessPatch(
  access: AccessValues,
  dirty: Partial<Record<keyof AccessValues, unknown>>,
  timeZone: string,
): PatchedAccessProfileRequest {
  const full = accessRequest(access, timeZone);
  const patch: PatchedAccessProfileRequest = {};
  if (dirty.expiry !== undefined || dirty.expiryDate !== undefined) {
    patch.expires_at = full.expires_at ?? null;
  }
  if (dirty.max_streams !== undefined) patch.max_streams = access.max_streams;
  if (dirty.max_devices !== undefined) patch.max_devices = access.max_devices;
  if (dirty.max_quality !== undefined) patch.max_quality = qualityOf(access.max_quality);
  if (dirty.concurrency_policy !== undefined) patch.concurrency_policy = access.concurrency_policy;
  if (dirty.allow_movies !== undefined) patch.allow_movies = access.allow_movies;
  if (dirty.allow_series !== undefined) patch.allow_series = access.allow_series;
  if (dirty.allow_live !== undefined) patch.allow_live = access.allow_live;
  if (dirty.limitCategories !== undefined || dirty.category_ids !== undefined) {
    patch.category_ids = full.category_ids ?? [];
  }
  return patch;
}

/** The editor's starting values for an existing profile. */
export function accessFormValues(profile: AccessProfile, timeZone: string): AccessFormInput {
  const quality = String(profile.max_quality ?? 1080);
  return {
    access: {
      expiry: profile.expires_at ? "custom" : "none",
      expiryDate: profile.expires_at ? isoDateIn(profile.expires_at, timeZone) : "",
      max_streams: String(profile.max_streams ?? 1),
      max_devices: String(profile.max_devices ?? 2),
      max_quality: (QUALITIES as readonly string[]).includes(quality)
        ? (quality as QualityOption)
        : "1080",
      concurrency_policy: profile.concurrency_policy ?? "reject",
      allow_movies: profile.allow_movies ?? true,
      allow_series: profile.allow_series ?? true,
      allow_live: profile.allow_live ?? true,
      limitCategories: profile.categories.length > 0,
      category_ids: profile.categories.map((category) => category.id),
    },
  };
}

function profileFields(profile: ProfileValues) {
  return {
    name: profile.name,
    email: profile.email,
    phone: profile.phone,
    locale: profile.locale,
    notes: profile.notes,
  };
}

/** A new device login; the API generates the username and password unless set manually. */
export function deviceRequest(device: DeviceValues): DeviceCreateRequest {
  return {
    app_hint: device.app_hint,
    ...(device.name ? { name: device.name } : {}),
    ...(device.credentials === "manual"
      ? { username: device.username, password: device.password }
      : {}),
  };
}

/** Empty body: a generated password. Manual: the chosen password, and the username if renamed. */
export function resetRequest(
  values: ResetFormValues,
  currentUsername: string | null,
): CredentialResetRequest {
  const { device } = values;
  if (device.credentials === "generate") return {};
  return {
    password: device.password,
    ...(device.username === currentUsername ? {} : { username: device.username }),
  };
}

export function createCustomerRequest(
  values: WizardValues,
  timeZone: string,
): CustomerCreateRequest {
  const { device, profile } = values;
  return {
    ...(profile.username ? { username: profile.username } : {}),
    ...profileFields(profile),
    timezone: timeZone,
    access: accessRequest(values.access, timeZone),
    device: device.create ? deviceRequest(device) : null,
  };
}

export function profileFormValues(customer: CustomerDetail): ProfileFormInput {
  return {
    profile: {
      name: customer.name,
      email: customer.email,
      phone: customer.phone,
      locale: customer.locale,
      notes: customer.notes,
    },
  };
}

export function profilePatch(values: ProfileFormValues): PatchedCustomerProfileRequest {
  return profileFields(values.profile);
}

/** API field paths of the create request → wizard form paths (for problem field_errors). */
export const WIZARD_FIELD_PATHS = {
  username: "profile.username",
  name: "profile.name",
  email: "profile.email",
  phone: "profile.phone",
  locale: "profile.locale",
  notes: "profile.notes",
  "access.expires_at": "access.expiryDate",
  "access.max_streams": "access.max_streams",
  "access.max_devices": "access.max_devices",
  "access.max_quality": "access.max_quality",
  "access.concurrency_policy": "access.concurrency_policy",
  "access.category_ids": "access.category_ids",
  "device.name": "device.name",
  "device.app_hint": "device.app_hint",
  "device.username": "device.username",
  "device.password": "device.password",
} as const;

export const DEVICE_FIELD_PATHS = {
  name: "device.name",
  app_hint: "device.app_hint",
  username: "device.username",
  password: "device.password",
} as const;

export const RESET_FIELD_PATHS = {
  username: "device.username",
  password: "device.password",
} as const;

export const ACCESS_FIELD_PATHS = {
  expires_at: "access.expiryDate",
  max_streams: "access.max_streams",
  max_devices: "access.max_devices",
  max_quality: "access.max_quality",
  concurrency_policy: "access.concurrency_policy",
  category_ids: "access.category_ids",
} as const;

export const PROFILE_FIELD_PATHS = {
  name: "profile.name",
  email: "profile.email",
  phone: "profile.phone",
  locale: "profile.locale",
  notes: "profile.notes",
} as const;
