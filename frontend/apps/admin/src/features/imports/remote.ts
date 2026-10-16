import {
  isApiError,
  useImportRequestsRetrieve,
  type ImportJob,
  type RemoteResult,
} from "@smart-iptv/api";
import { useFormatters, useNow } from "@smart-iptv/ui";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

/** How long the admin waits for the importer to answer a Test or a listing. */
const ANSWER_TIMEOUT_MS = 120_000;

/**
 * Poll a Test or a folder listing the importer was asked for (`request_id`) until it
 * answers (ADR-0025). `result` is null while pending; `timedOut` once it took too long.
 */
export function useRemoteAnswer(requestId: string | null, startedAt: number | null) {
  const now = useNow(1000);
  const query = useImportRequestsRetrieve(requestId ?? "", {
    query: {
      enabled: requestId !== null,
      refetchInterval: (state) => {
        if (state.state.data?.status === "done") return false;
        if (startedAt !== null && Date.now() - startedAt > ANSWER_TIMEOUT_MS) return false;
        return 1000;
      },
      retry: (count, error) => !(isApiError(error) && error.status === 404) && count < 3,
    },
  });
  const result: RemoteResult | null =
    query.data?.status === "done" ? (query.data.result ?? null) : null;
  const timedOut =
    requestId !== null &&
    result === null &&
    startedAt !== null &&
    now - startedAt > ANSWER_TIMEOUT_MS;
  return { result, pending: requestId !== null && result === null && !timedOut, timedOut, query };
}

/** The error codes of jobs and source Tests the admin translates (imports.errors.*). */
const KNOWN_ERRORS = new Set([
  "insufficient_space",
  "target_exists",
  "unsupported_type",
  "nothing_to_import",
  "auth_failed",
  "not_found",
  "unreachable",
  "timeout",
  "tls_failed",
  "unsafe_destination",
  "checksum_mismatch",
  "transfer_failed",
  "worker_lost",
  "source_missing",
  "library_missing",
  "unreadable_link",
  "too_many_items",
  "unexpected",
  "invalid_path",
]);

function names(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}

/** A Test's or a listing's failure in the admin's language. */
export function remoteErrorText(t: TFunction, code: string): string {
  return KNOWN_ERRORS.has(code) ? t(`imports.errors.${code}`) : t("imports.errors.unexpected");
}

/** Why a job failed (or is retrying), in the admin's language, with its sizes and names. */
export function useJobErrorText() {
  const { t } = useTranslation();
  const format = useFormatters();
  return (job: ImportJob): string => {
    const code = job.error_code;
    if (!code) return "";
    const params = job.error_params;
    if (code === "insufficient_space" && typeof params.needed === "number") {
      return t("imports.errors.insufficient_space_sizes", {
        needed: format.bytes(params.needed),
        reserve: format.bytes(Number(params.reserve ?? 0)),
        free: format.bytes(Number(params.free ?? 0)),
      });
    }
    if ((code === "target_exists" || code === "unsupported_type") && names(params.names)) {
      return t(`imports.errors.${code}_names`, { names: names(params.names) });
    }
    return KNOWN_ERRORS.has(code) ? t(`imports.errors.${code}`) : t("imports.errors.unexpected");
  };
}

/** Where a job's files come from: the source's name, or the link's host. */
export function jobOrigin(job: ImportJob): string {
  if (job.origin === "link") {
    try {
      return new URL(job.url).host;
    } catch {
      return job.url;
    }
  }
  return job.source_name;
}

/** What a job copies: the first item and how many more. */
export function jobWhat(t: TFunction, job: ImportJob): string {
  if (job.origin === "link") return job.file_name || job.url;
  const [first, ...rest] = job.items;
  if (first === undefined) return "";
  const name = first.path.split("/").at(-1) ?? first.path;
  return rest.length > 0 ? t("imports.queue.andMore", { name, count: rest.length }) : name;
}
