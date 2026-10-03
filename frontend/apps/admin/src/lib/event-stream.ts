import { useEffect, useRef, useState } from "react";

/** `open` while events flow; `connecting` before the first byte and while reconnecting. */
export type StreamState = "connecting" | "open" | "offline";

export type StreamListener = (event: string, data: unknown) => void;

/** After the server refuses a stream (EventSource gives up for good), try again this much later. */
const REOPEN_MS = 5_000;

function parse(raw: unknown): unknown {
  if (typeof raw !== "string") return undefined;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * Subscribe to a server-sent event stream of the admin API (SPEC §8.2: live
 * sessions, scans and transcode jobs). Same-origin, so the session cookie goes
 * with it. The browser reconnects by itself after a dropped connection (the
 * server's `retry` hint); when the server refuses the stream (an error status
 * closes an EventSource for good) the hook opens a new one after 5 s.
 *
 * `events` lists the event names to listen for; each event's `data` arrives
 * parsed as JSON (undefined when it isn't). A null `url` subscribes to nothing.
 */
export function useEventStream(
  url: string | null,
  events: readonly string[],
  onEvent: StreamListener,
): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const listener = useRef(onEvent);
  useEffect(() => {
    listener.current = onEvent;
  });
  const names = events.join(",");

  useEffect(() => {
    if (url === null) return undefined;
    if (typeof EventSource === "undefined") {
      setState("offline");
      return undefined;
    }
    let source: EventSource | null = null;
    let reopen: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    function open(target: string): void {
      setState("connecting");
      const current = new EventSource(target);
      source = current;
      current.onopen = () => {
        setState("open");
      };
      current.onerror = () => {
        if (current.readyState === EventSource.CLOSED) {
          setState("offline");
          if (!disposed) {
            reopen = setTimeout(() => {
              open(target);
            }, REOPEN_MS);
          }
        } else {
          setState("connecting");
        }
      };
      for (const name of names.split(",").filter(Boolean)) {
        current.addEventListener(name, (event: MessageEvent) => {
          listener.current(name, parse(event.data));
        });
      }
    }

    open(url);
    return () => {
      disposed = true;
      clearTimeout(reopen);
      source?.close();
    };
  }, [url, names]);

  return url === null ? "offline" : state;
}
