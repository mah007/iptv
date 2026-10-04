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

export function intParam(
  value: unknown,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
): number | undefined {
  const number = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  if (typeof number !== "number" || !Number.isInteger(number)) return undefined;
  return number >= min && number <= max ? number : undefined;
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
