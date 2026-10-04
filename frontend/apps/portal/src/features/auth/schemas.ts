import { z } from "zod";

/** Messages are translation keys. Django requires 12 characters (AUTH_PASSWORD_VALIDATORS). */
export const PASSWORD_MIN_LENGTH = 12;

export const loginSchema = z.object({
  /** Username, email or phone number. */
  login: z
    .string()
    .trim()
    .min(1, "auth.validation.loginRequired")
    .max(254, "auth.validation.tooLong"),
  password: z
    .string()
    .min(1, "auth.validation.passwordRequired")
    .max(1024, "auth.validation.tooLong"),
});
export type LoginInput = z.input<typeof loginSchema>;
export type LoginValues = z.output<typeof loginSchema>;

export const forgotSchema = z.object({
  login: z
    .string()
    .trim()
    .min(1, "auth.validation.loginRequired")
    .max(254, "auth.validation.tooLong"),
});
export type ForgotValues = z.output<typeof forgotSchema>;

export const newPasswordSchema = z
  .object({
    password: z
      .string()
      .min(PASSWORD_MIN_LENGTH, "auth.validation.passwordShort")
      .max(1024, "auth.validation.tooLong"),
    confirm: z.string(),
  })
  .refine((values) => values.password === values.confirm, {
    path: ["confirm"],
    message: "auth.validation.passwordMismatch",
  });
export type NewPasswordValues = z.output<typeof newPasswordSchema>;
