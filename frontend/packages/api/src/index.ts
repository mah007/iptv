// @smart-iptv/api: the typed admin API client. Hooks and models are generated from the
// backend's OpenAPI schema (`make api-client`); the fetcher is written by hand.
export * from "./generated/admin";
export * from "./generated/admin.schemas";
export { ApiError, CSRF_ENDPOINT, apiFetch, isApiError, readCookie } from "./fetcher";
export type {
  ApiErrorCode,
  ApiErrorInit,
  ClientErrorCode,
  ErrorType,
  FieldErrors,
} from "./fetcher";
