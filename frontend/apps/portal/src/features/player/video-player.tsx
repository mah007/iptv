import { playbackProgress, playbackStop, type PlaybackGrant } from "@smart-iptv/api-portal";
import { Button, cn, formatDuration } from "@smart-iptv/ui";
import {
  ArrowLeft,
  Captions,
  LoaderCircle,
  Maximize,
  Minimize,
  Pause,
  PictureInPicture2,
  Play,
  RotateCcw,
  RotateCw,
  Settings,
  SkipForward,
  Volume1,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { languageName } from "../../lib/languages";
import {
  isCritical,
  shouldFallBackToMp4,
  streamFailure,
  type StreamFailure,
} from "./playback-errors";
import { ProgressReporter, type Position } from "./progress";
import { loadShaka, qualityHeights, qualityLabel, type ShakaPlayer } from "./shaka";
import { isTypingTarget, SEEK_STEP_S, shortcutFor, type PlayerAction } from "./shortcuts";
import { parseThumbnailsVtt, thumbnailAt, type ThumbnailCue } from "./thumbnails";
import { invalidateViewerState } from "./viewer-cache";

export type PlayerFailure = StreamFailure | "HLS_UNAVAILABLE";

export interface VideoPlayerProps {
  grant: PlaybackGrant;
  heading: string;
  subheading?: string | undefined;
  /** Where to start, in seconds. */
  startAt: number;
  onBack: () => void;
  /** The stream failed: the page asks for a new grant (or the MP4) from `positionS`. */
  onFailure: (failure: PlayerFailure, positionS: number) => void;
  /** The next episode, offered with a countdown at the end. */
  next?: { label: string; play: () => void } | null;
}

interface TextOption {
  id: number;
  label: string;
}

interface AudioOption {
  key: string;
  label: string;
  language: string;
  role: string | null;
  active: boolean;
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;
const SUBTITLE_SIZES = { small: 0.8, medium: 1, large: 1.35 } as const;
type SubtitleSize = keyof typeof SUBTITLE_SIZES;
const HIDE_CONTROLS_MS = 3000;
const NEXT_COUNTDOWN_S = 10;
const PREFS_KEY = "smart-iptv.player";
/** Stops scheduled by an unmount, per playback session, until the timer fires. */
const pendingStops = new Map<string, number>();

interface Preferences {
  volume: number;
  muted: boolean;
  subtitleSize: SubtitleSize;
  subtitleBackground: boolean;
  subtitleLanguage: string | null;
}

const DEFAULT_PREFS: Preferences = {
  volume: 1,
  muted: false,
  subtitleSize: "medium",
  subtitleBackground: true,
  subtitleLanguage: null,
};

function readPreferences(): Preferences {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (raw === null) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return {
      volume: typeof parsed.volume === "number" ? Math.min(1, Math.max(0, parsed.volume)) : 1,
      muted: parsed.muted === true,
      subtitleSize:
        parsed.subtitleSize !== undefined && parsed.subtitleSize in SUBTITLE_SIZES
          ? parsed.subtitleSize
          : "medium",
      subtitleBackground: parsed.subtitleBackground !== false,
      subtitleLanguage:
        typeof parsed.subtitleLanguage === "string" ? parsed.subtitleLanguage : null,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

function writePreferences(preferences: Preferences): void {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(preferences));
  } catch {
    // Not remembered; playback is unaffected.
  }
}

function clock(seconds: number, language: string): string {
  return formatDuration(Number.isFinite(seconds) ? seconds : 0, language, "clock");
}

/** One labelled radio group in the settings panel. */
function SettingsGroup<T extends string | number>({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string;
  name: string;
  value: T;
  options: readonly { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="grid gap-1">
      <legend className="mb-1 px-2 text-xs font-semibold uppercase text-white/60 ltr:tracking-wide">
        {legend}
      </legend>
      {options.map((option) => (
        <label
          key={String(option.value)}
          className="flex h-9 cursor-pointer items-center gap-2 rounded-input px-2 text-sm text-white hover:bg-white/10 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring"
        >
          <input
            type="radio"
            name={name}
            className="size-4 accent-[var(--primary)]"
            checked={option.value === value}
            onChange={() => {
              onChange(option.value);
            }}
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

/**
 * The web player (SPEC §9): Shaka Player with custom controls, quality within
 * the plan's ceiling, audio and subtitle tracks, seek-bar thumbnails, keyboard
 * shortcuts, full screen and picture-in-picture. Progress goes to the API every
 * ~15 s and on pause; leaving stops the session.
 */
export function VideoPlayer({
  grant,
  heading,
  subheading,
  startAt,
  onBack,
  onFailure,
  next,
}: VideoPlayerProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language;
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<ShakaPlayer | null>(null);
  const reporterRef = useRef<ProgressReporter | null>(null);
  const hideTimer = useRef<number | undefined>(undefined);
  const fail = useEffectEvent(onFailure);
  const settingsId = useId();

  const [preferences, setPreferences] = useState(readPreferences);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [waiting, setWaiting] = useState(true);
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const [time, setTime] = useState(startAt);
  const [duration, setDuration] = useState(grant.duration_ms / 1000);
  const [buffered, setBuffered] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [pip, setPip] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [heights, setHeights] = useState<number[]>([]);
  const [activeHeight, setActiveHeight] = useState<number | null>(null);
  const [autoQuality, setAutoQuality] = useState(true);
  const [audioOptions, setAudioOptions] = useState<AudioOption[]>([]);
  const [textOptions, setTextOptions] = useState<TextOption[]>([]);
  const [activeText, setActiveText] = useState<number | null>(null);
  const [sprites, setSprites] = useState<{ url: string; cues: ThumbnailCue[] } | null>(null);
  const [preview, setPreview] = useState<{ time: number; ratio: number } | null>(null);
  const [ended, setEnded] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const lastText = useRef<number | null>(null);
  const startCountdown = useEffectEvent(() => {
    if (next) setCountdown(NEXT_COUNTDOWN_S);
  });

  const updatePreferences = useCallback((patch: Partial<Preferences>) => {
    setPreferences((current) => {
      const updated = { ...current, ...patch };
      writePreferences(updated);
      return updated;
    });
  }, []);

  // The last known position: on unmount the video element is already detached.
  const lastPosition = useRef<Position>({
    positionMs: startAt * 1000,
    durationMs: grant.duration_ms > 0 ? grant.duration_ms : null,
  });
  const position = useCallback((): Position => {
    const video = videoRef.current;
    if (video && video.readyState > 0) {
      const durationS = Number.isFinite(video.duration) ? video.duration : grant.duration_ms / 1000;
      lastPosition.current = {
        positionMs: video.currentTime * 1000,
        durationMs: durationS > 0 ? durationS * 1000 : null,
      };
    }
    return lastPosition.current;
  }, [grant.duration_ms]);

  /** Re-read the tracks Shaka exposes after a load or an adaptation. */
  const readTracks = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;
    const videos = player.getVideoTracks();
    setHeights(qualityHeights(videos));
    setActiveHeight(videos.find((track) => track.active)?.height ?? null);
    const locale = language;
    const audio = player.getAudioTracks().map((track, index) => {
      const name =
        languageName(track.language, locale) ??
        track.label ??
        t("player.trackNumber", { number: index + 1 });
      const channels =
        track.channelsCount !== null && track.channelsCount > 2
          ? ` · ${track.channelsCount === 6 ? "5.1" : track.channelsCount === 8 ? "7.1" : String(track.channelsCount)}`
          : "";
      return {
        key: `${track.language}|${track.label ?? ""}|${String(index)}`,
        label: `${name}${channels}`,
        language: track.language,
        role: track.roles[0] ?? null,
        active: track.active,
      };
    });
    setAudioOptions(audio);
    const texts = player.getTextTracks();
    setTextOptions(
      texts.map((track) => ({
        id: track.id,
        label: [
          languageName(track.language, locale) ?? track.label ?? track.language,
          track.forced ? t("player.forced") : null,
        ]
          .filter(Boolean)
          .join(" · "),
      })),
    );
    setActiveText(texts.find((track) => track.active)?.id ?? null);
  }, [language, t]);

  // Load the stream with Shaka; report progress; stop the session when leaving.
  useEffect(() => {
    const video = videoRef.current;
    const container = containerRef.current;
    if (!video || !container) return;
    // An object, so the checks after each await see the cleanup's change.
    const state = { cancelled: false };
    const cancelled = () => state.cancelled;
    // React may unmount and remount at once (StrictMode, a quick re-render): the stop
    // scheduled by the unmount is cancelled, so the session keeps playing.
    const pending = pendingStops.get(grant.session_id);
    if (pending !== undefined) {
      window.clearTimeout(pending);
      pendingStops.delete(grant.session_id);
    }
    const reporter = new ProgressReporter({
      progress: (value) =>
        playbackProgress(grant.session_id, {
          position_ms: value.positionMs,
          duration_ms: value.durationMs,
        }),
      // keepalive: the request survives the page being closed or navigated away.
      stop: (value) =>
        playbackStop(
          grant.session_id,
          { position_ms: value.positionMs, duration_ms: value.durationMs },
          { keepalive: true },
        ).finally(() => {
          void invalidateViewerState(queryClient);
        }),
    });
    reporterRef.current = reporter;
    const segmented = grant.delivery === "segmented";

    async function start(): Promise<void> {
      if (!video || !container) return;
      const shaka = await loadShaka();
      if (cancelled()) return;
      if (segmented && !shaka.Player.isBrowserSupported()) {
        fail("HLS_UNAVAILABLE", startAt);
        return;
      }
      const player = new shaka.Player();
      playerRef.current = player;
      await player.attach(video);
      player.setVideoContainer(container);
      player.configure({
        // Subtitles as styled text inside the player, not the browser's native cues.
        textDisplayFactory: (owner: ShakaPlayer) => new shaka.text.UITextDisplayer(owner),
        streaming: { retryParameters: { maxAttempts: 3, baseDelay: 800 } },
        manifest: { retryParameters: { maxAttempts: 3, baseDelay: 800 } },
      });
      player.addEventListener("error", (event) => {
        const detail = (event as unknown as { detail: unknown }).detail;
        // Shaka retries recoverable errors itself; only a critical one ends playback.
        if (!isCritical(detail)) return;
        fail(streamFailure(detail), video.currentTime);
      });
      for (const name of [
        "trackschanged",
        "variantchanged",
        "adaptation",
        "textchanged",
        "audiotrackschanged",
      ]) {
        player.addEventListener(name, readTracks);
      }
      try {
        await player.load(
          grant.url,
          startAt > 0 ? startAt : null,
          segmented ? "application/x-mpegurl" : "video/mp4",
        );
      } catch (error) {
        if (cancelled()) return;
        if (segmented && shouldFallBackToMp4(error)) fail("HLS_UNAVAILABLE", startAt);
        else fail(streamFailure(error), startAt);
        return;
      }
      if (cancelled()) return;
      // Subtitles: an HLS master lists them; the MP4 gets the signed WebVTT files.
      if (player.getTextTracks().length === 0) {
        for (const track of grant.subtitles) {
          if (!track.url) continue;
          try {
            await player.addTextTrackAsync(
              track.url,
              track.language,
              "subtitles",
              "text/vtt",
              undefined,
              track.name || undefined,
              track.forced,
            );
          } catch {
            // A missing subtitle file never stops playback.
          }
        }
      }
      const preferred = preferences.subtitleLanguage;
      const texts = player.getTextTracks();
      const match =
        preferred === null ? undefined : texts.find((track) => track.language === preferred);
      player.selectTextTrack(match ?? null);
      readTracks();
      setReady(true);
      try {
        await video.play();
      } catch {
        // Autoplay with sound was refused: the viewer presses play.
        setAutoplayBlocked(true);
        setWaiting(false);
      }
    }
    void start();

    const onPageHide = () => {
      void reporter.stop(position());
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      state.cancelled = true;
      window.removeEventListener("pagehide", onPageHide);
      const final = position();
      pendingStops.set(
        grant.session_id,
        window.setTimeout(() => {
          pendingStops.delete(grant.session_id);
          void reporter.stop(final);
        }, 0),
      );
      const player = playerRef.current;
      playerRef.current = null;
      void player?.destroy();
    };
    // The grant is the stream: a new one reloads everything; preferences are read once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grant]);

  // Seek-bar thumbnails from the signed sprite map.
  useEffect(() => {
    const url = grant.thumbnails_url;
    if (!url) return;
    const controller = new AbortController();
    fetch(url, { signal: controller.signal })
      .then((response) => (response.ok ? response.text() : ""))
      .then((text) => {
        setSprites({ url, cues: parseThumbnailsVtt(text, url) });
      })
      .catch(() => {
        // No previews; the seek bar still works.
      });
    return () => {
      controller.abort();
    };
  }, [grant.thumbnails_url]);

  // Controls fade out while playing, and come back on any movement or key.
  const reveal = useCallback(() => {
    setControlsVisible(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      setControlsVisible(false);
    }, HIDE_CONTROLS_MS);
  }, []);

  // Media element events.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = preferences.volume;
    video.muted = preferences.muted;
    const onTime = () => {
      setTime(video.currentTime);
      if (Number.isFinite(video.duration) && video.duration > 0) setDuration(video.duration);
      if (video.buffered.length > 0) setBuffered(video.buffered.end(video.buffered.length - 1));
      if (!video.paused) reporterRef.current?.tick(position());
    };
    const onPlay = () => {
      setPlaying(true);
      setEnded(false);
      setAutoplayBlocked(false);
      // Playing: the controls fade after a moment without movement.
      reveal();
    };
    const onPause = () => {
      setPlaying(false);
      reporterRef.current?.flush(position());
    };
    const onWaiting = () => {
      setWaiting(true);
    };
    const onPlaying = () => {
      setWaiting(false);
    };
    const onEnded = () => {
      setPlaying(false);
      setEnded(true);
      startCountdown();
      reporterRef.current?.flush(position());
    };
    const onVolume = () => {
      updatePreferences({ volume: video.volume, muted: video.muted });
    };
    const onEnterPip = () => {
      setPip(true);
    };
    const onLeavePip = () => {
      setPip(false);
    };
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("durationchange", onTime);
    video.addEventListener("progress", onTime);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("waiting", onWaiting);
    video.addEventListener("playing", onPlaying);
    video.addEventListener("canplay", onPlaying);
    video.addEventListener("ended", onEnded);
    video.addEventListener("volumechange", onVolume);
    video.addEventListener("enterpictureinpicture", onEnterPip);
    video.addEventListener("leavepictureinpicture", onLeavePip);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") reporterRef.current?.flush(position());
    };
    document.addEventListener("visibilitychange", onVisibility);
    const onFullscreen = () => {
      setFullscreen(document.fullscreenElement === containerRef.current);
    };
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => {
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("durationchange", onTime);
      video.removeEventListener("progress", onTime);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("waiting", onWaiting);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("canplay", onPlaying);
      video.removeEventListener("ended", onEnded);
      video.removeEventListener("volumechange", onVolume);
      video.removeEventListener("enterpictureinpicture", onEnterPip);
      video.removeEventListener("leavepictureinpicture", onLeavePip);
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("fullscreenchange", onFullscreen);
    };
    // Volume is applied once per stream; later changes go straight to the element.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grant, position, reveal, updatePreferences]);

  // The next-episode countdown after the end: a tick a second, then the next episode.
  const playNext = useEffectEvent(() => {
    next?.play();
  });
  useEffect(() => {
    if (countdown === null) return;
    const timer = window.setTimeout(() => {
      if (countdown <= 1) {
        setCountdown(null);
        playNext();
      } else {
        setCountdown(countdown - 1);
      }
    }, 1000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [countdown]);

  useEffect(
    () => () => {
      window.clearTimeout(hideTimer.current);
    },
    [],
  );
  const showControls = controlsVisible || !playing || settingsOpen || !ready || ended;

  // --- Actions -----------------------------------------------------------------------------

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused || video.ended) void video.play().catch(() => undefined);
    else video.pause();
  }, []);

  const seekTo = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    const end = Number.isFinite(video.duration) ? video.duration : Number.POSITIVE_INFINITY;
    video.currentTime = Math.min(Math.max(0, seconds), Math.max(0, end - 0.25));
    setTime(video.currentTime);
  }, []);

  const toggleFullscreen = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    else if (typeof container.requestFullscreen === "function")
      void container.requestFullscreen().catch(() => undefined);
  }, []);

  const toggleMute = useCallback(() => {
    const video = videoRef.current;
    if (video) video.muted = !video.muted;
  }, []);

  const selectText = useCallback(
    (id: number | null) => {
      const player = playerRef.current;
      if (!player) return;
      const track = id === null ? undefined : player.getTextTracks().find((item) => item.id === id);
      player.selectTextTrack(track ?? null);
      if (track) lastText.current = track.id;
      setActiveText(track?.id ?? null);
      updatePreferences({ subtitleLanguage: track?.language ?? null });
    },
    [updatePreferences],
  );

  const toggleCaptions = useCallback(() => {
    if (activeText !== null) {
      selectText(null);
      return;
    }
    const candidate =
      textOptions.find((option) => option.id === lastText.current) ?? textOptions[0];
    if (candidate) selectText(candidate.id);
  }, [activeText, selectText, textOptions]);

  const selectQuality = useCallback(
    (height: number | "auto") => {
      const player = playerRef.current;
      if (!player) return;
      if (height === "auto") {
        player.configure({ abr: { enabled: true } });
        setAutoQuality(true);
      } else {
        player.configure({ abr: { enabled: false } });
        const track = player.getVideoTracks().find((item) => item.height === height);
        if (track) player.selectVideoTrack(track, true);
        setAutoQuality(false);
      }
      readTracks();
    },
    [readTracks],
  );

  const selectAudio = useCallback(
    (key: string) => {
      const player = playerRef.current;
      if (!player) return;
      const tracks = player.getAudioTracks();
      const index = Number(key.split("|").at(-1));
      const track = tracks[index];
      if (track) player.selectAudioTrack(track);
      readTracks();
    },
    [readTracks],
  );

  const run = useCallback(
    (action: PlayerAction) => {
      const video = videoRef.current;
      switch (action.type) {
        case "toggle-play":
          togglePlay();
          break;
        case "seek-by":
          if (video) seekTo(video.currentTime + action.seconds);
          break;
        case "seek-to-ratio":
          if (video && Number.isFinite(video.duration)) seekTo(video.duration * action.ratio);
          break;
        case "volume-by":
          if (video) {
            video.volume = Math.min(
              1,
              Math.max(0, Math.round((video.volume + action.delta) * 100) / 100),
            );
            if (video.volume > 0) video.muted = false;
          }
          break;
        case "toggle-fullscreen":
          toggleFullscreen();
          break;
        case "toggle-mute":
          toggleMute();
          break;
        case "toggle-captions":
          toggleCaptions();
          break;
      }
    },
    [seekTo, toggleCaptions, toggleFullscreen, toggleMute, togglePlay],
  );

  // Keyboard shortcuts anywhere on the page, except where a control needs the key.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTypingTarget(event.target)) return;
      if (event.key === "Escape" && settingsOpen) {
        setSettingsOpen(false);
        return;
      }
      const action = shortcutFor(event);
      if (action === null) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      // A focused slider moves itself; a focused button or radio takes Space and Enter.
      if (
        target?.matches("input[type=range]") &&
        action.type !== "toggle-play" &&
        !["f", "F", "m", "M", "c", "C"].includes(event.key)
      )
        return;
      if (event.key === " " && target?.matches("button, a, input, label")) return;
      event.preventDefault();
      run(action);
      reveal();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [reveal, run, settingsOpen]);

  // --- Rendering ---------------------------------------------------------------------------

  const ratio = duration > 0 ? Math.min(1, time / duration) : 0;
  const bufferedRatio = duration > 0 ? Math.min(1, buffered / duration) : 0;
  const thumbnails = sprites !== null && sprites.url === grant.thumbnails_url ? sprites.cues : [];
  const thumb = preview ? thumbnailAt(thumbnails, preview.time) : null;
  const pipSupported = typeof document !== "undefined" && document.pictureInPictureEnabled;
  const volume = preferences.muted ? 0 : preferences.volume;
  const VolumeIcon = volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;
  const subtitleStyle = {
    "--subtitle-scale": String(SUBTITLE_SIZES[preferences.subtitleSize]),
    "--subtitle-background": preferences.subtitleBackground ? "rgb(0 0 0 / 0.65)" : "transparent",
  } as CSSProperties;

  return (
    <div
      ref={containerRef}
      data-controls={showControls || undefined}
      className={cn(
        "player-shell group/player fixed inset-0 z-50 bg-black text-white",
        !showControls && "cursor-none",
      )}
      style={subtitleStyle}
      onPointerMove={reveal}
      onPointerDown={reveal}
      onFocusCapture={reveal}
    >
      <video
        ref={videoRef}
        playsInline
        aria-label={heading}
        className="absolute inset-0 size-full bg-black"
        onClick={() => {
          if (!settingsOpen) togglePlay();
          else setSettingsOpen(false);
        }}
        onDoubleClick={toggleFullscreen}
      />

      {waiting && !autoplayBlocked ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center" role="status">
          <LoaderCircle aria-hidden="true" className="size-12 animate-spin text-white/90" />
          <span className="sr-only">{t("player.loading")}</span>
        </div>
      ) : null}

      {autoplayBlocked || (ready && !playing && !waiting && !ended) ? (
        <button
          type="button"
          aria-label={t("player.play")}
          className="absolute inset-0 m-auto grid size-20 place-items-center rounded-full bg-black/55 text-white ring-1 ring-white/40 outline-none backdrop-blur-sm transition-transform hover:scale-105 focus-visible:ring-2 focus-visible:ring-ring"
          onClick={togglePlay}
        >
          <Play aria-hidden="true" className="size-9 fill-current" />
        </button>
      ) : null}

      {/* Top: back and what is playing. */}
      <div
        className={cn(
          "absolute inset-x-0 top-0 flex items-center gap-3 bg-linear-to-b from-black/80 to-transparent px-3 pt-3 pb-10 transition-opacity duration-200 sm:px-6",
          showControls ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      >
        <Button
          variant="ghost"
          size="icon"
          className="text-white hover:bg-white/15 hover:text-white"
          aria-label={t("player.back")}
          onClick={onBack}
        >
          <ArrowLeft aria-hidden="true" className="rtl:-scale-x-100" />
        </Button>
        <div className="grid min-w-0">
          <h1 className="truncate text-base font-semibold sm:text-lg">{heading}</h1>
          {subheading ? <p className="truncate text-sm text-white/75">{subheading}</p> : null}
        </div>
      </div>

      {/* Next episode countdown. */}
      {ended && next ? (
        <div
          className="absolute end-4 bottom-28 grid w-72 gap-3 rounded-card bg-black/80 p-4 ring-1 ring-white/20 backdrop-blur-md sm:end-8"
          role="status"
        >
          <p className="text-sm text-white/80">
            {countdown === null ? t("player.nextUp") : t("player.nextIn", { count: countdown })}
          </p>
          <p className="truncate font-semibold">{next.label}</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={next.play}>
              <SkipForward aria-hidden="true" />
              {t("player.playNext")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-white hover:bg-white/15 hover:text-white"
              onClick={() => {
                setCountdown(null);
              }}
            >
              {t("player.cancel")}
            </Button>
          </div>
        </div>
      ) : null}

      {/* Bottom controls. Media timelines stay left-to-right in Arabic, as in other players. */}
      <div
        className={cn(
          "absolute inset-x-0 bottom-0 grid gap-2 bg-linear-to-t from-black/85 via-black/50 to-transparent px-3 pt-16 pb-3 transition-opacity duration-200 sm:px-6 sm:pb-5",
          showControls ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      >
        <div
          dir="ltr"
          className="group/seek relative flex h-6 items-center"
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const at = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
            setPreview({ ratio: at, time: at * duration });
          }}
          onPointerLeave={() => {
            setPreview(null);
          }}
        >
          <div className="pointer-events-none absolute inset-x-0 h-1 rounded-full bg-white/25 transition-[height] group-hover/seek:h-1.5">
            <div
              className="absolute inset-y-0 start-0 rounded-full bg-white/35"
              style={{ width: `${String(bufferedRatio * 100)}%` }}
            />
            <div
              className="absolute inset-y-0 start-0 rounded-full bg-primary"
              style={{ width: `${String(ratio * 100)}%` }}
            />
          </div>
          <div
            className="pointer-events-none absolute size-3.5 -translate-x-1/2 rounded-full bg-primary shadow ring-2 ring-black/20"
            style={{ left: `${String(ratio * 100)}%` }}
          />
          <input
            type="range"
            min={0}
            max={Math.max(1, Math.floor(duration))}
            step={SEEK_STEP_S}
            value={Math.floor(time)}
            aria-label={t("player.seek")}
            aria-valuetext={t("player.positionOf", {
              position: clock(time, language),
              duration: clock(duration, language),
            })}
            className="absolute inset-0 h-6 w-full cursor-pointer appearance-none opacity-0"
            onChange={(event) => {
              seekTo(Number(event.target.value));
            }}
          />
          {preview ? (
            <div
              className="pointer-events-none absolute bottom-6 grid -translate-x-1/2 justify-items-center gap-1"
              style={{ left: `clamp(5rem, ${String(preview.ratio * 100)}%, calc(100% - 5rem))` }}
              aria-hidden="true"
            >
              {thumb ? (
                <div
                  className="overflow-hidden rounded-input bg-black ring-1 ring-white/40"
                  style={{
                    width: thumb.width,
                    height: thumb.height,
                    backgroundImage: `url("${thumb.url}")`,
                    backgroundPosition: `-${String(thumb.x)}px -${String(thumb.y)}px`,
                  }}
                />
              ) : null}
              <span className="rounded-badge bg-black/80 px-1.5 py-0.5 text-xs tabular-nums">
                {clock(preview.time, language)}
              </span>
            </div>
          ) : null}
        </div>

        <div dir="ltr" className="flex items-center gap-1 sm:gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="text-white hover:bg-white/15 hover:text-white"
            aria-label={playing ? t("player.pause") : t("player.play")}
            onClick={togglePlay}
          >
            {playing ? (
              <Pause aria-hidden="true" className="fill-current" />
            ) : (
              <Play aria-hidden="true" className="fill-current" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="text-white hover:bg-white/15 hover:text-white"
            aria-label={t("player.back10")}
            onClick={() => {
              run({ type: "seek-by", seconds: -SEEK_STEP_S });
            }}
          >
            <RotateCcw aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="text-white hover:bg-white/15 hover:text-white"
            aria-label={t("player.forward10")}
            onClick={() => {
              run({ type: "seek-by", seconds: SEEK_STEP_S });
            }}
          >
            <RotateCw aria-hidden="true" />
          </Button>
          <div className="group/volume flex items-center">
            <Button
              variant="ghost"
              size="icon"
              className="text-white hover:bg-white/15 hover:text-white"
              aria-label={volume === 0 ? t("player.unmute") : t("player.mute")}
              onClick={toggleMute}
            >
              <VolumeIcon aria-hidden="true" />
            </Button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={volume}
              aria-label={t("player.volume")}
              aria-valuetext={`${String(Math.round(volume * 100))}%`}
              className="hidden h-1 w-20 cursor-pointer accent-[var(--primary)] sm:block"
              onChange={(event) => {
                const video = videoRef.current;
                if (!video) return;
                video.volume = Number(event.target.value);
                video.muted = video.volume === 0;
              }}
            />
          </div>
          <span className="ms-1 text-xs tabular-nums text-white/85 sm:text-sm">
            {clock(time, language)} / {clock(duration, language)}
          </span>

          <div className="ms-auto flex items-center gap-1 sm:gap-2">
            {next ? (
              <Button
                variant="ghost"
                size="icon"
                className="text-white hover:bg-white/15 hover:text-white"
                aria-label={t("player.nextEpisode", { title: next.label })}
                onClick={next.play}
              >
                <SkipForward aria-hidden="true" />
              </Button>
            ) : null}
            {textOptions.length > 0 ? (
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  "text-white hover:bg-white/15 hover:text-white",
                  activeText !== null && "text-primary hover:text-primary",
                )}
                aria-label={t("player.captions")}
                aria-pressed={activeText !== null}
                onClick={toggleCaptions}
              >
                <Captions aria-hidden="true" />
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="icon"
              className="text-white hover:bg-white/15 hover:text-white"
              aria-label={t("player.settings")}
              aria-expanded={settingsOpen}
              aria-controls={settingsId}
              onClick={() => {
                setSettingsOpen((open) => !open);
              }}
            >
              <Settings aria-hidden="true" />
            </Button>
            {pipSupported ? (
              <Button
                variant="ghost"
                size="icon"
                className="hidden text-white hover:bg-white/15 hover:text-white sm:inline-flex"
                aria-label={pip ? t("player.pipExit") : t("player.pip")}
                aria-pressed={pip}
                onClick={() => {
                  const video = videoRef.current;
                  if (!video) return;
                  if (document.pictureInPictureElement)
                    void document.exitPictureInPicture().catch(() => undefined);
                  else void video.requestPictureInPicture().catch(() => undefined);
                }}
              >
                <PictureInPicture2 aria-hidden="true" />
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="icon"
              className="text-white hover:bg-white/15 hover:text-white"
              aria-label={fullscreen ? t("player.exitFullscreen") : t("player.fullscreen")}
              onClick={toggleFullscreen}
            >
              {fullscreen ? <Minimize aria-hidden="true" /> : <Maximize aria-hidden="true" />}
            </Button>
          </div>
        </div>
      </div>

      {/* Settings: quality, audio, subtitles and their style, speed. Inside the player so it shows in full screen. */}
      {settingsOpen ? (
        // Anchored above the settings button, which sits at the right in both languages
        // (the controls are left-to-right); the panel's own text follows the language.
        <div
          dir="ltr"
          className="pointer-events-none absolute inset-x-3 bottom-24 flex justify-end sm:inset-x-6"
        >
          <div
            id={settingsId}
            role="dialog"
            aria-label={t("player.settings")}
            dir={i18n.dir(language)}
            className="pointer-events-auto grid max-h-[70dvh] w-72 gap-4 overflow-y-auto rounded-card bg-black/90 p-3 ring-1 ring-white/15 backdrop-blur-md"
          >
            {heights.length > 0 ? (
              <SettingsGroup<string>
                legend={t("player.quality")}
                name={`${settingsId}-quality`}
                value={autoQuality ? "auto" : String(activeHeight ?? "auto")}
                options={[
                  {
                    value: "auto",
                    label:
                      activeHeight && autoQuality
                        ? t("player.autoWith", { quality: qualityLabel(activeHeight) })
                        : t("player.auto"),
                  },
                  ...heights.map((height) => ({
                    value: String(height),
                    label: qualityLabel(height),
                  })),
                ]}
                onChange={(value) => {
                  selectQuality(value === "auto" ? "auto" : Number(value));
                }}
              />
            ) : (
              <p className="px-2 text-sm text-white/75">
                {t("player.qualityFixed", { quality: qualityLabel(grant.height) })}
              </p>
            )}
            {audioOptions.length > 1 ? (
              <SettingsGroup<string>
                legend={t("player.audio")}
                name={`${settingsId}-audio`}
                value={audioOptions.find((option) => option.active)?.key ?? ""}
                options={audioOptions.map((option) => ({ value: option.key, label: option.label }))}
                onChange={selectAudio}
              />
            ) : null}
            {textOptions.length > 0 ? (
              <>
                <SettingsGroup<string>
                  legend={t("player.subtitles")}
                  name={`${settingsId}-text`}
                  value={activeText === null ? "off" : String(activeText)}
                  options={[
                    { value: "off", label: t("player.off") },
                    ...textOptions.map((option) => ({
                      value: String(option.id),
                      label: option.label,
                    })),
                  ]}
                  onChange={(value) => {
                    selectText(value === "off" ? null : Number(value));
                  }}
                />
                <SettingsGroup<SubtitleSize>
                  legend={t("player.subtitleSize")}
                  name={`${settingsId}-size`}
                  value={preferences.subtitleSize}
                  options={(Object.keys(SUBTITLE_SIZES) as SubtitleSize[]).map((size) => ({
                    value: size,
                    label: t(`player.sizes.${size}`),
                  }))}
                  onChange={(subtitleSize) => {
                    updatePreferences({ subtitleSize });
                  }}
                />
                <SettingsGroup<string>
                  legend={t("player.subtitleBackground")}
                  name={`${settingsId}-background`}
                  value={preferences.subtitleBackground ? "on" : "off"}
                  options={[
                    { value: "on", label: t("player.backgroundOn") },
                    { value: "off", label: t("player.backgroundOff") },
                  ]}
                  onChange={(value) => {
                    updatePreferences({ subtitleBackground: value === "on" });
                  }}
                />
              </>
            ) : null}
            <SettingsGroup<number>
              legend={t("player.speed")}
              name={`${settingsId}-speed`}
              value={speed}
              options={SPEEDS.map((value) => ({
                value,
                label: value === 1 ? t("player.normalSpeed") : `${String(value)}×`,
              }))}
              onChange={(value) => {
                setSpeed(value);
                if (videoRef.current) videoRef.current.playbackRate = value;
              }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
