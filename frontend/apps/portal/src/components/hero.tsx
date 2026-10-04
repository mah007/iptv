import type { Hero as HeroItem } from "@smart-iptv/api-portal";
import { Button, cn } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Info, Pause, Play } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { artworkSource, artworkUrl } from "../lib/artwork";
import { titlePath, watchPath } from "../lib/links";
import { TitleMeta } from "./title-meta";

const ADVANCE_MS = 9000;

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Backdrop as a full-bleed picture: AVIF or WebP at the width the screen needs. */
function HeroBackdrop({ item, priority }: { item: HeroItem; priority: boolean }) {
  const source = artworkSource(item.backdrop);
  if (source === null) return <div className="absolute inset-0 bg-muted" />;
  const srcSet = (format: "avif" | "webp") =>
    [
      ["w780", 780],
      ["w1280", 1280],
    ]
      .map(([size, width]) => `${source(String(size), format)} ${String(width)}w`)
      .join(", ");
  return (
    <picture>
      <source type="image/avif" srcSet={srcSet("avif")} sizes="100vw" />
      <source type="image/webp" srcSet={srcSet("webp")} sizes="100vw" />
      <img
        src={source("w1280", "webp")}
        alt=""
        loading={priority ? "eager" : "lazy"}
        fetchPriority={priority ? "high" : "auto"}
        decoding="async"
        className="absolute inset-0 size-full object-cover object-top"
      />
    </picture>
  );
}

/**
 * The home carousel (SPEC §9): featured titles with their backdrop, logo and
 * overview, Play and More info. It advances on its own unless the viewer
 * pauses it, hovers or focuses it, or prefers reduced motion.
 */
export function Hero({ items }: { items: readonly HeroItem[] }) {
  const { t } = useTranslation();
  const regionId = useId();
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(prefersReducedMotion);
  const [holding, setHolding] = useState(false);
  const count = items.length;
  const item = items[Math.min(index, count - 1)];

  useEffect(() => {
    if (paused || holding || count < 2) return;
    const timer = window.setTimeout(() => {
      setIndex((current) => (current + 1) % count);
    }, ADVANCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [index, paused, holding, count]);

  if (item === undefined) return null;
  const logo = artworkUrl(item.logo, "w500");
  const go = (step: number) => {
    setIndex((current) => (current + step + count) % count);
  };

  return (
    <section
      aria-roledescription={t("hero.carousel")}
      aria-label={t("hero.label")}
      className="relative isolate min-h-[30rem] overflow-hidden sm:min-h-[36rem] lg:min-h-[42rem]"
      onPointerEnter={() => {
        setHolding(true);
      }}
      onPointerLeave={() => {
        setHolding(false);
      }}
      onFocus={() => {
        setHolding(true);
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setHolding(false);
      }}
    >
      <div key={item.id} className="absolute inset-0 -z-10 animate-fade-in">
        <HeroBackdrop item={item} priority={index === 0} />
      </div>
      {/* Legibility: fade to the page at the bottom and towards the text's side. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-linear-to-t from-background via-background/40 to-background/10"
      />
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-linear-to-r from-background/85 via-background/30 to-transparent rtl:bg-linear-to-l"
      />

      <div
        id={regionId}
        aria-live={paused ? "polite" : "off"}
        className="mx-auto flex min-h-[30rem] max-w-[1800px] flex-col justify-end gap-4 px-4 pt-24 pb-14 sm:min-h-[36rem] sm:px-6 lg:min-h-[42rem] lg:px-10"
      >
        <div
          role="group"
          aria-roledescription={t("hero.slide")}
          aria-label={t("hero.position", { index: index + 1, count })}
          className="grid max-w-xl gap-4"
        >
          {logo ? (
            <h2 className="m-0">
              <img
                src={logo}
                alt={item.title}
                className="max-h-24 w-auto max-w-[min(22rem,80%)] object-contain object-start drop-shadow-lg sm:max-h-32"
              />
            </h2>
          ) : (
            <h2 className="text-3xl font-bold text-foreground sm:text-5xl ltr:tracking-tight">
              {item.title}
            </h2>
          )}
          <TitleMeta
            year={item.year}
            rating={item.rating}
            runtimeMin={item.runtime_min}
            certification={item.certification}
            className="text-foreground/90"
          />
          {item.overview ? (
            <p className="line-clamp-3 text-sm text-foreground/85 sm:text-base">{item.overview}</p>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button asChild size="lg">
              <Link {...watchPath(item.type, item.id)}>
                <Play aria-hidden="true" className="fill-current" />
                {t("title.play")}
              </Link>
            </Button>
            <Button
              asChild
              size="lg"
              variant="secondary"
              className="bg-background/60 backdrop-blur-sm"
            >
              <Link {...titlePath(item.type, item.id)}>
                <Info aria-hidden="true" />
                {t("hero.moreInfo")}
              </Link>
            </Button>
          </div>
        </div>
        {count > 1 ? (
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={paused ? t("hero.play") : t("hero.pause")}
              onClick={() => {
                setPaused((current) => !current);
              }}
            >
              {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t("hero.previous")}
              aria-controls={regionId}
              onClick={() => {
                go(-1);
              }}
            >
              <ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />
            </Button>
            <div className="flex items-center">
              {items.map((slide, slideIndex) => (
                <button
                  key={slide.id}
                  type="button"
                  aria-label={t("hero.goTo", { title: slide.title })}
                  aria-current={slideIndex === index || undefined}
                  className="group/dot grid h-6 min-w-6 place-items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => {
                    setIndex(slideIndex);
                  }}
                >
                  <span
                    className={cn(
                      "h-1.5 rounded-full transition-all duration-200",
                      slideIndex === index
                        ? "w-6 bg-foreground"
                        : "w-1.5 bg-foreground/40 group-hover/dot:bg-foreground/70",
                    )}
                  />
                </button>
              ))}
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t("hero.next")}
              aria-controls={regionId}
              onClick={() => {
                go(1);
              }}
            >
              <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
