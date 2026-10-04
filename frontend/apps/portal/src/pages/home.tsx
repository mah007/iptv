import { useHomeRetrieve, type HomeRow } from "@smart-iptv/api-portal";
import { Button, EmptyState, ErrorState, Skeleton } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Clapperboard } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Hero } from "../components/hero";
import { LazyMount } from "../components/lazy-mount";
import { Rail, RailSkeleton } from "../components/rail";
import { TitleCard } from "../components/title-card";
import { WatchCard } from "../components/watch-card";
import { usePageTitle } from "../lib/page-title";

/** "See all" for rows that have a page of their own (a collection, a category). */
function RowAction({ row }: { row: HomeRow }) {
  const { t } = useTranslation();
  const label = (
    <>
      {t("home.seeAll")}
      <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
    </>
  );
  if (row.kind === "collection" && row.collection_slug) {
    return (
      <Button asChild variant="link" size="sm" className="me-2">
        <Link to="/collections/$slug" params={{ slug: row.collection_slug }}>
          {label}
        </Link>
      </Button>
    );
  }
  if (row.kind === "category" && row.category) {
    const to = row.category.kind === "series" ? "/series" : "/movies";
    return (
      <Button asChild variant="link" size="sm" className="me-2">
        <Link to={to} search={{ category: row.category.id }}>
          {label}
        </Link>
      </Button>
    );
  }
  return null;
}

function HomeSkeleton() {
  const { t } = useTranslation();
  return (
    <div aria-busy="true">
      <span className="sr-only" role="status">
        {t("states.loading")}
      </span>
      <Skeleton className="h-[30rem] w-full rounded-none sm:h-[36rem]" />
      <div className="mt-8 grid gap-10">
        <RailSkeleton landscape />
        <RailSkeleton />
      </div>
    </div>
  );
}

/**
 * Home (SPEC §9, ADR-0013 §6): the hero carousel, continue watching, then the
 * rows in the API's order. Rows below the fold mount as they scroll near.
 */
export function HomePage() {
  const { t } = useTranslation();
  usePageTitle();
  const home = useHomeRetrieve({ query: { staleTime: 30_000, refetchOnWindowFocus: true } });

  if (home.isPending) return <HomeSkeleton />;
  if (home.isError) {
    return (
      <div className="page-top px-4">
        <ErrorState
          title={t("home.error")}
          error={home.error}
          onRetry={() => {
            void home.refetch();
          }}
        />
      </div>
    );
  }

  const { hero, continue_watching: continueWatching, rows } = home.data;
  const empty = hero.length === 0 && continueWatching.length === 0 && rows.length === 0;

  return (
    <div>
      {hero.length > 0 ? <Hero items={hero} /> : <div className="page-top" />}
      <h1 className="sr-only">{t("home.title")}</h1>
      {empty ? (
        <EmptyState
          className="py-24"
          icon={<Clapperboard />}
          title={t("home.emptyTitle")}
          description={t("home.emptyDescription")}
        />
      ) : null}
      <div className="relative z-10 -mt-8 grid gap-10 sm:-mt-12">
        {continueWatching.length > 0 ? (
          <Rail title={t("home.continueWatching")} itemClassName="w-64 sm:w-72">
            {continueWatching.map((item) => (
              <WatchCard key={`${item.type}:${item.episode?.id ?? item.title.id}`} item={item} />
            ))}
          </Rail>
        ) : null}
        {rows.map((row, index) =>
          row.items.length === 0 ? null : (
            <LazyMount
              key={row.key}
              placeholder={<RailSkeleton />}
              rootMargin={index < 2 ? "2000px 0px" : "600px 0px"}
            >
              <Rail title={row.title} action={<RowAction row={row} />}>
                {row.items.map((item) => (
                  <TitleCard key={`${item.type}:${item.id}`} title={item} />
                ))}
              </Rail>
            </LazyMount>
          ),
        )}
      </div>
    </div>
  );
}
