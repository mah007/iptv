import {
  getTranscodeJobsListQueryKey,
  getTranscodeJobsStreamUrl,
  type TranscodeJob,
} from "@smart-iptv/api";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useEventStream, type StreamState } from "../../lib/event-stream";

/**
 * A job's live fields as the transcode feed sends them (`apps.media.services.event`;
 * the SSE payload is outside the OpenAPI schema).
 */
export type JobUpdate = Pick<
  TranscodeJob,
  | "id"
  | "status"
  | "progress"
  | "fps"
  | "speed"
  | "eta_s"
  | "backend"
  | "encoder"
  | "priority"
  | "attempts"
  | "worker_host"
  | "error"
>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isUpdate(value: unknown): value is JobUpdate {
  return isRecord(value) && typeof value.id === "string" && typeof value.status === "string";
}

/** State changes within this window share one list refetch. */
const REFETCH_DELAY_MS = 1_000;

/**
 * Live transcode progress (SPEC §8.3.10): the feed's snapshot and `job` events,
 * by job id, to lay over the REST list. When a job changes state (queued →
 * running → done or failed) or a job the list doesn't know appears, the list
 * is fetched again so its other fields follow.
 */
export function useLiveJobs(): { updates: ReadonlyMap<string, JobUpdate>; state: StreamState } {
  const queryClient = useQueryClient();
  const [updates, setUpdates] = useState<ReadonlyMap<string, JobUpdate>>(new Map());
  const statuses = useRef(new Map<string, string>());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
    },
    [],
  );

  function refetchSoon(): void {
    if (timer.current !== undefined) return;
    timer.current = setTimeout(() => {
      timer.current = undefined;
      void queryClient.invalidateQueries({ queryKey: getTranscodeJobsListQueryKey() });
    }, REFETCH_DELAY_MS);
  }

  const state = useEventStream(getTranscodeJobsStreamUrl(), ["snapshot", "job"], (name, data) => {
    if (name === "snapshot" && isRecord(data) && Array.isArray(data.jobs)) {
      const jobs = data.jobs.filter(isUpdate);
      statuses.current = new Map(jobs.map((job) => [job.id, job.status]));
      setUpdates(new Map(jobs.map((job) => [job.id, job])));
      return;
    }
    if (name === "job" && isUpdate(data)) {
      const before = statuses.current.get(data.id);
      statuses.current.set(data.id, data.status);
      setUpdates((current) => new Map(current).set(data.id, data));
      if (before !== data.status) refetchSoon();
    }
  });
  return { updates, state };
}
