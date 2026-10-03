import {
  TitleStatus,
  useCategoriesList,
  useLibrariesList,
  useMoviesList,
  useSeriesList,
  type MovieSummary,
  type MoviesListParams,
  type SeriesSummary,
} from "@smart-iptv/api";
import {

  Button,
  DataTable,
  EmptyState,
  ErrorState,
  FacetFilter,
  FilterBar,
  PageHeader,
  PosterCard,
  PosterGrid,
  PosterImage,
  RelativeTime,
  SearchInput,
  StatusBadge,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
  sortingFromOrdering,
  sortingToOrdering,
  useFormatters,
  type FilterChip,
} from "@smart-iptv/ui";
import { keepPreviousData } from "@tanstack/react-query";
import { Link, getRouteApi } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Clapperboard, LayoutGrid, List, Tv } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { imageSource, localName, localTitle } from "../features/catalog/artwork";
import type { TitlesSearch } from "../features/titles/search";
import { SyntheticBadge, type TitleKind } from "../features/titles/synthetic-badge";
import { LIBRARY_VIEW } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

type TitleSummary = MovieSummary | SeriesSummary;

const moviesRoute = getRouteApi("/app/movies");
const seriesRoute = getRouteApi("/app/series");
const helper = createDataTableColumnHelper<TitleSummary>();
const GRID_PAGE_SIZE = 48;

function TitleLink({ kind, title }: { kind: TitleKind; title: TitleSummary }) {
  const { i18n } = useTranslation();
  const name = localTitle(title, i18n.language);
  const className =
    "truncate font-medium text-foreground outline-none hover:underline focus-visible:underline";
  return kind === "movie" ? (
    <Link to="/movies/$titleId" params={{ titleId: title.id }} className={className}>
      {name}
    </Link>
  ) : (
    <Link to="/series/$titleId" params={{ titleId: title.id }} className={className}>
      {name}
    </Link>
  );
}

function useColumns(kind: TitleKind) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  return useMemo(
    () =>
      helper.columns([
        helper.accessor("title", {
          id: "title",
          header: t("titles.columns.title"),
          cell: ({ row }) => {
            const title = row.original;
            const other = i18n.language.startsWith("ar") ? title.title : title.title_ar;
            return (
              <div className="flex min-w-0 items-center gap-3">
                <PosterImage
                  src={imageSource(title.poster)}
                  blurhash={title.poster?.blurhash}
                  alt=""
                  sizes="40px"
                  className="w-10 shrink-0 rounded-sm"
                />
                <div className="grid min-w-0">
                  <TitleLink kind={kind} title={title} />
                  {other ? (
                    <span className="truncate text-xs text-muted-foreground" dir="auto">
                      {other}
                    </span>
                  ) : null}
                </div>
              </div>
            );
          },
          meta: { cellClassName: "max-w-80" },
        }),
        helper.accessor("year", {
          id: "year",
          header: t("titles.columns.year"),
          cell: ({ getValue }) => <span className="tabular-nums">{getValue() ?? ""}</span>,
        }),
        helper.accessor("status", {
          id: "status",
          header: t("titles.columns.status"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="flex flex-wrap items-center gap-1">
              <StatusBadge status={row.original.status} />
              {row.original.synthetic ? <SyntheticBadge /> : null}
            </span>
          ),
        }),
        helper.accessor("categories", {
          id: "categories",
          header: t("titles.columns.categories"),
          enableSorting: false,
          cell: ({ getValue }) => (
            <span className="line-clamp-2 text-ui text-muted-foreground">
              {getValue()
                .map((category) => localName(category, i18n.language))
                .join(t("titles.listSeparator"))}
            </span>
          ),
          meta: { cellClassName: "max-w-56" },
        }),
        helper.accessor("file_count", {
          id: "files",
          header: kind === "movie" ? t("titles.columns.files") : t("titles.columns.episodes"),
          enableSorting: false,
          cell: ({ row }) => (
            <span className="tabular-nums">
              {format.number(
                "episode_count" in row.original
                  ? row.original.episode_count
                  : row.original.file_count,
              )}
            </span>
          ),
          meta: { align: "end" },
        }),
        helper.accessor("rating", {
          id: "rating",
          header: t("titles.columns.rating"),
          cell: ({ getValue }) => {
            const value = getValue();
            return (
              <span className="tabular-nums">
                {value === null ? "" : format.number(value, { maximumFractionDigits: 1 })}
              </span>
            );
          },
          meta: { align: "end" },
        }),
        helper.accessor("updated_at", {
          id: "updated_at",
          header: t("titles.columns.updated"),
          cell: ({ getValue }) => <RelativeTime value={getValue()} />,
        }),
      ]),
    [t, i18n.language, format, kind],
  );
}

function apiParams(search: TitlesSearch, pageSize: number): MoviesListParams {
  return compact({
    search: search.q,
    status: search.status ? [search.status] : undefined,
    library: search.library,
    category: search.category,
    ordering: search.ordering,
    page: search.page,
    page_size: pageSize,
  });
}

/** Both kinds share one view; only the matching list query runs. */
function useTitles(kind: TitleKind, params: MoviesListParams) {
  const options = { placeholderData: keepPreviousData };
  const movies = useMoviesList(params, { query: { ...options, enabled: kind === "movie" } });
  const series = useSeriesList(params, { query: { ...options, enabled: kind === "series" } });
  return kind === "movie" ? movies : series;
}

function GridPager({
  page,
  pageCount,
  onPage,
}: {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (pageCount <= 1) return null;
  return (
    <nav
      aria-label={t("titles.pagination")}
      className="flex items-center justify-end gap-2 text-ui text-muted-foreground"
    >
      <span className="tabular-nums">
        {t("titles.pageOf", { page: format.number(page), pages: format.number(pageCount) })}
      </span>
      <Button
        variant="secondary"
        size="icon-sm"
        disabled={page <= 1}
        aria-label={t("titles.previousPage")}
        onClick={() => {
          onPage(page - 1);
        }}
      >
        <ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />
      </Button>
      <Button
        variant="secondary"
        size="icon-sm"
        disabled={page >= pageCount}
        aria-label={t("titles.nextPage")}
        onClick={() => {
          onPage(page + 1);
        }}
      >
        <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
      </Button>
    </nav>
  );
}

function TitlesView({
  kind,
  search,
  update,
  onOpen,
}: {
  kind: TitleKind;
  search: TitlesSearch;
  update: Update;
  onOpen: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const columns = useColumns(kind);
  const view = search.view ?? "grid";
  const pagination = paginationFromSearch(search, view === "grid" ? GRID_PAGE_SIZE : undefined);
  const query = useTitles(kind, apiParams(search, pagination.pageSize));
  const libraries = useLibrariesList({ page_size: 100 });
  const categories = useCategoriesList({
    kind: kind === "movie" ? "vod" : "series",
    page_size: 100,
  });
  const libraryName = (id: string) =>
    libraries.data?.results.find((library) => library.id === id)?.name ?? id;
  const categoryName = (id: string) => {
    const category = categories.data?.results.find((item) => item.id === id);
    return category ? localName(category, i18n.language) : id;
  };

  const filtered =
    search.q !== undefined ||
    search.status !== undefined ||
    search.library !== undefined ||
    search.category !== undefined;
  const chips: FilterChip[] = [];
  if (search.status !== undefined) {
    chips.push({
      id: "status",
      label: t("titles.filters.chipStatus", { value: t(`ui:status.${search.status}`) }),
      onRemove: () => {
        update({ status: undefined });
      },
    });
  }
  if (search.library !== undefined) {
    chips.push({
      id: "library",
      label: t("titles.filters.chipLibrary", { value: libraryName(search.library) }),
      onRemove: () => {
        update({ library: undefined });
      },
    });
  }
  if (search.category !== undefined) {
    chips.push({
      id: "category",
      label: t("titles.filters.chipCategory", { value: categoryName(search.category) }),
      onRemove: () => {
        update({ category: undefined });
      },
    });
  }

  const viewToggle = (
    <div
      role="radiogroup"
      aria-label={t("titles.view.label")}
      className="inline-flex rounded-input border border-border p-0.5"
    >
      {(
        [
          ["grid", LayoutGrid],
          ["list", List],
        ] as const
      ).map(([value, Icon]) => (
        <Button
          key={value}
          role="radio"
          aria-checked={view === value}
          aria-label={t(`titles.view.${value}`)}
          variant={view === value ? "secondary" : "ghost"}
          size="icon-xs"
          onClick={() => {
            update({ view: value === "grid" ? undefined : value, page_size: undefined });
          }}
        >
          <Icon aria-hidden="true" />
        </Button>
      ))}
    </div>
  );

  const toolbar = (
    <FilterBar
      className="w-full"
      search={
        <SearchInput
          value={search.q ?? ""}
          placeholder={t("titles.filters.search")}
          onValueChange={(value) => {
            update({ q: value || undefined });
          }}
        />
      }
      filters={
        <>
          <FacetFilter
            single
            title={t("titles.filters.status")}
            options={Object.values(TitleStatus).map((value) => ({
              value,
              label: t(`ui:status.${value}`),
            }))}
            selected={search.status ? [search.status] : []}
            onSelectedChange={(values) => {
              update({ status: values[0] as TitlesSearch["status"] });
            }}
          />
          <FacetFilter
            single
            title={t("titles.filters.library")}
            options={(libraries.data?.results ?? []).map((library) => ({
              value: library.id,
              label: library.name,
            }))}
            selected={search.library ? [search.library] : []}
            onSelectedChange={(values) => {
              update({ library: values[0] });
            }}
          />
          <FacetFilter
            single
            title={t("titles.filters.category")}
            options={(categories.data?.results ?? []).map((category) => ({
              value: category.id,
              label: localName(category, i18n.language),
            }))}
            selected={search.category ? [search.category] : []}
            onSelectedChange={(values) => {
              update({ category: values[0] });
            }}
          />
        </>
      }
      chips={chips}
      onClearAll={() => {
        update({ q: undefined, status: undefined, library: undefined, category: undefined });
      }}
      actions={viewToggle}
    />
  );

  const Icon = kind === "movie" ? Clapperboard : Tv;
  const emptyState = filtered ? (
    <EmptyState
      icon={<Icon />}
      title={t("titles.noMatches.title")}
      description={t("titles.noMatches.description")}
    />
  ) : (
    <EmptyState
      icon={<Icon />}
      title={t(`titles.empty.${kind}.title`)}
      description={t(`titles.empty.${kind}.description`)}
      action={
        <Button asChild variant="secondary">
          <Link to="/libraries">{t("titles.empty.libraries")}</Link>
        </Button>
      }
    />
  );

  if (view === "list") {
    return (
      <DataTable
        label={t(`titles.${kind}.title`)}
        columns={columns}
        data={query.data?.results}
        getRowId={(title) => title.id}
        rowCount={query.data?.count}
        pagination={pagination}
        onPaginationChange={(next) => {
          update(paginationToSearch(next), { keepPage: true });
        }}
        sorting={sortingFromOrdering(search.ordering)}
        onSortingChange={(sorting) => {
          update({ ordering: sortingToOrdering(sorting) });
        }}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        onRowClick={(title) => {
          onOpen(title.id);
        }}
        toolbar={toolbar}
        emptyState={emptyState}
      />
    );
  }

  const results = query.data?.results ?? [];
  const pageCount = query.data ? Math.max(1, Math.ceil(query.data.count / pagination.pageSize)) : 1;
  return (
    <div className="grid gap-4">
      {toolbar}
      {query.isError ? (
        <ErrorState
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : !query.isPending && results.length === 0 ? (
        emptyState
      ) : (
        <PosterGrid loading={query.isPending} aria-label={t(`titles.${kind}.title`)}>
          {results.map((title) => {
            const name = localTitle(title, i18n.language);
            return (
              <PosterCard
                key={title.id}
                asChild
                title={name}
                year={title.year}
                poster={imageSource(title.poster)}
                blurhash={title.poster?.blurhash}
                rating={title.rating}
                badges={
                  <>
                    {title.status === "ready" ? null : <StatusBadge status={title.status} />}
                    {title.synthetic ? <SyntheticBadge /> : null}
                  </>
                }
              >
                {kind === "movie" ? (
                  <Link to="/movies/$titleId" params={{ titleId: title.id }} />
                ) : (
                  <Link to="/series/$titleId" params={{ titleId: title.id }} />
                )}
              </PosterCard>
            );
          })}
        </PosterGrid>
      )}
      <GridPager
        page={pagination.pageIndex + 1}
        pageCount={pageCount}
        onPage={(page) => {
          update({ page: page > 1 ? page : undefined }, { keepPage: true });
        }}
      />
    </div>
  );
}

type Update = (patch: SearchPatch<TitlesSearch>, options?: { keepPage?: boolean }) => void;

/** Any filter change starts again from the first page. */
function nextSearch(
  previous: TitlesSearch,
  patch: SearchPatch<TitlesSearch>,
  keepPage = false,
): TitlesSearch {
  return compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) });
}

function Movies() {
  const { t } = useTranslation();
  const search = moviesRoute.useSearch();
  const navigate = moviesRoute.useNavigate();
  const update: Update = (patch, { keepPage = false } = {}) => {
    void navigate({ search: (previous) => nextSearch(previous, patch, keepPage), replace: true });
  };
  return (
    <>
      <PageHeader title={t("titles.movie.title")} description={t("titles.movie.description")} />
      <TitlesView
        kind="movie"
        search={search}
        update={update}
        onOpen={(titleId) => {
          void navigate({ to: "/movies/$titleId", params: { titleId } });
        }}
      />
    </>
  );
}

function Series() {
  const { t } = useTranslation();
  const search = seriesRoute.useSearch();
  const navigate = seriesRoute.useNavigate();
  const update: Update = (patch, { keepPage = false } = {}) => {
    void navigate({ search: (previous) => nextSearch(previous, patch, keepPage), replace: true });
  };
  return (
    <>
      <PageHeader title={t("titles.series.title")} description={t("titles.series.description")} />
      <TitlesView
        kind="series"
        search={search}
        update={update}
        onOpen={(titleId) => {
          void navigate({ to: "/series/$titleId", params: { titleId } });
        }}
      />
    </>
  );
}

export function MoviesPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.movie.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Movies />
    </RequirePermission>
  );
}

export function SeriesPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.series.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Series />
    </RequirePermission>
  );
}
