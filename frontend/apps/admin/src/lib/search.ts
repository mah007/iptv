/**
 * Parsers for URL search params in route `validateSearch` functions. The
 * router parses values as JSON first ("?q=123" arrives as the number 123), so
 * each parser accepts both forms; junk becomes undefined (the param is dropped).
 */

export function stringParam(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

export function enumParam<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

export function intParam(value: unknown, allowed?: readonly number[]): number | undefined {
  const number = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  if (typeof number !== "number" || !Number.isInteger(number)) return undefined;
  if (allowed !== undefined && !allowed.includes(number)) return undefined;
  return number;
}

export function booleanParam(value: unknown): true | undefined {
  return value === true || value === "true" || value === 1 || value === "1" ? true : undefined;
}

/** "YYYY-MM-DD" only. */
export function dateParam(value: unknown): string | undefined {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/u.test(value) ? value : undefined;
}

/**
 * Drop undefined values, so a parsed search object has only the params that
 * are set: clean URLs, and optional keys under exactOptionalPropertyTypes.
 */
export function compact<T extends Record<string, unknown>>(
  values: T,
): { [K in keyof T]?: Exclude<T[K], undefined> } {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined)) as {
    [K in keyof T]?: Exclude<T[K], undefined>;
  };
}

/** Changes to a search object: a key set to undefined removes that param. */
export type SearchPatch<T> = { [K in keyof T]?: T[K] | undefined };
