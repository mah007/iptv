/**
 * The HTTP client behind every generated portal hook (the Orval mutator).
 *
 * Same-origin by design (ADR-0004, ADR-0013): the portal SPA and its API share
 * app.<domain>, so the session cookie travels with `credentials: "same-origin"`, and
 * unsafe requests carry Django's CSRF token, read from the `csrftoken` cookie, as
 * `X-CSRFToken`. Requests name the UI language in `Accept-Language` (the document's
 * `lang`), so titles, genres and row names come back in Arabic or English to match.
 * Every failure becomes an `ApiError`, parsed from RFC 9457 problem+json when the
 * server sent one.
 */
import { ErrorCode } from "./generated/portal.schemas";
import type { Problem } from "./generated/portal.schemas";

/** Failures detected in the browser, where no problem document exists. */
export type ClientErrorCode = "NETWORK_ERROR" | "UNEXPECTED_RESPONSE";
export type ApiErrorCode = ErrorCode | ClientErrorCode;
export type FieldErrors = Readonly<Record<string, readonly string[]>>;
/** One stable code per message of `FieldErrors`, same keys and order (ADR-0015). */
export type FieldErrorCodes = Readonly<Record<string, readonly string[]>>;

export interface ApiErrorInit<Body = Problem> {
  status: number;
  code: ApiErrorCode;
  title: string;
  detail: string;
  fieldErrors?: FieldErrors;
  fieldErrorCodes?: FieldErrorCodes;
  requestId?: string | null;
  problem?: Body | null;
}

export class ApiError<Body = Problem> extends Error {
  override readonly name = "ApiError";
  /** HTTP status; 0 when the request never got a response. */
  readonly status: number;
  /** Stable code to branch on (CONCURRENCY_LIMIT, SUBSCRIPTION_EXPIRED, ...). */
  readonly code: ApiErrorCode;
  readonly title: string;
  readonly detail: string;
  /** Messages per field, with dotted paths for nested fields. */
  readonly fieldErrors: FieldErrors;
  /** Stable codes per field, to translate instead of showing `fieldErrors`. */
  readonly fieldErrorCodes: FieldErrorCodes;
  /** The server's X-Request-ID, to quote in support requests and find in logs. */
  readonly requestId: string | null;
  /** The problem document as received; null when the server sent none. */
  readonly problem: Body | null;

  constructor(init: ApiErrorInit<Body>) {
    super(init.detail);
    this.status = init.status;
    this.code = init.code;
    this.title = init.title;
    this.detail = init.detail;
    this.fieldErrors = init.fieldErrors ?? {};
    this.fieldErrorCodes = init.fieldErrorCodes ?? {};
    this.requestId = init.requestId ?? null;
    this.problem = init.problem ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Orval: the error type of every generated hook (`Body` is the declared error schema). */
export type ErrorType<Body> = ApiError<Body>;

/** Sets the csrftoken cookie; called once when an unsafe request finds none. */
export const CSRF_ENDPOINT = "/api/v1/auth/csrf";
const CSRF_COOKIE = "csrftoken";
const CSRF_HEADER = "X-CSRFToken";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);
const EMPTY_BODY_STATUSES = new Set([204, 205, 304]);
const ERROR_CODES = new Set<string>(Object.values(ErrorCode));
const LANGUAGES = new Set(["ar", "en"]);

export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const cookie = part.trim();
    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(cookie.slice(prefix.length));
    }
  }
  return null;
}

let csrfBootstrap: Promise<void> | null = null;

async function csrfToken(): Promise<string | null> {
  const token = readCookie(CSRF_COOKIE);
  if (token !== null) {
    return token;
  }
  csrfBootstrap ??= fetch(CSRF_ENDPOINT, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  }).then(
    () => undefined,
    () => undefined,
  );
  try {
    await csrfBootstrap;
  } finally {
    csrfBootstrap = null;
  }
  return readCookie(CSRF_COOKIE);
}

/** The UI language (`<html lang>`), when it is one the API speaks. */
function uiLanguage(): string | null {
  const lang = document.documentElement.lang.toLowerCase().split("-")[0] ?? "";
  return LANGUAGES.has(lang) ? lang : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringLists(value: unknown): Record<string, readonly string[]> {
  if (!isRecord(value)) {
    return {};
  }
  const lists: Record<string, readonly string[]> = {};
  for (const [field, items] of Object.entries(value)) {
    if (Array.isArray(items)) {
      lists[field] = items.filter((item): item is string => typeof item === "string");
    }
  }
  return lists;
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get("X-Request-ID");
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.includes("json")) {
    const body: unknown = await response.json().catch(() => null);
    if (
      isRecord(body) &&
      typeof body.code === "string" &&
      ERROR_CODES.has(body.code) &&
      typeof body.title === "string" &&
      typeof body.detail === "string"
    ) {
      const problem = body as unknown as Problem;
      return new ApiError({
        status: typeof body.status === "number" ? body.status : response.status,
        code: problem.code,
        title: problem.title,
        detail: problem.detail,
        fieldErrors: stringLists(body.field_errors),
        fieldErrorCodes: stringLists(body.field_error_codes),
        requestId,
        problem,
      });
    }
  }
  // Not a problem document: a proxy error page, an outage, a non-API route.
  return new ApiError({
    status: response.status,
    code: "UNEXPECTED_RESPONSE",
    title: response.statusText || "Unexpected response",
    detail: `The server answered with HTTP ${String(response.status)}.`,
    requestId,
  });
}

async function parseBody(response: Response): Promise<unknown> {
  if (EMPTY_BODY_STATUSES.has(response.status)) {
    return undefined;
  }
  const text = await response.text();
  if (text === "") {
    return undefined;
  }
  const contentType = response.headers.get("Content-Type") ?? "";
  return contentType.includes("json") ? (JSON.parse(text) as unknown) : text;
}

/**
 * Orval mutator: performs the request for a generated hook or function.
 * `url` is an absolute path on this origin, e.g. `/api/v1/home`.
 */
export const apiFetch = async <T>(url: string, options: RequestInit = {}): Promise<T> => {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  const language = uiLanguage();
  if (language !== null && !headers.has("Accept-Language")) {
    headers.set("Accept-Language", language);
  }
  if (!SAFE_METHODS.has(method)) {
    const token = await csrfToken();
    if (token !== null) {
      headers.set(CSRF_HEADER, token);
    }
  }

  let response: Response;
  try {
    response = await fetch(url, { ...options, method, headers, credentials: "same-origin" });
  } catch (error) {
    // Cancellation is not a failure: TanStack Query must see the AbortError itself.
    if (options.signal?.aborted === true) {
      throw error;
    }
    throw new ApiError({
      status: 0,
      code: "NETWORK_ERROR",
      title: "Network error",
      detail: "The server could not be reached.",
    });
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await parseBody(response)) as T;
};
