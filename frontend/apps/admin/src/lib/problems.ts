import { isApiError, type ApiError } from "@smart-iptv/api";
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

/*
 * Field errors in the admin's language (ADR-0015). Problems carry
 * `field_error_codes` beside `field_errors`: the same keys and one stable code
 * per message. A code with a message under `fieldErrors.<code>` is shown in the
 * admin's language; any other code (including the generic `invalid`) shows the
 * API's message, which is English.
 *
 * Numbers (lengths, limits) come from the form when it knows them, else from
 * the numbers in the API's message, in order: the message for `password_length`
 * is "Use 10 to 64 characters.", whose numbers are the current setting.
 */

/** Numbers for one field's messages, e.g. `{ max: 150 }`. */
export type FieldErrorParams = Readonly<Record<string, number>>;

/**
 * Codes the admin translates, each with the numbers its message needs (in the
 * order they appear in the API's English message). Each needs
 * `fieldErrors.<code>` in both locales.
 */
export const FIELD_CODE_PARAMS: Readonly<Record<string, readonly string[]>> = {
  required: [],
  blank: [],
  null: [],
  invalid_choice: [],
  max_length: ["max"],
  min_length: ["min"],
  max_value: ["max"],
  min_value: ["min"],
  unique: [],
  does_not_exist: [],
  username_taken: [],
  username_rule: [],
  customer_username_rule: [],
  password_length: ["min", "max"],
  password_characters: [],
  password_equals_username: [],
  email_taken: [],
  invalid_phone: [],
  invalid_timezone: [],
  invalid_ip: [],
  invalid_cidr: [],
  invalid_country: [],
  not_a_customer: [],
  not_in_future: [],
  role_name_rule: [],
  role_name_taken: [],
};

/** The numbers in a message, in order ("Use 10 to 64 characters." → [10, 64]). */
export function numbersIn(message: string): number[] {
  return (message.match(/\d+(?:\.\d+)?/gu) ?? []).map(Number);
}

/** The codes the API sent for a field, aligned with its messages (may be empty). */
export function fieldErrorCodes(error: ApiError, field: string): readonly string[] {
  const codes = error.problem?.field_error_codes?.[field];
  return Array.isArray(codes) ? codes : [];
}

/**
 * One field message in the admin's language: the translation of `code` when the
 * admin knows it and has every number it needs, else the API's own message.
 */
export function fieldErrorMessage(
  t: TFunction,
  code: string | undefined,
  message: string,
  params: FieldErrorParams = {},
): string {
  if (code === undefined || !Object.hasOwn(FIELD_CODE_PARAMS, code)) return message;
  const names = FIELD_CODE_PARAMS[code] ?? [];
  const numbers = numbersIn(message);
  const values: Record<string, number> = {};
  for (const [index, name] of names.entries()) {
    const value = params[name] ?? numbers[index];
    if (value === undefined) return message;
    values[name] = value;
  }
  return t(`fieldErrors.${code}`, values);
}

/** Every message of a VALIDATION_ERROR, translated: for forms without per-field inputs. */
export function translatedFieldErrors(t: TFunction, error: unknown): string[] {
  if (!isApiError(error) || error.code !== "VALIDATION_ERROR") return [];
  return Object.entries(error.fieldErrors).flatMap(([field, messages]) => {
    const codes = fieldErrorCodes(error, field);
    return messages.map((message, index) => fieldErrorMessage(t, codes[index], message));
  });
}

/**
 * Show a VALIDATION_ERROR's field messages on the form, in the admin's language.
 * `fields` maps API field paths (dotted for nested objects, e.g.
 * `access.max_streams`) to form paths; unmapped fields are ignored. `params`
 * gives the numbers a form field's messages may need, by API field path.
 * Returns the form paths that got an error, in the order of `fields`, so a
 * wizard can jump to the first one.
 */
export function applyFieldErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: Readonly<Record<string, Path<T>>>,
  t: TFunction,
  params: Readonly<Record<string, FieldErrorParams>> = {},
): Path<T>[] {
  if (!isApiError(error) || error.code !== "VALIDATION_ERROR") return [];
  const applied: Path<T>[] = [];
  for (const [apiField, formField] of Object.entries(fields)) {
    const first = error.fieldErrors[apiField]?.[0];
    if (first === undefined) continue;
    const message = fieldErrorMessage(
      t,
      fieldErrorCodes(error, apiField)[0],
      first,
      params[apiField],
    );
    setError(formField, { type: "server", message }, { shouldFocus: applied.length === 0 });
    applied.push(formField);
  }
  return applied;
}
