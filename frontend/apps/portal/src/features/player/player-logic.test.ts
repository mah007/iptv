import { ApiError } from "@smart-iptv/api-portal";
import { describe, expect, it, vi } from "vitest";

import { isCritical, playbackProblem, shouldFallBackToMp4, streamFailure } from "./playback-errors";
import { PROGRESS_INTERVAL_MS, ProgressReporter } from "./progress";
import { qualityHeights, qualityLabel } from "./shaka";
import { isTypingTarget, shortcutFor } from "./shortcuts";
import { parseThumbnailsVtt, parseTimestamp, thumbnailAt } from "./thumbnails";

function apiError(code: string, status = 403): ApiError {
  return new ApiError({ status, code: code as ApiError["code"], title: code, detail: code });
}

describe("ProgressReporter", () => {
  function setup() {
    let clock = 0;
    const progress = vi.fn(() => Promise.resolve());
    const stop = vi.fn(() => Promise.resolve());
    const reporter = new ProgressReporter({ progress, stop }, () => clock);
    return {
      reporter,
      progress,
      stop,
      advance: (ms: number) => {
        clock += ms;
      },
    };
  }

  it("reports at once, then every 15 seconds while playing", () => {
    const { reporter, progress, advance } = setup();
    reporter.tick({ positionMs: 250.4, durationMs: 30_000 });
    expect(progress).toHaveBeenLastCalledWith({ positionMs: 250, durationMs: 30_000 });
    advance(PROGRESS_INTERVAL_MS - 1);
    reporter.tick({ positionMs: 15_000, durationMs: 30_000 });
    expect(progress).toHaveBeenCalledTimes(1);
    advance(1);
    reporter.tick({ positionMs: 15_200, durationMs: 30_000 });
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("reports on pause unless it just did (the API drops reports closer than 5 s)", () => {
    const { reporter, progress, advance } = setup();
    reporter.tick({ positionMs: 1_000, durationMs: null });
    advance(2_000);
    reporter.flush({ positionMs: 3_000, durationMs: null });
    expect(progress).toHaveBeenCalledTimes(1);
    advance(4_000);
    reporter.flush({ positionMs: 7_000, durationMs: null });
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("stops once, with the final position, and reports nothing after", async () => {
    const { reporter, progress, stop } = setup();
    await reporter.stop({ positionMs: 6_400, durationMs: Number.NaN });
    await reporter.stop({ positionMs: 9_000, durationMs: 30_000 });
    expect(stop).toHaveBeenCalledTimes(1);
    expect(stop).toHaveBeenCalledWith({ positionMs: 6_400, durationMs: null });
    reporter.tick({ positionMs: 10_000, durationMs: 30_000 });
    reporter.flush({ positionMs: 10_000, durationMs: 30_000 });
    expect(progress).not.toHaveBeenCalled();
    expect(reporter.isStopped).toBe(true);
  });

  it("never lets a failed report break playback", async () => {
    const reporter = new ProgressReporter({
      progress: () => Promise.reject(new Error("offline")),
      stop: () => Promise.reject(new Error("offline")),
    });
    reporter.tick({ positionMs: 1, durationMs: 2 });
    await expect(reporter.stop({ positionMs: 1, durationMs: 2 })).resolves.toBeUndefined();
  });
});

describe("thumbnails", () => {
  const vtt = [
    "WEBVTT",
    "",
    "00:00:00.000 --> 00:00:10.000",
    "sprite-0.jpg#xywh=0,0,160,90",
    "",
    "2",
    "00:00:10.000 --> 00:00:20.000",
    "sprite-0.jpg#xywh=160,0,160,90",
    "",
    "01:00:00.000 --> 01:00:10.000",
    "sprite-3.jpg#xywh=320,90,160,90",
    "",
    "00:00:30.000 --> 00:00:40.000",
    "broken-cue-without-fragment.jpg",
  ].join("\r\n");

  it("parses sprite cues and resolves images next to the signed VTT", () => {
    const cues = parseThumbnailsVtt(vtt, "http://media.localhost/v/tok/hls/thumbs/thumbs.vtt");
    expect(cues).toHaveLength(3);
    expect(cues[1]).toEqual({
      start: 10,
      end: 20,
      url: "http://media.localhost/v/tok/hls/thumbs/sprite-0.jpg",
      x: 160,
      y: 0,
      width: 160,
      height: 90,
    });
    expect(thumbnailAt(cues, 12.5)?.x).toBe(160);
    expect(thumbnailAt(cues, 3_700)?.url).toMatch(/sprite-3\.jpg$/u);
    expect(thumbnailAt([], 5)).toBeNull();
  });

  it("reads timestamps with and without hours", () => {
    expect(parseTimestamp("01:02:03.500")).toBe(3723.5);
    expect(parseTimestamp("02:03.250")).toBe(123.25);
    expect(parseTimestamp("nonsense")).toBeNaN();
  });
});

describe("shortcuts", () => {
  const key = (value: string, modifiers: Partial<KeyboardEvent> = {}) =>
    shortcutFor({ key: value, altKey: false, ctrlKey: false, metaKey: false, ...modifiers });

  it("maps the player keys", () => {
    expect(key(" ")).toEqual({ type: "toggle-play" });
    expect(key("ArrowLeft")).toEqual({ type: "seek-by", seconds: -10 });
    expect(key("ArrowRight")).toEqual({ type: "seek-by", seconds: 10 });
    expect(key("ArrowUp")).toEqual({ type: "volume-by", delta: 0.1 });
    expect(key("f")).toEqual({ type: "toggle-fullscreen" });
    expect(key("M")).toEqual({ type: "toggle-mute" });
    expect(key("c")).toEqual({ type: "toggle-captions" });
    expect(key("5")).toEqual({ type: "seek-to-ratio", ratio: 0.5 });
    expect(key("f", { ctrlKey: true })).toBeNull();
    expect(key("x")).toBeNull();
  });

  it("leaves typing alone", () => {
    const input = document.createElement("input");
    const slider = document.createElement("input");
    slider.type = "range";
    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(slider)).toBe(false);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("playback problems", () => {
  it("maps the API's denials to messages and actions", () => {
    expect(playbackProblem(apiError("SUBSCRIPTION_EXPIRED"))).toEqual({
      code: "SUBSCRIPTION_EXPIRED",
      actions: ["renew"],
    });
    expect(playbackProblem(apiError("CONCURRENCY_LIMIT", 409)).actions).toEqual([
      "devices",
      "retry",
    ]);
    expect(playbackProblem(apiError("TITLE_PREPARING", 409)).actions).toEqual(["retry"]);
    expect(playbackProblem(apiError("QUALITY_NOT_ALLOWED")).actions).toEqual(["plans"]);
    expect(playbackProblem("STREAM_DENIED").actions).toEqual(["retry"]);
    expect(playbackProblem(new Error("boom"))).toEqual({ code: "UNEXPECTED", actions: ["retry"] });
  });

  it("tells a refused stream from a broken one", () => {
    expect(streamFailure({ category: 1, code: 1001, data: ["url", 403] })).toBe("STREAM_DENIED");
    expect(streamFailure({ category: 1, code: 1001, data: ["url", 500] })).toBe("STREAM_FAILED");
    expect(streamFailure({ category: 3, code: 3016 })).toBe("STREAM_FAILED");
    expect(streamFailure(null)).toBe("STREAM_FAILED");
  });

  it("leaves recoverable errors to Shaka", () => {
    expect(isCritical({ category: 1, code: 1001, severity: 1 })).toBe(false);
    expect(isCritical({ category: 1, code: 1001, severity: 2 })).toBe(true);
    expect(isCritical(null)).toBe(true);
  });

  it("falls back to the MP4 unless the edge refused the token", () => {
    expect(shouldFallBackToMp4({ category: 4, code: 4000 })).toBe(true);
    expect(shouldFallBackToMp4({ category: 1, code: 1001, data: ["url", 404] })).toBe(true);
    expect(shouldFallBackToMp4({ category: 1, code: 1001, data: ["url", 403] })).toBe(false);
  });
});

describe("quality", () => {
  it("lists distinct heights, tallest first", () => {
    expect(
      qualityHeights([{ height: 720 }, { height: 1080 }, { height: 720 }, { height: null }]),
    ).toEqual([1080, 720]);
    expect(qualityLabel(2160)).toBe("4K");
    expect(qualityLabel(540)).toBe("540p");
  });
});
