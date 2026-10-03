import { z } from "zod";

/**
 * Sign-in forms (contract: plan §3.1, `POST /api/v1/auth/login` and
 * `POST /api/v1/auth/mfa/verify`). Messages are translation keys.
 */
export const loginSchema = z.object({
  /** Username or email. */
  login: z
    .string()
    .trim()
    .min(1, "auth.validation.loginRequired")
    .max(254, "auth.validation.loginTooLong"),
  password: z
    .string()
    .min(1, "auth.validation.passwordRequired")
    .max(1024, "auth.validation.passwordTooLong"),
});

export type LoginInput = z.input<typeof loginSchema>;
export type LoginValues = z.output<typeof loginSchema>;

/** Six-digit TOTP code; spaces (as in "123 456") are ignored. */
export const mfaCodeSchema = z.object({
  code: z
    .string()
    .transform((value) => value.replace(/\s+/gu, ""))
    .pipe(
      z
        .string()
        .min(1, "auth.validation.codeRequired")
        .regex(/^\d{6}$/u, "auth.validation.codeFormat"),
    ),
});

export type MfaCodeInput = z.input<typeof mfaCodeSchema>;
export type MfaCodeValues = z.output<typeof mfaCodeSchema>;

/** The base32 secret inside an otpauth:// URI, for manual entry; null if absent. */
export function otpauthSecret(uri: string): string | null {
  try {
    const secret = new URL(uri).searchParams.get("secret");
    return secret ? secret.toUpperCase() : null;
  } catch {
    return null;
  }
}
