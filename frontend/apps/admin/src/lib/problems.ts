import { isApiError } from "@smart-iptv/api";
import { toast } from "@smart-iptv/ui";
import type { TFunction } from "i18next";
import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

/** Error codes with their own message under `errors.*`; anything else is "unexpected". */
const MESSAGE_CODES = new Set([
  "VALIDATION_ERROR",
  "NOT_AUTHENTICATED",
  "PERMISSION_DENIED",
  "NOT_FOUND",
  "RATE_LIMITED",
  "CONFLICT",
  "DEVICE_LIMIT",
  "NETWORK_ERROR",
]);

/** Translation key that explains a failed request to the admin. */
export function errorMessageKey(error: unknown): string {
  if (isApiError(error) && MESSAGE_CODES.has(error.code)) return `errors.${error.code}`;
  return "errors.UNEXPECTED";
}

/** The API's own explanation (problem `detail`) when it is specific enough to show. */
function problemDetail(error: unknown): string | undefined {
  if (!isApiError(error) || error.problem === null) return undefined;
  if (error.status >= 500 || error.code === "VALIDATION_ERROR") return undefined;
  return error.detail || undefined;
}

/** Toast for a failed action: a translated summary plus the API's detail when useful. */
export function notifyError(t: TFunction, error: unknown): void {
  const description = problemDetail(error);
  toast.error(t(errorMessageKey(error)), description === undefined ? undefined : { description });
}

/**
 * Show a VALIDATION_ERROR's field messages on the form. `fields` maps API
 * field paths (dotted for nested objects, e.g. `access.max_streams`) to form
 * paths; unmapped fields are ignored. Returns the form paths that got an
 * error, in the order of `fields`, so a wizard can jump to the first one.
 */
export function applyFieldErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: Readonly<Record<string, Path<T>>>,
): Path<T>[] {
  if (!isApiError(error) || error.code !== "VALIDATION_ERROR") return [];
  const applied: Path<T>[] = [];
  for (const [apiField, formField] of Object.entries(fields)) {
    const messages = error.fieldErrors[apiField];
    const first = messages?.[0];
    if (first === undefined) continue;
    setError(formField, { type: "server", message: first }, { shouldFocus: applied.length === 0 });
    applied.push(formField);
  }
  return applied;
}
