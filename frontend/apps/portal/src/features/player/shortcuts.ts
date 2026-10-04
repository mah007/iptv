/**
 * Player keyboard shortcuts (SPEC §9): space/k play-pause, ←/→ 10 s back and
 * forward (j/l too), ↑/↓ volume, f full screen, m mute, c captions. Media
 * timelines are not mirrored in Arabic, so ← always goes back.
 */

export type PlayerAction =
  | { type: "toggle-play" }
  | { type: "seek-by"; seconds: number }
  | { type: "seek-to-ratio"; ratio: number }
  | { type: "volume-by"; delta: number }
  | { type: "toggle-fullscreen" }
  | { type: "toggle-mute" }
  | { type: "toggle-captions" };

export const SEEK_STEP_S = 10;
export const VOLUME_STEP = 0.1;

export function shortcutFor(
  event: Pick<KeyboardEvent, "key" | "altKey" | "ctrlKey" | "metaKey">,
): PlayerAction | null {
  if (event.altKey || event.ctrlKey || event.metaKey) return null;
  switch (event.key) {
    case " ":
    case "k":
    case "K":
    case "MediaPlayPause":
      return { type: "toggle-play" };
    case "ArrowLeft":
    case "j":
    case "J":
      return { type: "seek-by", seconds: -SEEK_STEP_S };
    case "ArrowRight":
    case "l":
    case "L":
      return { type: "seek-by", seconds: SEEK_STEP_S };
    case "ArrowUp":
      return { type: "volume-by", delta: VOLUME_STEP };
    case "ArrowDown":
      return { type: "volume-by", delta: -VOLUME_STEP };
    case "f":
    case "F":
      return { type: "toggle-fullscreen" };
    case "m":
    case "M":
      return { type: "toggle-mute" };
    case "c":
    case "C":
      return { type: "toggle-captions" };
    case "Home":
      return { type: "seek-to-ratio", ratio: 0 };
    default:
      if (/^[0-9]$/u.test(event.key))
        return { type: "seek-to-ratio", ratio: Number(event.key) / 10 };
      return null;
  }
}

/** Keys typed into a control keep their usual meaning (a focused button, a radio, a slider). */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return type !== "range" && type !== "button" && type !== "checkbox";
  }
  return false;
}
