/*
 * Admin-chosen IPTV app logins (Xtream). Apps put the username and password in
 * URLs (`/movie/<user>/<pass>/…`) and many don't percent-encode them, so only
 * characters that need no encoding are allowed; the API applies the same rules.
 */

export const USERNAME_PATTERN = /^[A-Za-z0-9._-]{3,32}$/u;
export const PASSWORD_PATTERN = /^[A-Za-z0-9._~@!*-]*$/u;
/** The API's `xtream.password_min_length` setting can raise this; it reports that per field. */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 64;

/** The customer's account username (not an app login): what the API accepts. */
export const ACCOUNT_USERNAME_PATTERN = /^[A-Za-z0-9._@+-]{3,150}$/u;

/**
 * Lowercase letters and digits without look-alikes (0/o, 1/l/i), like the
 * passwords the API generates: easy to read off a screen and to type with a
 * TV remote. 16 characters carry about 79 bits.
 */
const ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz";
const GENERATED_LENGTH = 16;

/** A random password the API accepts, from the browser's CSPRNG (no modulo bias). */
export function generatePassword(length: number = GENERATED_LENGTH): string {
  const limit = 256 - (256 % ALPHABET.length);
  let password = "";
  while (password.length < length) {
    for (const byte of crypto.getRandomValues(new Uint8Array(length * 2))) {
      if (byte < limit && password.length < length)
        password += ALPHABET.charAt(byte % ALPHABET.length);
    }
  }
  return password;
}
