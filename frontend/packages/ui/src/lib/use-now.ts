import { useCallback, useSyncExternalStore } from "react";

/**
 * Shared clocks: every component asking for the same cadence shares one
 * interval, so a table of 200 live timers costs one timer, not 200. Clocks
 * stop while the tab is hidden and catch up the moment it is shown again.
 */
interface Clock {
  now: number;
  listeners: Set<() => void>;
  timer: ReturnType<typeof setInterval> | undefined;
}

const clocks = new Map<number, Clock>();
let watchingVisibility = false;

function hidden(): boolean {
  return document.visibilityState === "hidden";
}

function tick(clock: Clock): void {
  clock.now = Date.now();
  for (const listener of clock.listeners) listener();
}

function start(intervalMs: number, clock: Clock): void {
  if (clock.timer !== undefined || hidden()) return;
  clock.timer = setInterval(() => {
    tick(clock);
  }, intervalMs);
}

function stop(clock: Clock): void {
  if (clock.timer === undefined) return;
  clearInterval(clock.timer);
  clock.timer = undefined;
}

function onVisibilityChange(): void {
  for (const [intervalMs, clock] of clocks) {
    if (hidden()) {
      stop(clock);
    } else {
      tick(clock);
      start(intervalMs, clock);
    }
  }
}

function clockFor(intervalMs: number): Clock {
  let clock = clocks.get(intervalMs);
  if (clock === undefined) {
    clock = { now: Date.now(), listeners: new Set(), timer: undefined };
    clocks.set(intervalMs, clock);
  }
  return clock;
}

function subscribe(intervalMs: number, listener: () => void): () => void {
  const clock = clockFor(intervalMs);
  // A clock created by a render that never mounted may be stale; React
  // re-reads the snapshot after subscribing, so refreshing here is safe.
  if (clock.listeners.size === 0) clock.now = Date.now();
  clock.listeners.add(listener);
  start(intervalMs, clock);
  if (!watchingVisibility) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    watchingVisibility = true;
  }
  return () => {
    clock.listeners.delete(listener);
    if (clock.listeners.size > 0) return;
    stop(clock);
    clocks.delete(intervalMs);
    if (clocks.size === 0) {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      watchingVisibility = false;
    }
  };
}

/** The current time in ms, refreshed every `intervalMs` while the tab is visible. */
export function useNow(intervalMs = 1000): number {
  const subscribeClock = useCallback(
    (listener: () => void) => subscribe(intervalMs, listener),
    [intervalMs],
  );
  const snapshot = useCallback(() => clockFor(intervalMs).now, [intervalMs]);
  return useSyncExternalStore(subscribeClock, snapshot, snapshot);
}
