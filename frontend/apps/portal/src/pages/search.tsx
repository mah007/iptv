import {
  getSearchQueryOptions,
  type SearchHit,
  type SearchResult,
  type SearchType,
} from "@smart-iptv/api-portal";
import { Avatar, BackdropImage, cn, EmptyState, ErrorState, PosterGrid } from "@smart-iptv/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Search as SearchIcon, SearchX, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { TitleCard } from "../components/title-card";
import { useEpisodeLabel } from "../components/watch-card";
import { artworkSource, artworkUrl } from "../lib/artwork";
import { watchPath } from "../lib/links";
import { usePageTitle } from "../lib/page-title";
import { useDebouncedValue } from "../lib/use-debounced-value";

const searchApi = getRouteApi("/app/shell/search");

/** SPEC §9: instant search, debounced 150 ms. */
export const SEARCH_DEBOUNCE_MS = 150;

const FILTERS: readonly (SearchType | "all")[] = ["all", "movie", "series", "episode"];

/** Hits grouped the way the page shows them: movies, series, episodes. */
export function groupHits(hits: readonly SearchHit[]) {
  return {
    movies: hits.filter((hit) => hit.type === "movie"),
    series: hits.filter((hit) => hit.type === "series"),
    episodes: hits.filter((hit) => hit.type === "episode"),
  };
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="grid gap-3">
      <h2 id={id} className="text-lg font-semibold text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function EpisodeHit({ hit }: { hit: SearchHit }) {
  const episodeLabel = useEpisodeLabel();
  const episode = hit.episode;
  if (episode === null) return null;
  const art = episode.still ?? hit.title.backdrop;
  return (
    <Link
      {...watchPath("series", hit.title.id, { episode: episode.id })}
      className="group/hit grid grid-cols-[8rem_1fr] items-center gap-3 rounded-card p-2 outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="overflow-hidden rounded-input ring-1 ring-border/70">
        <BackdropImage src={artworkSource(art)} blurhash={art?.blurhash} alt="" sizes="128px" />
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span className="truncate text-sm font-medium text-foreground">{episode.title}</span>
        <span className="truncate text-xs text-muted-foreground">
          {hit.title.title} · {episodeLabel(episode.season_number, episode.number)}
        </span>
      </span>
    </Link>
  );
}

function Results({ data, filter }: { data: SearchResult; filter: SearchType | "all" }) {
  const { t } = useTranslation();
  const groups = groupHits(data.results);
  const showPeople = filter === "all" && data.people.length > 0;
  if (data.results.length === 0 && !showPeople) {
    return (
      <EmptyState
        icon={<SearchX />}
        title={t("search.noResultsTitle", { query: data.query })}
        description={t("search.noResults")}
      />
    );
  }
  return (
    <div className="grid gap-10">
      <p className="sr-only" role="status">
        {t("search.count", { count: data.total })}
      </p>
      {groups.movies.length > 0 ? (
        <Group title={t("search.movies")}>
          <PosterGrid>
            {groups.movies.map((hit) => (
              <TitleCard key={hit.title.id} title={hit.title} />
            ))}
          </PosterGrid>
        </Group>
      ) : null}
      {groups.series.length > 0 ? (
        <Group title={t("search.series")}>
          <PosterGrid>
            {groups.series.map((hit) => (
              <TitleCard key={hit.title.id} title={hit.title} />
            ))}
          </PosterGrid>
        </Group>
      ) : null}
      {groups.episodes.length > 0 ? (
        <Group title={t("search.episodes")}>
          <ul role="list" className="grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
            {groups.episodes.map((hit) => (
              <li key={hit.episode?.id ?? hit.title.id}>
                <EpisodeHit hit={hit} />
              </li>
            ))}
          </ul>
        </Group>
      ) : null}
      {showPeople ? (
        <Group title={t("search.people")}>
          <ul role="list" className="flex flex-wrap gap-3">
            {data.people.map((person) => (
              <li key={person.id}>
                <Link
                  to="/people/$personId"
                  params={{ personId: person.id }}
                  className="flex items-center gap-2 rounded-full border border-border bg-card py-1 ps-1 pe-4 text-sm font-medium text-foreground outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Avatar
                    name={person.name}
                    src={artworkUrl(person.profile, "w185") ?? undefined}
                    size="lg"
                  />
                  {person.name}
                </Link>
              </li>
            ))}
          </ul>
        </Group>
      ) : null}
    </div>
  );
}

/** Search movies, series, episodes and people, in Arabic or English, typos forgiven. */
export function SearchPage() {
  const { t } = useTranslation();
  usePageTitle(t("search.title"));
  const navigate = useNavigate();
  const search = searchApi.useSearch();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(search.q ?? "");
  const query = useDebouncedValue(text.trim(), SEARCH_DEBOUNCE_MS);
  const filter = search.type ?? "all";

  // Keep the URL in step with what was searched, so results can be shared and survive a reload.
  useEffect(() => {
    if ((search.q ?? "") === query) return;
    void navigate({
      to: "/search",
      search: { ...(query ? { q: query } : {}), ...(search.type ? { type: search.type } : {}) },
      replace: true,
    });
  }, [query, search.q, search.type, navigate]);

  const results = useQuery({
    ...getSearchQueryOptions({
      q: query,
      page_size: 40,
      ...(filter === "all" ? {} : { type: filter }),
    }),
    enabled: query.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      <h1 className="sr-only">{t("search.title")}</h1>
      <form
        role="search"
        className="relative max-w-3xl"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label htmlFor="portal-search" className="sr-only">
          {t("search.label")}
        </label>
        <SearchIcon
          aria-hidden="true"
          className="pointer-events-none absolute start-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
        />
        <input
          ref={inputRef}
          id="portal-search"
          type="search"
          dir="auto"
          autoFocus
          autoComplete="off"
          enterKeyHint="search"
          placeholder={t("search.placeholder")}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className="h-14 w-full rounded-card border border-input bg-card ps-12 pe-12 text-lg text-foreground shadow-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
        />
        {text ? (
          <button
            type="button"
            aria-label={t("search.clear")}
            className="absolute end-2 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => {
              setText("");
              inputRef.current?.focus();
            }}
          >
            <X aria-hidden="true" className="size-5" />
          </button>
        ) : null}
      </form>

      <div role="group" aria-label={t("search.filterLabel")} className="flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={filter === option}
            className={cn(
              "h-9 rounded-full border px-4 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
              filter === option
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
            onClick={() => {
              void navigate({
                to: "/search",
                search: {
                  ...(query ? { q: query } : {}),
                  ...(option === "all" ? {} : { type: option }),
                },
                replace: true,
              });
            }}
          >
            {t(`search.filters.${option}`)}
          </button>
        ))}
      </div>

      <div aria-live="polite" aria-busy={results.isFetching || undefined}>
        {query.length === 0 ? (
          <EmptyState
            icon={<SearchIcon />}
            title={t("search.startTitle")}
            description={t("search.start")}
          />
        ) : results.isError ? (
          <ErrorState
            title={t("search.error")}
            error={results.error}
            onRetry={() => {
              void results.refetch();
            }}
          />
        ) : results.data ? (
          <Results data={results.data} filter={filter} />
        ) : (
          <PosterGrid loading skeletonCount={8} />
        )}
      </div>
    </div>
  );
}
