import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Badge, type BadgeTone } from "./badge";

/**
 * What the badges are computed from: the probe summary of a media file
 * (SPEC §6 `MediaFile` and its audio tracks). Field names match the API.
 */
export interface MediaSummary {
  width?: number | null | undefined;
  height?: number | null | undefined;
  /** ffprobe `codec_name` of the video stream: hevc, h264, av1… */
  video_codec?: string | null | undefined;
  /** `sdr`, `hdr10`, `hlg` or `dv`. */
  hdr?: string | null | undefined;
  /** Channels of the best audio track (6 = 5.1, 8 = 7.1). */
  audio_channels?: number | null | undefined;
  has_atmos?: boolean | null | undefined;
}

export type QualityKey =
  | "uhd"
  | "fhd"
  | "hd"
  | "sd"
  | "hdr10"
  | "hlg"
  | "dv"
  | "surround71"
  | "surround51"
  | "atmos"
  | "hevc"
  | "av1";

function resolutionKey(width: number, height: number): QualityKey | null {
  if (width <= 0 && height <= 0) return null;
  // Width matters too: a 3840×1600 scope picture is still 4K.
  if (width >= 3200 || height >= 2000) return "uhd";
  if (width >= 1800 || height >= 1000) return "fhd";
  if (width >= 1200 || height >= 700) return "hd";
  return "sd";
}

const HDR_KEYS: Record<string, QualityKey> = {
  hdr10: "hdr10",
  hdr10plus: "hdr10",
  "hdr10+": "hdr10",
  hlg: "hlg",
  dv: "dv",
  dovi: "dv",
  dolby_vision: "dv",
};

const CODEC_KEYS: Record<string, QualityKey> = { hevc: "hevc", h265: "hevc", av1: "av1" };

/** The badges a file earns, in display order: resolution, HDR, audio, codec. */
export function qualityKeys(media: MediaSummary): QualityKey[] {
  const keys: QualityKey[] = [];
  const resolution = resolutionKey(media.width ?? 0, media.height ?? 0);
  if (resolution) keys.push(resolution);
  const hdr = HDR_KEYS[(media.hdr ?? "").toLowerCase()];
  if (hdr) keys.push(hdr);
  const channels = media.audio_channels ?? 0;
  if (channels >= 8) keys.push("surround71");
  else if (channels >= 6) keys.push("surround51");
  if (media.has_atmos) keys.push("atmos");
  const codec = CODEC_KEYS[(media.video_codec ?? "").toLowerCase()];
  if (codec) keys.push(codec);
  return keys;
}

const TONE: Partial<Record<QualityKey, BadgeTone>> = {
  uhd: "primary",
  hdr10: "violet",
  hlg: "violet",
  dv: "violet",
};

const OVERLAY = "bg-black/55 text-white ring-white/20 backdrop-blur-sm";

export interface QualityBadgesProps extends Omit<ComponentProps<"ul">, "children"> {
  media: MediaSummary;
  /** Light-on-dark chips for use over artwork. */
  overlay?: boolean;
}

/** Compact 4K / HDR10 / 5.1 / Atmos / HEVC chips, each with a spoken full name. */
export function QualityBadges({ media, overlay = false, className, ...props }: QualityBadgesProps) {
  const { t } = useTranslation("ui");
  const keys = qualityKeys(media);
  if (keys.length === 0) return null;
  return (
    <ul
      role="list"
      data-slot="quality-badges"
      aria-label={t("quality.label")}
      className={cn("flex flex-wrap items-center gap-1", className)}
      {...props}
    >
      {keys.map((key) => (
        <li key={key} data-quality={key} className="flex">
          <Badge
            tone={overlay ? "neutral" : (TONE[key] ?? "neutral")}
            className={cn("h-4.5 px-1 text-[0.6875rem] font-semibold", overlay && OVERLAY)}
          >
            <span aria-hidden="true">{t(`quality.short.${key}`)}</span>
            <span className="sr-only">{t(`quality.long.${key}`)}</span>
          </Badge>
        </li>
      ))}
    </ul>
  );
}
