import { decode } from "blurhash";
import { Film, ImageOff } from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps } from "react";

import { cn } from "../lib/cn";

export type ImageFormat = "avif" | "webp";

/** URL of one stored rendition, e.g. for signed or CDN URLs: `(size, format) => url`. */
export type ImageUrlResolver = (size: string, format: ImageFormat) => string;

type ArtworkKind = "poster" | "backdrop";

/** Stored widths per kind (SPEC §7.2): posters w185/w500, backdrops w780/w1280. */
const RENDITIONS: Record<ArtworkKind, readonly { size: string; width: number }[]> = {
  poster: [
    { size: "w185", width: 185 },
    { size: "w500", width: 500 },
  ],
  backdrop: [
    { size: "w780", width: 780 },
    { size: "w1280", width: 1280 },
  ],
};

const ASPECT: Record<ArtworkKind, string> = { poster: "aspect-[2/3]", backdrop: "aspect-video" };

/** Blurhash decode size: tiny, the browser smooths it when stretching the canvas. */
const BLUR_SIZE: Record<ArtworkKind, readonly [number, number]> = {
  poster: [20, 30],
  backdrop: [32, 18],
};

const DEFAULT_SIZES: Record<ArtworkKind, string> = { poster: "185px", backdrop: "100vw" };

function resolverFor(src: string | ImageUrlResolver): ImageUrlResolver {
  if (typeof src === "function") return src;
  // Storage layout `{base}/{size}.{ext}` (SPEC §7.2).
  const base = src.replace(/\/+$/u, "");
  return (size, format) => `${base}/${size}.${format}`;
}

function srcSet(resolve: ImageUrlResolver, kind: ArtworkKind, format: ImageFormat): string {
  return RENDITIONS[kind]
    .map(({ size, width }) => `${resolve(size, format)} ${String(width)}w`)
    .join(", ");
}

function BlurhashCanvas({ hash, kind }: { hash: string; kind: ArtworkKind }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [width, height] = BLUR_SIZE[kind];
  useEffect(() => {
    const context = ref.current?.getContext("2d");
    if (!context) return;
    try {
      const image = context.createImageData(width, height);
      image.data.set(decode(hash, width, height));
      context.putImageData(image, 0, 0);
    } catch {
      // A malformed hash leaves the neutral backdrop; the image still loads.
    }
  }, [hash, width, height]);
  return (
    <canvas
      ref={ref}
      width={width}
      height={height}
      aria-hidden="true"
      data-slot="blurhash"
      className="absolute inset-0 size-full"
    />
  );
}

export interface PosterImageProps extends Omit<ComponentProps<"div">, "children"> {
  /**
   * Folder URL of the stored image, served as `{src}/{size}.{avif|webp}`, or a
   * resolver for other URL schemes. Empty shows a placeholder icon.
   */
  src?: string | ImageUrlResolver | null | undefined;
  /** Shown blurred while the image loads. */
  blurhash?: string | null | undefined;
  /** What the image shows; pass "" when the title is printed next to it. */
  alt: string;
  /** Rendered width hint so the browser picks a size, e.g. "(min-width: 1024px) 300px, 40vw". */
  sizes?: string;
  /** Above the fold (e.g. a hero backdrop): load eagerly at high priority. */
  priority?: boolean;
}

interface ArtworkProps extends PosterImageProps {
  kind: ArtworkKind;
}

function Artwork({
  kind,
  src,
  blurhash,
  alt,
  sizes = DEFAULT_SIZES[kind],
  priority = false,
  className,
  ...props
}: ArtworkProps) {
  const resolve = src ? resolverFor(src) : null;
  const largest = RENDITIONS[kind][RENDITIONS[kind].length - 1]?.size ?? "original";
  const fallback = resolve ? resolve(largest, "webp") : null;
  // Remember which URL loaded or failed, so a new `src` starts over without an effect.
  const [loaded, setLoaded] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const showImage = resolve !== null && fallback !== null && failed !== fallback;
  const isLoaded = showImage && loaded === fallback;
  const Placeholder = resolve === null ? Film : ImageOff;

  return (
    <div
      data-slot={kind === "poster" ? "poster-image" : "backdrop-image"}
      data-state={showImage ? (isLoaded ? "loaded" : "loading") : "empty"}
      className={cn("relative isolate overflow-hidden bg-muted", ASPECT[kind], className)}
      {...props}
    >
      {showImage && blurhash && !isLoaded ? <BlurhashCanvas hash={blurhash} kind={kind} /> : null}
      {showImage ? (
        <picture>
          <source type="image/avif" srcSet={srcSet(resolve, kind, "avif")} sizes={sizes} />
          <source type="image/webp" srcSet={srcSet(resolve, kind, "webp")} sizes={sizes} />
          <img
            src={fallback}
            alt={alt}
            loading={priority ? "eager" : "lazy"}
            decoding="async"
            fetchPriority={priority ? "high" : "auto"}
            onLoad={() => {
              setLoaded(fallback);
            }}
            onError={() => {
              setFailed(fallback);
            }}
            className={cn(
              "absolute inset-0 size-full object-cover transition-opacity duration-200 ease-out",
              isLoaded ? "opacity-100" : "opacity-0",
            )}
          />
        </picture>
      ) : (
        <div
          data-slot="artwork-fallback"
          {...(alt ? { role: "img", "aria-label": alt } : { "aria-hidden": true })}
          className="absolute inset-0 grid place-items-center text-muted-foreground/60"
        >
          <Placeholder aria-hidden="true" className={kind === "poster" ? "size-8" : "size-10"} />
        </div>
      )}
    </div>
  );
}

/** 2:3 poster: blurhash placeholder, then AVIF or WebP at w185/w500, lazy-loaded. */
export function PosterImage(props: PosterImageProps) {
  return <Artwork kind="poster" {...props} />;
}

/** 16:9 backdrop: blurhash placeholder, then AVIF or WebP at w780/w1280, lazy-loaded. */
export function BackdropImage(props: PosterImageProps) {
  return <Artwork kind="backdrop" {...props} />;
}
