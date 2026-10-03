/**
 * The HTTP client behind every generated hook (the Orval mutator).
 *
 * Same-origin by design (ADR-0004): the admin SPA and its API share a host, so the
 * session cookie travels with `credentials: "same-origin"`, and unsafe requests
 * carry Django's CSRF token, read from the `csrftoken` cookie, as `X-CSRFToken`.
 * Every failure becomes an `ApiError`, parsed from RFC 9457 problem+json when the
 * server sent one.
 */
import { ErrorCode } from "./generated/admin.schemas";

/** Failures detected in the browser, where no problem document exists. */
export type ClientErrorCode = "NETWORK_ERROR" | "UNEXPECTED_RESPONSE";
export type ApiErrorCode = ErrorCode | ClientErrorCode;
export type FieldErrors = Readonly<Record<string, readonly string[]>>;

export interface ApiErrorInit {
  status: number;
  code: ApiErrorCode;
  title: string;
  detail: string;
  fieldErrors?: FieldErrors;
  requestId?: string | null;
}

export class ApiError extends Error {
  override readonly name = "ApiError";
  /** HTTP status; 0 when the request never got a response. */
  readonly status: number;
  /** Stable code to branch on; `title` and `detail` are for people. */
  readonly code: ApiErrorCode;
  readonly title: string;
  readonly detail: string;
  /** Messages per field, with dotted paths for nested fields. */
  readonly fieldErrors: FieldErrors;
  /** The server's X-Request-ID, to quote in support requests and find in logs. */
  readonly requestId: string | null;

  constructor(init: ApiErrorInit) {
    super(init.detail);
    this.status = init.status;
    this.code = init.code;
    this.title = init.title;
    this.detail = init.detail;
    this.fieldErrors = init.fieldErrors ?? {};
    this.requestId = init.requestId ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Orval: the error type of every generated hook. */
export type ErrorType<_Body> = ApiError;

/** Sets the csrftoken cookie; called once when an unsafe request finds none. */
export const CSRF_ENDPOINT = "/api/v1/auth/csrf";
const CSRF_COOKIE = "csrftoken";
const CSRF_HEADER = "X-CSRFToken";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);
const EMPTY_BODY_STATUSES = new Set([204, 205, 304]);
const ERROR_CODES = new Set<string>(Object.values(ErrorCode));

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fieldErrorsOf(value: unknown): FieldErrors {
  if (!isRecord(value)) {
    return {};
  }
  const errors: Record<string, readonly string[]> = {};
  for (const [field, messages] of Object.entries(value)) {
    if (Array.isArray(messages)) {
      errors[field] = messages.filter((message): message is string => typeof message === "string");
    }
  }
  return errors;
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
      return new ApiError({
        status: typeof body.status === "number" ? body.status : response.status,
        code: body.code as ErrorCode,
        title: body.title,
        detail: body.detail,
        fieldErrors: fieldErrorsOf(body.field_errors),
        requestId,
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
 * `url` is an absolute path on this origin, e.g. `/api/v1/admin/settings`.
 */
export const apiFetch = async <T>(url: string, options: RequestInit = {}): Promise<T> => {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
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
