/**
 * Shaka Player (Apache-2.0), HLS build: loaded with the player page only.
 * The types come from the package's generated declarations.
 */
import type shakaNamespace from "shaka-player/dist/shaka-player.hls-es2021";

export type Shaka = typeof shakaNamespace;
export type ShakaPlayer = InstanceType<Shaka["Player"]>;

let loading: Promise<Shaka> | null = null;

export function loadShaka(): Promise<Shaka> {
  loading ??= import("shaka-player/dist/shaka-player.hls-es2021").then((module) => {
    const shaka = module.default;
    shaka.polyfill.installAll();
    return shaka;
  });
  return loading;
}

/** Distinct video heights, tallest first: the quality menu's rungs (within the plan's ceiling). */
export function qualityHeights(tracks: readonly { height: number | null }[]): number[] {
  const heights = new Set<number>();
  for (const track of tracks) if (track.height) heights.add(track.height);
  return [...heights].sort((a, b) => b - a);
}

/** "1080p", "4K" for 2160 and above. */
export function qualityLabel(height: number): string {
  return height >= 2160 ? "4K" : `${String(height)}p`;
}
