import { useFavoritesList } from "@smart-iptv/api-portal";
import { Button, EmptyState, ErrorState, PosterGrid } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { Heart } from "lucide-react";
import { useTranslation } from "react-i18next";

import { TitleCard } from "../components/title-card";
import { usePageTitle } from "../lib/page-title";

/** My List: the favourites, newest first. Titles are added and removed on their pages. */
export function MyListPage() {
  const { t } = useTranslation();
  usePageTitle(t("myList.title"));
  const favorites = useFavoritesList({ query: { refetchOnMount: "always" } });

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{t("myList.title")}</h1>
      {favorites.isError ? (
        <ErrorState
          error={favorites.error}
          onRetry={() => {
            void favorites.refetch();
          }}
        />
      ) : favorites.data?.length === 0 ? (
        <EmptyState
          icon={<Heart />}
          title={t("myList.emptyTitle")}
          description={t("myList.empty")}
          action={
            <Button asChild>
              <Link to="/movies">{t("myList.browse")}</Link>
            </Button>
          }
        />
      ) : (
        <PosterGrid loading={favorites.isPending} aria-label={t("myList.title")}>
          {(favorites.data ?? []).map((item) => (
            <TitleCard key={`${item.type}:${item.id}`} title={item} />
          ))}
        </PosterGrid>
      )}
    </div>
  );
}
