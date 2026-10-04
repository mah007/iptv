import { Button, cn, Skeleton } from "@smart-iptv/ui";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface RailProps {
  title: ReactNode;
  /** A "See all" link or similar, after the title. */
  action?: ReactNode;
  /** Card width classes (each item is wrapped in an <li> of this width). */
  itemClassName?: string;
  children: ReactNode[];
  className?: string;
}

/** Which ends of a horizontal scroller the viewer can still scroll towards. */
function useScrollEnds(ref: React.RefObject<HTMLUListElement | null>) {
  const [ends, setEnds] = useState({ start: false, end: false });
  const update = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    // scrollLeft is 0 at the start in LTR and runs negative in RTL.
    const scrolled = Math.abs(element.scrollLeft);
    const room = element.scrollWidth - element.clientWidth;
    setEnds({ start: scrolled > 4, end: room - scrolled > 4 });
  }, [ref]);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    update();
    element.addEventListener("scroll", update, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element);
    return () => {
      element.removeEventListener("scroll", update);
      observer?.disconnect();
    };
  }, [ref, update]);
  return ends;
}

/**
 * A titled horizontal row of cards (SPEC §9 home rows). Swipe or scroll it;
 * on wider screens the arrow buttons page through it, mirrored in Arabic.
 */
export function Rail({ title, action, itemClassName, children, className }: RailProps) {
  const { t } = useTranslation();
  const headingId = useId();
  const listRef = useRef<HTMLUListElement>(null);
  const ends = useScrollEnds(listRef);

  function page(direction: 1 | -1): void {
    const element = listRef.current;
    if (!element) return;
    const rtl = getComputedStyle(element).direction === "rtl";
    const distance = element.clientWidth * 0.85 * direction * (rtl ? -1 : 1);
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollBy({ left: distance, behavior: smooth ? "smooth" : "auto" });
  }

  return (
    <section aria-labelledby={headingId} className={cn("group/rail grid gap-3", className)}>
      <div className="flex items-end justify-between gap-3 px-4 sm:px-6 lg:px-10">
        <h2 id={headingId} className="text-lg font-semibold text-foreground sm:text-xl">
          {title}
        </h2>
        <div className="flex items-center gap-1">
          {action}
          {ends.start || ends.end ? (
            <>
              <Button
                variant="ghost"
                size="icon-sm"
                className="hidden sm:inline-flex"
                aria-label={t("rail.previous")}
                disabled={!ends.start}
                onClick={() => {
                  page(-1);
                }}
              >
                <ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                className="hidden sm:inline-flex"
                aria-label={t("rail.next")}
                disabled={!ends.end}
                onClick={() => {
                  page(1);
                }}
              >
                <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
              </Button>
            </>
          ) : null}
        </div>
      </div>
      <ul
        ref={listRef}
        role="list"
        className="rail gap-3 scroll-px-4 px-4 pb-2 sm:scroll-px-6 sm:px-6 lg:scroll-px-10 lg:px-10"
      >
        {children.map((child, index) => (
          <li key={index} className={cn("w-32 shrink-0 sm:w-40 lg:w-44", itemClassName)}>
            {child}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The shape of a rail while its data loads. */
export function RailSkeleton({ landscape = false }: { landscape?: boolean }) {
  return (
    <div className="grid gap-3" aria-hidden="true">
      <Skeleton className="mx-4 h-6 w-48 sm:mx-6 lg:mx-10" />
      <div className="flex gap-3 overflow-hidden px-4 sm:px-6 lg:px-10">
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton
            key={index}
            className={cn(
              "shrink-0 rounded-card",
              landscape ? "aspect-video w-64 sm:w-72" : "aspect-[2/3] w-32 sm:w-40 lg:w-44",
            )}
          />
        ))}
      </div>
    </div>
  );
}
