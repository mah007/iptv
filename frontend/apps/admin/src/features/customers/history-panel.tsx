import { useCustomersHistory } from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ProgressBar,
  RelativeTime,
  Skeleton,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Clapperboard, History, Tv } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { localTitle } from "../catalog/artwork";

const PAGE_SIZE = 20;

/** What the customer watched and how far (SPEC §8.3.3 Watch history tab). */
export function CustomerHistory({ customerId }: { customerId: string }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const [page, setPage] = useState(1);
  const query = useCustomersHistory(
    customerId,
    { page, page_size: PAGE_SIZE },
    { query: { placeholderData: (previous) => previous } },
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("customerHistory.title")}</CardTitle>
        <CardDescription>{t("customerHistory.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <div className="grid gap-3" role="status">
            <span className="sr-only">{t("layout.loading")}</span>
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.results.length === 0 ? (
          <EmptyState className="py-8" icon={<History />} title={t("customerHistory.empty")} />
        ) : (
          <div className="grid gap-4">
            <ul className="grid gap-3">
              {query.data.results.map((row) => {
                const name = localTitle(row.title, i18n.language);
                const Icon = row.title.kind === "movie" ? Clapperboard : Tv;
                const ratio = row.duration_ms > 0 ? row.position_ms / row.duration_ms : 0;
                return (
                  <li key={row.id} className="grid gap-1.5 rounded-input border border-border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-2">
                        <Icon
                          aria-hidden="true"
                          className="size-4 shrink-0 text-muted-foreground"
                        />
                        {row.title.kind === "movie" ? (
                          <Link
                            to="/movies/$titleId"
                            params={{ titleId: row.title.id }}
                            className="truncate font-medium text-foreground hover:underline"
                          >
                            <bdi>{name}</bdi>
                          </Link>
                        ) : (
                          <Link
                            to="/series/$titleId"
                            params={{ titleId: row.title.id }}
                            className="truncate font-medium text-foreground hover:underline"
                          >
                            <bdi>{name}</bdi>
                          </Link>
                        )}
                        {row.title.season !== null && row.title.episode !== null ? (
                          <span className="shrink-0 text-xs text-muted-foreground" dir="ltr">
                            {t("customerHistory.episode", {
                              season: row.title.season,
                              episode: row.title.episode,
                            })}
                          </span>
                        ) : null}
                      </span>
                      <span className="flex items-center gap-2 text-xs text-muted-foreground">
                        {row.completed ? (
                          <Badge tone="success">{t("customerHistory.finished")}</Badge>
                        ) : null}
                        <RelativeTime value={row.updated_at} />
                      </span>
                    </div>
                    {row.duration_ms > 0 ? (
                      <ProgressBar
                        value={Math.min(1, ratio) * 100}
                        variant="inline"
                        label={t("customerHistory.position", {
                          position: format.duration(row.position_ms / 1000),
                          duration: format.duration(row.duration_ms / 1000),
                        })}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {query.data.count > PAGE_SIZE ? (
              <nav
                aria-label={t("customerHistory.pagination")}
                className="flex items-center justify-between gap-3 border-t border-border pt-3"
              >
                <span className="text-xs text-muted-foreground tabular-nums">
                  {t("titles.pageOf", { page, pages: Math.ceil(query.data.count / PAGE_SIZE) })}
                </span>
                <span className="flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => {
                      setPage((current) => current - 1);
                    }}
                  >
                    <ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />
                    {t("titles.previousPage")}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page * PAGE_SIZE >= query.data.count}
                    onClick={() => {
                      setPage((current) => current + 1);
                    }}
                  >
                    {t("titles.nextPage")}
                    <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
                  </Button>
                </span>
              </nav>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
