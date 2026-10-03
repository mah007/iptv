import { CircleHelp, Monitor, Smartphone, Tablet, Tv, type LucideProps } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";

export type DeviceKind = "tv" | "smartphone" | "tablet" | "monitor" | "unknown";

// Order matters: "Android TV" is a TV and "Galaxy Tab" a tablet before either is a phone.
const PATTERNS: readonly (readonly [DeviceKind, RegExp])[] = [
  [
    "tv",
    /\btv\b|tvos|smart ?tv|android ?tv|google ?tv|apple ?tv|fire ?(tv|stick)|\baft[a-z]|shield|tizen|web0?os|roku|\bmag\d*\b|formuler|tivimate|ott ?navigator|xciptv|\bibo\b|bravia|chromecast|set-?top|\bstb\b/u,
  ],
  ["tablet", /tablet|ipad|galaxy ?tab|kindle|\btab\b/u],
  ["smartphone", /smartphone|phone|mobile|android|\bios\b/u],
  [
    "monitor",
    /monitor|desktop|computer|\bpc\b|\bweb\b|browser|windows|macintosh|mac ?os|linux|chrome|firefox|safari|\bedge\b|vlc|iptvnator/u,
  ],
];

/** Device kind from an explicit kind, an app name or a user agent. */
export function deviceKind(hint: string | null | undefined): DeviceKind {
  const text = (hint ?? "").toLowerCase();
  if (text === "") return "unknown";
  for (const [kind, pattern] of PATTERNS) {
    if (pattern.test(text)) return kind;
  }
  return "unknown";
}

const ICONS = {
  tv: Tv,
  smartphone: Smartphone,
  tablet: Tablet,
  monitor: Monitor,
  unknown: CircleHelp,
} as const;

export interface DeviceIconProps extends Omit<LucideProps, "ref"> {
  /** Kind ("tv", "smartphone"…), app name ("TiviMate") or user agent. */
  hint?: string | null | undefined;
  /** Hide from assistive tech when the device name is printed next to it. */
  decorative?: boolean;
}

/** Icon for the kind of device a session or credential uses, labelled for screen readers. */
export function DeviceIcon({ hint, decorative = false, className, ...props }: DeviceIconProps) {
  const { t } = useTranslation("ui");
  const kind = deviceKind(hint);
  const Icon = ICONS[kind];
  return (
    <Icon
      data-slot="device-icon"
      data-kind={kind}
      {...(decorative
        ? { "aria-hidden": true }
        : { role: "img", "aria-label": t(`device.${kind}`) })}
      className={cn("size-4 shrink-0 text-muted-foreground", className)}
      {...props}
    />
  );
}
