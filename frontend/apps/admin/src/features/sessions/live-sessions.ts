import { getSessionsStreamUrl, useSessionsList, type Session } from "@smart-iptv/api";
import { useMemo, useState } from "react";

import { FALLBACK_POLL_MS, useEventStream, type StreamState } from "../../lib/event-stream";

/** Open sessions read per poll while the feed is offline (far above any real concurrency). */
const FALLBACK_PAGE_SIZE = 500;

/**
 * A live session as the session feed sends it (`apps.playback.feed.entry`;
 * the SSE payload is outside the OpenAPI schema). `id` is the PlaybackSession
 * id that `sessions/{id}/kill` takes.
 */
export interface LiveSession {
  id: string;
  user: { id: string; name: string };
  device: { id: string; name: string };
  title: { kind: string; id: string; name: string };
  rendition: string;
  ip: string | null;
  country: string;
  edge: string;
  started_at: string;
  last_seen_at: string;
  bytes_sent: number;
}

interface SnapshotEvent {
  sessions: LiveSession[];
}

interface DiffEvent {
  added: LiveSession[];
  updated: LiveSession[];
  removed: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSnapshot(value: unknown): value is SnapshotEvent {
  return isRecord(value) && Array.isArray(value.sessions);
}

function isDiff(value: unknown): value is DiffEvent {
  return (
    isRecord(value) &&
    Array.isArray(value.added) &&
    Array.isArray(value.updated) &&
    Array.isArray(value.removed)
  );
}

/** Apply one feed event to the sessions by id: a snapshot replaces, a diff patches. */
export function applySessionEvent(
  current: ReadonlyMap<string, LiveSession>,
  name: string,
  data: unknown,
): ReadonlyMap<string, LiveSession> {
  if (name === "snapshot" && isSnapshot(data)) {
    return new Map(data.sessions.map((session) => [session.id, session]));
  }
  if (name === "diff" && isDiff(data)) {
    const next = new Map(current);
    for (const session of [...data.added, ...data.updated]) next.set(session.id, session);
    for (const id of data.removed) next.delete(id);
    return next;
  }
  return current;
}

/** A session of the REST list in the feed's shape (the edge is not known there). */
export function fromListed(session: Session): LiveSession {
  return {
    id: session.id,
    user: { id: session.user.id, name: session.user.name || session.user.username },
    device: session.device
      ? { id: session.device.id, name: session.device.name }
      : { id: "", name: "" },
    title: { kind: session.title_kind, id: session.title_id, name: session.title_name },
    rendition: session.rendition,
    ip: session.ip,
    country: session.country,
    edge: "",
    started_at: session.started_at,
    last_seen_at: session.last_heartbeat_at,
    bytes_sent: session.bytes_sent,
  };
}

/**
 * Live sessions from the SSE feed (a snapshot, then a diff every 2 s when
 * something changed). While the feed is offline, the open sessions are polled
 * from the REST list every 5 s instead.
 */
export function useLiveSessions(): {
  sessions: ReadonlyMap<string, LiveSession>;
  state: StreamState;
  received: boolean;
} {
  const [sessions, setSessions] = useState<ReadonlyMap<string, LiveSession>>(new Map());
  const [received, setReceived] = useState(false);
  const state = useEventStream(getSessionsStreamUrl(), ["snapshot", "diff"], (name, data) => {
    setSessions((current) => applySessionEvent(current, name, data));
    if (name === "snapshot") setReceived(true);
  });
  const offline = state === "offline";
  const polled = useSessionsList(
    { active: true, page_size: FALLBACK_PAGE_SIZE },
    { query: { enabled: offline, refetchInterval: offline ? FALLBACK_POLL_MS : false } },
  );
  const listed = useMemo(
    () =>
      polled.data
        ? new Map(polled.data.results.map((session) => [session.id, fromListed(session)]))
        : null,
    [polled.data],
  );
  if (offline && listed) return { sessions: listed, state, received: true };
  return { sessions, state, received };
}
