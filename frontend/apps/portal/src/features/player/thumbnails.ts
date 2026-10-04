/**
 * Seek-bar previews from a WebVTT sprite map (ADR-0014 §5): cues like
 * `00:00:10.000 --> 00:00:20.000` / `sprite-0.jpg#xywh=160,0,160,90`, one
 * 160×90 tile every 10 s on 10×10 sheets. Image URLs are relative to the VTT
 * and carry its signed token scope.
 */

export interface ThumbnailCue {
  start: number;
  end: number;
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** "01:02:03.500" or "02:03.500" to seconds; NaN when malformed. */
export function parseTimestamp(value: string): number {
  const parts = value.trim().split(":");
  if (parts.length < 2 || parts.length > 3) return Number.NaN;
  let seconds = 0;
  for (const part of parts) {
    if (!/^\d+(?:\.\d+)?$/u.test(part)) return Number.NaN;
    seconds = seconds * 60 + Number(part);
  }
  return seconds;
}

export function parseThumbnailsVtt(text: string, baseUrl: string): ThumbnailCue[] {
  const cues: ThumbnailCue[] = [];
  const blocks = text.replace(/\r\n?/gu, "\n").split(/\n{2,}/u);
  for (const block of blocks) {
    const lines = block
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const timingIndex = lines.findIndex((line) => line.includes("-->"));
    if (timingIndex === -1) continue;
    const [startText = "", endText = ""] = (lines[timingIndex] ?? "").split("-->");
    const start = parseTimestamp(startText);
    const end = parseTimestamp(endText.trim().split(/\s+/u)[0] ?? "");
    const payload = lines[timingIndex + 1];
    if (!Number.isFinite(start) || !Number.isFinite(end) || payload === undefined) continue;
    const [path = "", fragment = ""] = payload.split("#");
    const match = /^xywh=(\d+),(\d+),(\d+),(\d+)$/u.exec(fragment);
    let url: string;
    try {
      url = new URL(path, baseUrl).href;
    } catch {
      continue;
    }
    if (match === null) continue;
    cues.push({
      start,
      end,
      url,
      x: Number(match[1]),
      y: Number(match[2]),
      width: Number(match[3]),
      height: Number(match[4]),
    });
  }
  return cues.sort((a, b) => a.start - b.start);
}

/** The cue covering `time` (binary search), else the nearest earlier one. */
export function thumbnailAt(cues: readonly ThumbnailCue[], time: number): ThumbnailCue | null {
  let low = 0;
  let high = cues.length - 1;
  let found: ThumbnailCue | null = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const cue = cues[middle];
    if (cue === undefined) break;
    if (cue.start <= time) {
      found = cue;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found;
}
