import { isApiError } from "@smart-iptv/api-portal";

/** Codes with their own sign-in message under `auth.errors.*`. */
const AUTH_CODES = new Set([
  "INVALID_CREDENTIALS",
  "ACCOUNT_LOCKED",
  "ACCOUNT_SUSPENDED",
  "RATE_LIMITED",
  "NETWORK_ERROR",
]);

/** Translation key for a failed sign-in or password request. */
export function authErrorKey(error: unknown): string {
  if (isApiError(error) && AUTH_CODES.has(error.code)) return `auth.errors.${error.code}`;
  return "auth.errors.UNEXPECTED";
}

/** Django's password validator codes (and the link's), translated under `auth.passwordErrors.*`. */
const PASSWORD_CODES = new Set([
  "password_too_short",
  "password_too_similar",
  "password_too_common",
  "password_entirely_numeric",
]);

/** What went wrong with a new password from a link: the link itself, or the password's rules. */
export function resetErrorKeys(error: unknown): { link: boolean; password: string | null } {
  if (!isApiError(error) || error.code !== "VALIDATION_ERROR") {
    return { link: false, password: null };
  }
  const tokenCodes = error.fieldErrorCodes.token ?? [];
  if (tokenCodes.length > 0) return { link: true, password: null };
  const code = error.fieldErrorCodes.password?.[0];
  if (code === undefined) return { link: false, password: null };
  return {
    link: false,
    password: PASSWORD_CODES.has(code)
      ? `auth.passwordErrors.${code}`
      : "auth.passwordErrors.invalid",
  };
}
