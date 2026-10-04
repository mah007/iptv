/**
 * When to report playback progress (SPEC §9, ADR-0013 §9): every ~15 s while
 * playing, on pause, when the page is hidden, and a final stop on exit. The
 * API drops reports closer together than 5 s, so a pause right after a
 * periodic report is skipped here too.
 */

export const PROGRESS_INTERVAL_MS = 15_000;
/** `playback.progress_min_interval_s`: the API ignores reports closer than this. */
export const PROGRESS_MIN_GAP_MS = 5_000;

export interface Position {
  positionMs: number;
  durationMs: number | null;
}

export interface ProgressSink {
  progress: (position: Position) => Promise<unknown>;
  stop: (position: Position) => Promise<unknown>;
}

export class ProgressReporter {
  private lastSentAt: number | null = null;
  private stopped = false;

  constructor(
    private readonly sink: ProgressSink,
    private readonly now: () => number = () => Date.now(),
    private readonly intervalMs = PROGRESS_INTERVAL_MS,
  ) {}

  /** While playing (timeupdate): report when the interval has passed. */
  tick(position: Position): void {
    if (this.stopped) return;
    if (this.lastSentAt !== null && this.now() - this.lastSentAt < this.intervalMs) return;
    this.send(position);
  }

  /** Paused, seeked away, or the page went to the background: report now unless just reported. */
  flush(position: Position): void {
    if (this.stopped) return;
    // The API would drop it; the next tick or the stop carries the position.
    if (this.lastSentAt !== null && this.now() - this.lastSentAt < PROGRESS_MIN_GAP_MS) return;
    this.send(position);
  }

  /** Leaving the player: the final position, unthrottled, and the session ends. Only once. */
  stop(position: Position): Promise<unknown> {
    if (this.stopped) return Promise.resolve();
    this.stopped = true;
    return this.sink.stop(rounded(position)).catch(() => undefined);
  }

  get isStopped(): boolean {
    return this.stopped;
  }

  private send(position: Position): void {
    const value = rounded(position);
    this.lastSentAt = this.now();
    void this.sink.progress(value).catch(() => undefined);
  }
}

function rounded(position: Position): Position {
  return {
    positionMs: Math.max(0, Math.round(position.positionMs)),
    durationMs:
      position.durationMs === null || !Number.isFinite(position.durationMs)
        ? null
        : Math.max(0, Math.round(position.durationMs)),
  };
}
