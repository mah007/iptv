import { isApiError } from "@smart-iptv/api-portal";

/**
 * Why playback can't start or stopped, as the viewer should hear it (SPEC §9
 * "clear error states", §7.4's stable codes), and what they can do about it.
 * Messages live under `player.errors.<code>.{title,description}`.
 */
export type PlaybackAction = "renew" | "devices" | "plans" | "account" | "retry";

export interface PlaybackProblem {
  code: string;
  actions: readonly PlaybackAction[];
}

/** Failures seen by the player itself, after the API granted the stream. */
export type StreamFailure = "STREAM_DENIED" | "STREAM_FAILED" | "STREAM_UNSUPPORTED";

const PROBLEMS: Readonly<Record<string, readonly PlaybackAction[]>> = {
  SUBSCRIPTION_EXPIRED: ["renew"],
  ACCOUNT_SUSPENDED: ["account"],
  CONCURRENCY_LIMIT: ["devices", "retry"],
  DEVICE_BLOCKED: ["devices"],
  DEVICE_NOT_APPROVED: ["devices"],
  DEVICE_LIMIT: ["devices"],
  IP_BLOCKED: [],
  GEO_BLOCKED: [],
  CONTENT_TYPE_NOT_ALLOWED: ["plans"],
  CATEGORY_NOT_ALLOWED: ["plans"],
  QUALITY_NOT_ALLOWED: ["plans"],
  LICENSE_EXPIRED: [],
  TITLE_PREPARING: ["retry"],
  NOT_FOUND: [],
  RATE_LIMITED: ["retry"],
  NETWORK_ERROR: ["retry"],
  STREAM_DENIED: ["retry"],
  STREAM_FAILED: ["retry"],
  STREAM_UNSUPPORTED: [],
};

export function playbackProblem(error: unknown): PlaybackProblem {
  const code = isApiError(error) ? error.code : typeof error === "string" ? error : "UNEXPECTED";
  const actions = PROBLEMS[code];
  return actions === undefined ? { code: "UNEXPECTED", actions: ["retry"] } : { code, actions };
}

/** Shaka error categories and codes we tell apart (shaka.util.Error). */
const NETWORK_CATEGORY = 1;
const BAD_HTTP_STATUS = 1001;
const HTTP_ERROR = 1002;
const TIMEOUT = 1003;
const MANIFEST_CATEGORY = 4;
const MEDIA_CATEGORY = 3;

interface ShakaLikeError {
  category?: number;
  code?: number;
  data?: unknown[];
  severity?: number;
}

/** shaka.util.Error.Severity.CRITICAL: playback can't continue without help. */
const CRITICAL = 2;

/** Whether a Shaka error event ends playback (recoverable ones are retried by Shaka). */
export function isCritical(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return true;
  const { severity } = error as ShakaLikeError;
  return severity === undefined || severity === CRITICAL;
}

/**
 * A stream error in viewer terms: a refused segment (403/401: the session was
 * stopped from elsewhere, or the signed link expired) can be resumed with a
 * new grant; anything else is a playback failure.
 */
export function streamFailure(error: unknown): StreamFailure {
  if (typeof error !== "object" || error === null) return "STREAM_FAILED";
  const { category, code, data } = error as ShakaLikeError;
  if (category === NETWORK_CATEGORY && code === BAD_HTTP_STATUS) {
    const status = Array.isArray(data) ? data[1] : undefined;
    if (status === 401 || status === 403 || status === 410) return "STREAM_DENIED";
  }
  if (category === NETWORK_CATEGORY && (code === HTTP_ERROR || code === TIMEOUT))
    return "STREAM_FAILED";
  if (category === MEDIA_CATEGORY || category === MANIFEST_CATEGORY) return "STREAM_FAILED";
  return "STREAM_FAILED";
}

/** Whether a failed HLS load should be retried as the progressive MP4. */
export function shouldFallBackToMp4(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return true;
  const { category, code, data } = error as ShakaLikeError;
  // A refused request would be refused for the MP4 too: ask for a new grant instead.
  if (category === NETWORK_CATEGORY && code === BAD_HTTP_STATUS) {
    const status = Array.isArray(data) ? data[1] : undefined;
    return status !== 401 && status !== 403;
  }
  return true;
}
