import {
  moviesList,
  seriesList,
  useCategoriesList,
  useGenresList,
  type PaginatedTitleCardList,
} from "@smart-iptv/api-portal";
import {
  Button,
  EmptyState,
  ErrorState,
  PosterGrid,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@smart-iptv/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Clapperboard, X } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { InfiniteSentinel } from "../components/infinite-sentinel";
import { TitleCard } from "../components/title-card";
import { cursorOf } from "../lib/cursor";
import { usePageTitle } from "../lib/page-title";
import { SORTS, type BrowseSearch, type BrowseSort } from "../search";

const PAGE_SIZE = 30;
const ANY = "__any";
const moviesApi = getRouteApi("/app/shell/movies");
const seriesApi = getRouteApi("/app/shell/series");

type Kind = "movie" | "series";

/** Years to offer: this one back to 1950, newest first. */
function yearOptions(now = new Date()): number[] {
  const current = now.getFullYear();
  return Array.from({ length: current - 1949 }, (_, index) => current - index);
}

/** The movies or series grid, filtered and sorted, a page at a time (cursor pagination). */
export function useTitlePages(kind: Kind, search: BrowseSearch) {
  const params = {
    page_size: PAGE_SIZE,
    ...(search.genre ? { genre: search.genre } : {}),
    ...(search.category ? { category: search.category } : {}),
    ...(search.year ? { year: search.year } : {}),
    ...(search.sort ? { sort: search.sort } : {}),
  };
  return useInfiniteQuery({
    queryKey: [kind === "movie" ? "/api/v1/movies" : "/api/v1/series", params, "pages"],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }): Promise<PaginatedTitleCardList> => {
      const page = pageParam === undefined ? params : { ...params, cursor: pageParam };
      return kind === "movie" ? moviesList(page, { signal }) : seriesList(page, { signal });
    },
    getNextPageParam: (last) => cursorOf(last.next),
  });
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
  anyOption = true,
}: {
  label: string;
  value: string | undefined;
  /** Offer "Any" (no filter); off for choices that always have a value, like the order. */
  anyOption?: boolean;
  options: readonly { value: string; label: string }[];
  onChange: (value: string | undefined) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="grid min-w-36 flex-1 gap-1 sm:flex-none">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <Select
        value={value ?? ANY}
        onValueChange={(next) => {
          onChange(next === ANY ? undefined : next);
        }}
      >
        <SelectTrigger id={id} className="w-full sm:w-44">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {anyOption ? <SelectItem value={ANY}>{t("browse.any")}</SelectItem> : null}
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function BrowsePage({ kind, search }: { kind: Kind; search: BrowseSearch }) {
  const { t } = useTranslation();
  const heading = kind === "movie" ? t("browse.movies") : t("browse.series");
  usePageTitle(heading);
  const navigate = useNavigate();
  const genres = useGenresList({ kind: kind === "movie" ? "movie" : "series" });
  const categories = useCategoriesList({ kind: kind === "movie" ? "vod" : "series" });
  const pages = useTitlePages(kind, search);
  const titles = pages.data?.pages.flatMap((page) => page.results) ?? [];
  const filtered =
    search.genre !== undefined || search.category !== undefined || search.year !== undefined;

  function update(patch: Partial<Record<keyof BrowseSearch, string | number | undefined>>): void {
    const next = Object.fromEntries(
      Object.entries({ ...search, ...patch }).filter(([, value]) => value !== undefined),
    );
    void navigate({ to: kind === "movie" ? "/movies" : "/series", search: next, replace: true });
  }

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{heading}</h1>
      <div className="flex flex-wrap items-end gap-3" role="group" aria-label={t("browse.filters")}>
        <FilterSelect
          label={t("browse.genre")}
          value={search.genre}
          options={(genres.data ?? []).map((genre) => ({ value: genre.id, label: genre.name }))}
          onChange={(genre) => {
            update({ genre });
          }}
        />
        <FilterSelect
          label={t("browse.category")}
          value={search.category}
          options={(categories.data ?? []).map((category) => ({
            value: category.id,
            label: category.name,
          }))}
          onChange={(category) => {
            update({ category });
          }}
        />
        <FilterSelect
          label={t("browse.year")}
          value={search.year === undefined ? undefined : String(search.year)}
          options={yearOptions().map((year) => ({ value: String(year), label: String(year) }))}
          onChange={(year) => {
            update({ year: year === undefined ? undefined : Number(year) });
          }}
        />
        <FilterSelect
          label={t("browse.sort")}
          value={search.sort ?? "added"}
          anyOption={false}
          options={SORTS.map((sort: BrowseSort) => ({
            value: sort,
            label: t(`browse.sorts.${sort}`),
          }))}
          onChange={(sort) => {
            // Recently added is the API's default order: no parameter for it.
            update({ sort: sort === "added" ? undefined : sort });
          }}
        />
        {filtered ? (
          <Button
            variant="ghost"
            size="sm"
            className="mb-0.5"
            onClick={() => {
              update({ genre: undefined, category: undefined, year: undefined });
            }}
          >
            <X aria-hidden="true" />
            {t("browse.clear")}
          </Button>
        ) : null}
      </div>

      {pages.isError ? (
        <ErrorState
          title={t("browse.error")}
          error={pages.error}
          onRetry={() => {
            void pages.refetch();
          }}
        />
      ) : !pages.isPending && titles.length === 0 ? (
        <EmptyState
          icon={<Clapperboard />}
          title={filtered ? t("browse.noMatchTitle") : t("browse.emptyTitle")}
          description={filtered ? t("browse.noMatch") : t("browse.empty")}
        />
      ) : (
        <>
          <PosterGrid loading={pages.isPending} aria-label={heading}>
            {titles.map((title) => (
              <TitleCard key={title.id} title={title} />
            ))}
          </PosterGrid>
          <InfiniteSentinel
            hasMore={pages.hasNextPage}
            loading={pages.isFetchingNextPage}
            onLoadMore={() => {
              void pages.fetchNextPage();
            }}
          />
        </>
      )}
    </div>
  );
}

export function MoviesPage() {
  const search = moviesApi.useSearch();
  return <BrowsePage kind="movie" search={search} />;
}

export function SeriesPage() {
  const search = seriesApi.useSearch();
  return <BrowsePage kind="series" search={search} />;
}
