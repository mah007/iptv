import { isApiError, useCollectionsRetrieve } from "@smart-iptv/api-portal";
import { EmptyState, ErrorState, PosterGrid } from "@smart-iptv/ui";
import { getRouteApi } from "@tanstack/react-router";
import { Library } from "lucide-react";
import { useTranslation } from "react-i18next";

import { TitleCard } from "../components/title-card";
import { usePageTitle } from "../lib/page-title";

const collectionApi = getRouteApi("/app/shell/collections/$slug");

/** A curated collection (ADR-0013 §7): its name, description and titles, in the admin's order. */
export function CollectionPage() {
  const { t } = useTranslation();
  const { slug } = collectionApi.useParams();
  const collection = useCollectionsRetrieve(slug);
  usePageTitle(collection.data?.name ?? t("collection.title"));

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      {collection.isError ? (
        isApiError(collection.error) && collection.error.code === "NOT_FOUND" ? (
          <EmptyState
            icon={<Library />}
            title={t("collection.notFoundTitle")}
            description={t("collection.notFound")}
          />
        ) : (
          <ErrorState
            error={collection.error}
            onRetry={() => {
              void collection.refetch();
            }}
          />
        )
      ) : (
        <>
          <div className="grid gap-2">
            <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">
              {collection.data?.name ?? t("collection.title")}
            </h1>
            {collection.data?.description ? (
              <p className="max-w-3xl text-sm text-muted-foreground">
                {collection.data.description}
              </p>
            ) : null}
          </div>
          {collection.data?.items.length === 0 ? (
            <EmptyState
              icon={<Library />}
              title={t("collection.emptyTitle")}
              description={t("collection.empty")}
            />
          ) : (
            <PosterGrid loading={collection.isPending} aria-label={collection.data?.name}>
              {(collection.data?.items ?? []).map((item) => (
                <TitleCard key={`${item.type}:${item.id}`} title={item} />
              ))}
            </PosterGrid>
          )}
        </>
      )}
    </div>
  );
}
