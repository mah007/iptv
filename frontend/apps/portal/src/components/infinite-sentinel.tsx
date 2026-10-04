import { Button } from "@smart-iptv/ui";
import { useEffect, useEffectEvent, useRef } from "react";
import { useTranslation } from "react-i18next";

/**
 * The end of an infinite list: loads the next page when it scrolls near, and
 * offers a button for keyboards, screen readers and browsers without
 * IntersectionObserver.
 */
export function InfiniteSentinel({
  hasMore,
  loading,
  onLoadMore,
}: {
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const load = useEffectEvent(onLoadMore);

  useEffect(() => {
    const element = ref.current;
    if (!hasMore || loading || element === null || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) load();
      },
      { rootMargin: "800px 0px" },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [hasMore, loading]);

  if (!hasMore) return null;
  return (
    <div ref={ref} className="flex justify-center py-6">
      <Button variant="secondary" pending={loading} onClick={onLoadMore}>
        {loading ? t("states.loadingMore") : t("states.loadMore")}
      </Button>
    </div>
  );
}
