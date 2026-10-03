/**
 * problem+json codes the sign-in endpoints return (plan §2.2/§3.1), plus
 * UNAVAILABLE for network failures and server errors.
 */
export const AUTH_ERROR_CODES = [
  "INVALID_CREDENTIALS",
  "ACCOUNT_LOCKED",
  "RATE_LIMITED",
  "MFA_INVALID",
  "NOT_AUTHENTICATED",
  "UNAVAILABLE",
] as const;

export type AuthErrorCode = (typeof AUTH_ERROR_CODES)[number];

function isAuthErrorCode(code: string): code is AuthErrorCode {
  return (AUTH_ERROR_CODES as readonly string[]).includes(code);
}

/**
 * Translation key for an error code. Unknown codes (e.g. INTERNAL_ERROR) read
 * as "unavailable": the admin can't fix them by retyping.
 */
export function authErrorKey(code: string): `auth.errors.${AuthErrorCode}` {
  return `auth.errors.${isAuthErrorCode(code) ? code : "UNAVAILABLE"}`;
}
