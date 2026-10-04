// @smart-iptv/api-portal: the typed customer API client for the portal. Hooks and models are
// generated from the backend's OpenAPI schema (`make api-client`); the fetcher is written by hand.
export * from "./generated/portal";
export * from "./generated/portal.schemas";
export { ApiError, CSRF_ENDPOINT, apiFetch, isApiError, readCookie } from "./fetcher";
export type {
  ApiErrorCode,
  ApiErrorInit,
  ClientErrorCode,
  ErrorType,
  FieldErrorCodes,
  FieldErrors,
} from "./fetcher";
