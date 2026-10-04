import { isApiError, usePeopleRetrieve } from "@smart-iptv/api-portal";
import { Avatar, EmptyState, ErrorState, PosterGrid, Skeleton } from "@smart-iptv/ui";
import { getRouteApi } from "@tanstack/react-router";
import { UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import { TitleCard } from "../components/title-card";
import { artworkUrl } from "../lib/artwork";
import { usePageTitle } from "../lib/page-title";

const personApi = getRouteApi("/app/shell/people/$personId");

/** A cast or crew member and the titles you can watch them in. */
export function PersonPage() {
  const { t } = useTranslation();
  const { personId } = personApi.useParams();
  const person = usePeopleRetrieve(personId);
  usePageTitle(person.data?.name ?? t("person.title"));

  if (person.isError) {
    return (
      <div className="page-top px-4">
        {isApiError(person.error) && person.error.code === "NOT_FOUND" ? (
          <EmptyState
            icon={<UserRound />}
            title={t("person.notFoundTitle")}
            description={t("person.notFound")}
          />
        ) : (
          <ErrorState
            error={person.error}
            onRetry={() => {
              void person.refetch();
            }}
          />
        )}
      </div>
    );
  }

  const data = person.data;
  // One card per title, with every role the person had in it.
  const titles = new Map<
    string,
    { card: NonNullable<typeof data>["known_for"][number]["title"]; roles: string[] }
  >();
  for (const credit of data?.known_for ?? []) {
    const key = `${credit.title.type}:${credit.title.id}`;
    const entry = titles.get(key) ?? { card: credit.title, roles: [] };
    entry.roles.push(credit.character || t(`person.roles.${credit.role}`));
    titles.set(key, entry);
  }

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-8 px-4 sm:px-6 lg:px-10">
      <div className="flex items-center gap-4">
        {data ? (
          <Avatar
            name={data.name}
            src={artworkUrl(data.profile, "w185") ?? undefined}
            className="size-20 text-xl sm:size-24"
          />
        ) : (
          <Skeleton className="size-20 rounded-full sm:size-24" />
        )}
        <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">
          {data?.name ?? <Skeleton className="h-8 w-48" />}
        </h1>
      </div>
      <section aria-labelledby="known-for" className="grid gap-4">
        <h2 id="known-for" className="text-lg font-semibold text-foreground">
          {t("person.knownFor")}
        </h2>
        {data && titles.size === 0 ? (
          <p className="text-sm text-muted-foreground">{t("person.none")}</p>
        ) : (
          <PosterGrid loading={person.isPending}>
            {[...titles.entries()].map(([key, entry]) => (
              <div key={key} className="grid gap-1">
                <TitleCard title={entry.card} />
                <span className="line-clamp-2 px-0.5 text-xs text-muted-foreground">
                  {entry.roles.join(t("listSeparator"))}
                </span>
              </div>
            ))}
          </PosterGrid>
        )}
      </section>
    </div>
  );
}
