import { useSettingsList, type SettingEntry } from "@smart-iptv/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageHeader,
  Skeleton,
} from "@smart-iptv/ui";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { SettingRow } from "../features/settings/setting-row";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

/** Registry groups in the order of SPEC §8.3.18; groups added later follow. */
const GROUP_ORDER = [
  "branding",
  "xtream",
  "playback",
  "library",
  "metadata",
  "security",
  "features",
  "billing",
  "trials",
];

function groupSettings(entries: readonly SettingEntry[]) {
  const groups = new Map<string, SettingEntry[]>();
  for (const entry of entries) {
    const list = groups.get(entry.group) ?? [];
    list.push(entry);
    groups.set(entry.group, list);
  }
  const rank = (group: string) => {
    const index = GROUP_ORDER.indexOf(group);
    return index === -1 ? GROUP_ORDER.length : index;
  };
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

function SettingsSkeleton() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-live="polite" className="grid gap-4">
      <span className="sr-only">{t("layout.loading")}</span>
      {[0, 1].map((card) => (
        <div key={card} className="grid gap-4 rounded-card border border-border bg-card p-5">
          <Skeleton className="h-5 w-40" />
          {[0, 1, 2].map((row) => (
            <div key={row} className="grid gap-3 md:grid-cols-[2fr_3fr]">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-8 w-full max-w-64" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function Settings() {
  const { t } = useTranslation();
  const can = useCan();
  const canEdit = can("settings.edit");
  const query = useSettingsList();
  const groups = useMemo(() => groupSettings(query.data ?? []), [query.data]);

  return (
    <div className="grid gap-6 lg:grid-cols-[12rem_minmax(0,1fr)]">
      <PageHeader
        className="pb-0 lg:col-span-2"
        title={t("settings.title")}
        description={canEdit ? t("settings.description") : t("settings.readOnly")}
      />
      {query.isPending ? (
        <SettingsSkeleton />
      ) : query.isError ? (
        <Card className="lg:col-span-2">
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        </Card>
      ) : (
        <>
          <nav aria-label={t("settings.sections")} className="hidden lg:block">
            <ul className="sticky top-20 grid gap-0.5">
              {groups.map(([group]) => (
                <li key={group}>
                  <a
                    href={`#settings-${group}`}
                    className="block rounded-input px-2.5 py-1.5 text-ui text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {t(`settings.groups.${group}.title`, { defaultValue: group })}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="grid min-w-0 gap-4">
            {groups.map(([group, entries]) => (
              <Card key={group} id={`settings-${group}`} className="scroll-mt-20">
                <CardHeader>
                  <CardTitle>
                    {t(`settings.groups.${group}.title`, { defaultValue: group })}
                  </CardTitle>
                  <CardDescription>
                    {t(`settings.groups.${group}.description`, { defaultValue: "" })}
                  </CardDescription>
                </CardHeader>
                <CardContent className="pb-1">
                  {entries.map((entry) => (
                    <SettingRow key={entry.key} entry={entry} canEdit={canEdit} />
                  ))}
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  usePageTitle(t("settings.title"));
  return (
    <RequirePermission permission="settings.view">
      <Settings />
    </RequirePermission>
  );
}
