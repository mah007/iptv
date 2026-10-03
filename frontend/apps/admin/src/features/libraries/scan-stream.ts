import {
  getLibrariesListQueryKey,
  getLibrariesRetrieveUrl,
  getMoviesListQueryKey,
  getReviewQueueListQueryKey,
  getScansListQueryKey,
  getSeriesListQueryKey,
  type ScanJob,
  type ScanStatus,
} from "@smart-iptv/api";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useEventStream, type StreamState } from "../../lib/event-stream";

/** What the scan stream sends: a scan job's state (`apps.library.services.job_event`). */
export type ScanEvent = Pick<
  ScanJob,
  | "id"
  | "trigger"
  | "status"
  | "path"
  | "found"
  | "new"
  | "changed"
  | "moved"
  | "removed"
  | "errors"
  | "started_at"
  | "finished_at"
> & { library_id: string };

/** The counters a scan card shows, from the REST job or a stream event. */
export type ScanProgress = Omit<ScanEvent, "library_id">;

const ACTIVE: readonly ScanStatus[] = ["queued", "running"];

export function isActiveScan(scan: { status: ScanStatus } | null | undefined): boolean {
  return scan !== null && scan !== undefined && ACTIVE.includes(scan.status);
}

function isScanEvent(data: unknown): data is ScanEvent {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { id?: unknown }).id === "string" &&
    typeof (data as { status?: unknown }).status === "string" &&
    typeof (data as { found?: unknown }).found === "number"
  );
}

/** `GET /api/v1/admin/libraries/{id}/scan/stream`: outside the OpenAPI schema (SSE). */
export function scanStreamUrl(libraryId: string): string {
  return `${getLibrariesRetrieveUrl(libraryId)}/scan/stream`;
}

/**
 * Live progress of a library's scan (SPEC §8.3.9). While `watch` is on, the
 * scan stream's events replace the REST snapshot; when the scan ends the
 * library, scan history and title lists are refreshed and `onFinished` runs.
 */
export function useScanStream(
  libraryId: string,
  watch: boolean,
  onFinished: (event: ScanEvent) => void,
): { event: ScanEvent | null; state: StreamState } {
  const queryClient = useQueryClient();
  const [event, setEvent] = useState<ScanEvent | null>(null);
  const state = useEventStream(watch ? scanStreamUrl(libraryId) : null, ["scan"], (_name, data) => {
    if (!isScanEvent(data)) return;
    setEvent(data);
    if (!isActiveScan(data)) {
      void Promise.all(
        [
          getLibrariesListQueryKey(),
          getScansListQueryKey(),
          getMoviesListQueryKey(),
          getSeriesListQueryKey(),
          getReviewQueueListQueryKey(),
        ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      );
      onFinished(data);
    }
  });
  return { event, state };
}
