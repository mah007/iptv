import { TranscodeJobsListBackend, TranscodeJobsListStatusItem } from "@smart-iptv/api";

import { compact, enumParam, intParam } from "../../lib/search";

/** Transcode jobs list state in the URL. */
export function parseTranscodeSearch(raw: Record<string, unknown>) {
  return compact({
    status: enumParam(raw.status, Object.values(TranscodeJobsListStatusItem)),
    backend: enumParam(raw.backend, Object.values(TranscodeJobsListBackend)),
    page: intParam(raw.page),
  });
}

export type TranscodeSearch = ReturnType<typeof parseTranscodeSearch>;
