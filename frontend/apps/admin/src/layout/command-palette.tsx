import {
  getLibrariesListQueryKey,
  useCustomersList,
  useLibrariesList,
  useLibrariesScan,
  useMoviesList,
  useSeriesList,
} from "@smart-iptv/api";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandLoading,
  CommandShortcut,
  Kbd,
  setTheme,
  toast,
  useTheme,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  Clapperboard,
  Keyboard,
  Languages,
  Moon,
  Plus,
  ScanSearch,
  ShieldBan,
  Sun,
  Tv,
  UserRound,
} from "lucide-react";
import { useDeferredValue, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { localTitle } from "../features/catalog/artwork";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { notifyError } from "../lib/problems";
import { keyCaps } from "../lib/shortcuts";
import { NAV_SECTIONS, NAV_SHORTCUTS } from "./nav";

/** Server searches start from this many characters. */
const MIN_SEARCH = 2;

function Keys({ keys }: { keys: string }) {
  return (
    <CommandShortcut>
      {keyCaps(keys)
        .flat()
        .map((cap, index) => (
          <Kbd key={index}>{cap}</Kbd>
        ))}
    </CommandShortcut>
  );
}

function matches(query: string, ...texts: string[]): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (needle === "") return true;
  return texts.some((text) => text.toLocaleLowerCase().includes(needle));
}

interface PaletteAction {
  id: string;
  label: string;
  icon: ReactNode;
  keys?: string;
  run: () => void;
}

/**
 * The command palette (SPEC §8.2, ⌘K / Ctrl+K): jump to any page, customer or
 * title, or run an action. Pages and actions filter as you type; customers and
 * titles are searched on the server from two characters.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onShowShortcuts,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShowShortcuts: () => void;
}) {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [theme] = useTheme();
  const [query, setQuery] = useState("");
  const search = useDeferredValue(query.trim());
  const searching = open && search.length >= MIN_SEARCH;

  const customers = useCustomersList(
    { search, page_size: 5 },
    { query: { enabled: searching && can("customers.view") } },
  );
  const movies = useMoviesList(
    { search, page_size: 4 },
    { query: { enabled: searching && can(LIBRARY_VIEW) } },
  );
  const series = useSeriesList(
    { search, page_size: 4 },
    { query: { enabled: searching && can(LIBRARY_VIEW) } },
  );
  const libraries = useLibrariesList(
    { page_size: 50 },
    { query: { enabled: open && can("library.manage") } },
  );
  const scan = useLibrariesScan();

  function close(): void {
    onOpenChange(false);
    setQuery("");
  }

  function go(run: () => Promise<void> | void): () => void {
    return () => {
      close();
      void run();
    };
  }

  const pages = NAV_SECTIONS.flatMap((section) =>
    section.items
      .filter((item) => can(item.permission))
      .map((item) => ({ item, label: t(item.labelKey), section: t(section.titleKey) })),
  ).filter((page) => matches(query, page.label, page.section));

  const actions: PaletteAction[] = [
    ...(can("customers.edit")
      ? [
          {
            id: "new-customer",
            label: t("palette.actions.newCustomer"),
            icon: <Plus />,
            keys: "n c",
            run: go(() => navigate({ to: "/customers", search: { new: true } })),
          },
          {
            id: "add-rule",
            label: t("palette.actions.addRule"),
            icon: <ShieldBan />,
            run: go(() => navigate({ to: "/access-rules" })),
          },
        ]
      : []),
    ...(can("library.manage")
      ? (libraries.data?.results ?? []).map((library) => ({
          id: `scan-${library.id}`,
          label: t("palette.actions.scan", { name: library.name }),
          icon: <ScanSearch />,
          run: go(async () => {
            try {
              await scan.mutateAsync({ id: library.id });
              toast.success(t("libraries.scan.started", { name: library.name }));
              await queryClient.invalidateQueries({ queryKey: getLibrariesListQueryKey() });
            } catch (error) {
              notifyError(t, error);
            }
          }),
        }))
      : []),
    {
      id: "theme",
      label: theme === "dark" ? t("palette.actions.lightTheme") : t("palette.actions.darkTheme"),
      icon: theme === "dark" ? <Sun /> : <Moon />,
      run: go(() => {
        setTheme(theme === "dark" ? "light" : "dark");
      }),
    },
    {
      id: "language",
      label: t("palette.actions.language"),
      icon: <Languages />,
      run: go(() =>
        i18n.changeLanguage(i18n.language === "ar" ? "en" : "ar").then(() => undefined),
      ),
    },
    {
      id: "shortcuts",
      label: t("palette.actions.shortcuts"),
      icon: <Keyboard />,
      keys: "?",
      run: go(onShowShortcuts),
    },
  ].filter((action) => matches(query, action.label));

  const titles = [
    ...(movies.data?.results ?? []).map((title) => ({ ...title, kind: "movie" as const })),
    ...(series.data?.results ?? []).map((title) => ({ ...title, kind: "series" as const })),
  ];
  const loading = searching && (customers.isFetching || movies.isFetching || series.isFetching);

  return (
    <CommandDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
      title={t("palette.title")}
      description={t("palette.description")}
      label={t("palette.title")}
      shouldFilter={false}
    >
      <CommandInput value={query} onValueChange={setQuery} placeholder={t("palette.placeholder")} />
      <CommandList>
        {loading ? <CommandLoading>{t("palette.searching")}</CommandLoading> : null}
        <CommandEmpty>{t("palette.empty")}</CommandEmpty>
        {searching && (customers.data?.results.length ?? 0) > 0 ? (
          <CommandGroup heading={t("palette.groups.customers")}>
            {customers.data?.results.map((customer) => (
              <CommandItem
                key={customer.id}
                value={`customer-${customer.id}`}
                onSelect={go(() =>
                  navigate({ to: "/customers/$customerId", params: { customerId: customer.id } }),
                )}
              >
                <UserRound aria-hidden="true" />
                <bdi className="truncate">{customer.name || customer.username}</bdi>
                <span className="ms-auto truncate text-xs text-muted-foreground" dir="ltr">
                  {customer.phone || customer.email || customer.username}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {searching && titles.length > 0 ? (
          <CommandGroup heading={t("palette.groups.titles")}>
            {titles.map((title) => (
              <CommandItem
                key={`${title.kind}-${title.id}`}
                value={`${title.kind}-${title.id}`}
                onSelect={go(() =>
                  title.kind === "movie"
                    ? navigate({ to: "/movies/$titleId", params: { titleId: title.id } })
                    : navigate({ to: "/series/$titleId", params: { titleId: title.id } }),
                )}
              >
                {title.kind === "movie" ? (
                  <Clapperboard aria-hidden="true" />
                ) : (
                  <Tv aria-hidden="true" />
                )}
                <bdi className="truncate">{localTitle(title, i18n.language)}</bdi>
                {title.year ? (
                  <span className="ms-auto text-xs text-muted-foreground tabular-nums">
                    {title.year}
                  </span>
                ) : null}
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {pages.length > 0 ? (
          <CommandGroup heading={t("palette.groups.pages")}>
            {pages.map(({ item, label }) => {
              const keys = NAV_SHORTCUTS[item.to];
              return (
                <CommandItem
                  key={item.to}
                  value={`page-${item.to}`}
                  onSelect={go(() => navigate({ to: item.to }))}
                >
                  <item.icon aria-hidden="true" />
                  {label}
                  {keys ? <Keys keys={keys} /> : null}
                </CommandItem>
              );
            })}
          </CommandGroup>
        ) : null}
        {actions.length > 0 ? (
          <CommandGroup heading={t("palette.groups.actions")}>
            {actions.map((action) => (
              <CommandItem key={action.id} value={action.id} onSelect={action.run}>
                {action.icon}
                {action.label}
                {action.keys ? <Keys keys={action.keys} /> : null}
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
      </CommandList>
    </CommandDialog>
  );
}
