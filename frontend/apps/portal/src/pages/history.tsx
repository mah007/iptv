import {
  getContinueWatchingListQueryKey,
  getHomeRetrieveQueryKey,
  useWatchHistoryDelete,
  watchHistoryList,
  type PaginatedWatchItemList,
  type WatchItem,
} from "@smart-iptv/api-portal";
import { Button, EmptyState, ErrorState, Skeleton, toast } from "@smart-iptv/ui";
import { useInfiniteQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { History, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { InfiniteSentinel } from "../components/infinite-sentinel";
import { WatchCard } from "../components/watch-card";
import { cursorOf } from "../lib/cursor";
import { usePageTitle } from "../lib/page-title";
import { useCustomerFormatters } from "../lib/formatters";

const HISTORY_KEY = ["/api/v1/watch-history", "pages"] as const;

function RemoveButton({ item }: { item: WatchItem }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const remove = useWatchHistoryDelete();
  if (item.id === null) return null;
  const id = item.id;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={t("history.remove", { title: item.title.title })}
      pending={remove.isPending}
      onClick={() => {
        remove.mutate(
          { id },
          {
            onSuccess: () => {
              queryClient.setQueryData<InfiniteData<PaginatedWatchItemList>>(HISTORY_KEY, (data) =>
                data === undefined
                  ? data
                  : {
                      ...data,
                      pages: data.pages.map((page) => ({
                        ...page,
                        results: page.results.filter((entry) => entry.id !== id),
                      })),
                    },
              );
              void queryClient.invalidateQueries({ queryKey: getContinueWatchingListQueryKey() });
              void queryClient.invalidateQueries({ queryKey: getHomeRetrieveQueryKey() });
              toast.success(t("history.removed"));
            },
            onError: () => {
              toast.error(t("history.removeFailed"));
            },
          },
        );
      }}
    >
      {remove.isPending ? null : <Trash2 aria-hidden="true" />}
    </Button>
  );
}

/** Watch history (SPEC §9): everything started, newest first; removing one also drops it from continue watching. */
export function HistoryPage() {
  const { t } = useTranslation();
  const format = useCustomerFormatters();
  usePageTitle(t("history.title"));
  const pages = useInfiniteQuery({
    queryKey: HISTORY_KEY,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      watchHistoryList(
        pageParam === undefined ? { page_size: 30 } : { page_size: 30, cursor: pageParam },
        {
          signal,
        },
      ),
    getNextPageParam: (last) => cursorOf(last.next),
    refetchOnMount: "always",
  });
  const items = pages.data?.pages.flatMap((page) => page.results) ?? [];

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{t("history.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("history.subtitle")}</p>
      </div>
      {pages.isError ? (
        <ErrorState
          error={pages.error}
          onRetry={() => {
            void pages.refetch();
          }}
        />
      ) : pages.isPending ? (
        <div
          className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4"
          aria-busy="true"
        >
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="aspect-video w-full rounded-card" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<History />}
          title={t("history.emptyTitle")}
          description={t("history.empty")}
          action={
            <Button asChild>
              <Link to="/">{t("notFound.home")}</Link>
            </Button>
          }
        />
      ) : (
        <>
          <ul
            role="list"
            className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-x-4 gap-y-6"
          >
            {items.map((item) => (
              <li key={item.id ?? `${item.type}:${item.title.id}`} className="grid gap-1">
                <WatchCard item={item} actions={<RemoveButton item={item} />} />
                <span className="px-0.5 text-xs text-muted-foreground">
                  {t("history.watchedOn", { date: format.dateTime(item.updated_at) })}
                </span>
              </li>
            ))}
          </ul>
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
