import {
  LibraryKind,
  ProcessingPolicy,
  type Library,
  type LibraryWriteRequest,
} from "@smart-iptv/api";
import { z } from "zod";

/* The library form (SPEC §8.3.9). Messages are translation keys. */

export const LIBRARY_KINDS = Object.values(LibraryKind);
export const POLICIES = Object.values(ProcessingPolicy);
/** The API's bounds for the reconciliation scan interval, in minutes. */
export const SCAN_INTERVAL_MAX = 10_080;

export const libraryFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "libraries.validation.nameRequired")
    .max(100, "libraries.validation.nameTooLong"),
  kind: z.enum(LibraryKind),
  path: z
    .string()
    .trim()
    .min(1, "libraries.validation.pathRequired")
    .max(500, "libraries.validation.pathTooLong"),
  processing_policy: z.enum(ProcessingPolicy),
  scan_interval_min: z
    .string()
    .trim()
    .regex(/^\d+$/u, "libraries.validation.interval")
    .transform(Number)
    .pipe(
      z
        .number()
        .min(1, "libraries.validation.interval")
        .max(SCAN_INTERVAL_MAX, "libraries.validation.interval"),
    ),
  enabled: z.boolean(),
});

export type LibraryFormInput = z.input<typeof libraryFormSchema>;
export type LibraryFormValues = z.output<typeof libraryFormSchema>;

export const LIBRARY_DEFAULTS: LibraryFormInput = {
  name: "",
  kind: "movies",
  path: "/media/",
  processing_policy: "ingest",
  scan_interval_min: "15",
  enabled: true,
};

export function libraryFormValues(library: Library): LibraryFormInput {
  return {
    name: library.name,
    kind: library.kind,
    path: library.path,
    processing_policy: library.processing_policy,
    scan_interval_min: String(library.scan_interval_min),
    enabled: library.enabled,
  };
}

export function libraryRequest(values: LibraryFormValues): LibraryWriteRequest {
  return { ...values };
}

/** API field → form field, for VALIDATION_ERROR messages. */
export const LIBRARY_FIELD_PATHS = {
  name: "name",
  kind: "kind",
  path: "path",
  processing_policy: "processing_policy",
  scan_interval_min: "scan_interval_min",
  enabled: "enabled",
} as const;
