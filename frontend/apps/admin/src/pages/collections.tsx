import {
  getCollectionsListQueryKey,
  useCollectionsCreate,
  useCollectionsDelete,
  useCollectionsList,
  useCollectionsUpdate,
  useMoviesList,
  useSeriesList,
  type Collection,
  type CollectionItem,
  type CollectionWriteRequest,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandLoading,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  PageHeader,
  PosterImage,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Skeleton,
  StatusBadge,
  Switch,
  Textarea,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Clapperboard,
  Library,
  Pencil,
  Plus,
  Trash2,
  Tv,
  X,
} from "lucide-react";
import { useDeferredValue, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { imageSource, localName, localTitle } from "../features/catalog/artwork";
import { LIBRARY_VIEW, useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError, translatedFieldErrors } from "../lib/problems";

type Item = Pick<CollectionItem, "type" | "id" | "title" | "title_ar" | "year" | "poster">;

interface Draft {
  slug: string;
  name_en: string;
  name_ar: string;
  description_en: string;
  description_ar: string;
  published: boolean;
  show_on_home: boolean;
  items: Item[];
}

const EMPTY: Draft = {
  slug: "",
  name_en: "",
  name_ar: "",
  description_en: "",
  description_ar: "",
  published: true,
  show_on_home: true,
  items: [],
};

function draftOf(collection: Collection): Draft {
  return {
    slug: collection.slug,
    name_en: collection.name_en,
    name_ar: collection.name_ar,
    description_en: collection.description_en,
    description_ar: collection.description_ar,
    published: collection.published,
    show_on_home: collection.show_on_home,
    items: [...collection.items],
  };
}

/** Find movies and series to add (the API searches both titles). */
function TitleSearch({
  onAdd,
  taken,
}: {
  onAdd: (item: Item) => void;
  taken: ReadonlySet<string>;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const search = useDeferredValue(query.trim());
  const enabled = search.length >= 2;
  const movies = useMoviesList({ search, page_size: 6 }, { query: { enabled } });
  const series = useSeriesList({ search, page_size: 6 }, { query: { enabled } });
  const results: Item[] = [
    ...(movies.data?.results ?? []).map((title) => ({ ...title, type: "movie" as const })),
    ...(series.data?.results ?? []).map((title) => ({ ...title, type: "series" as const })),
  ].filter((item) => !taken.has(`${item.type}:${item.id}`));
  return (
    <Command
      shouldFilter={false}
      label={t("collections.form.add")}
      className="border border-border"
    >
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder={t("collections.form.search")}
      />
      <CommandList className="max-h-56">
        {enabled && (movies.isFetching || series.isFetching) ? (
          <CommandLoading>{t("palette.searching")}</CommandLoading>
        ) : null}
        {enabled ? <CommandEmpty>{t("palette.empty")}</CommandEmpty> : null}
        <CommandGroup>
          {results.map((item) => (
            <CommandItem
              key={`${item.type}-${item.id}`}
              value={`${item.type}-${item.id}`}
              onSelect={() => {
                onAdd(item);
              }}
            >
              {item.type === "movie" ? (
                <Clapperboard aria-hidden="true" />
              ) : (
                <Tv aria-hidden="true" />
              )}
              <bdi className="truncate">{localTitle(item, i18n.language)}</bdi>
              {item.year ? (
                <span className="ms-auto text-xs text-muted-foreground tabular-nums">
                  {item.year}
                </span>
              ) : null}
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}

function CollectionSheet({
  collection,
  open,
  onOpenChange,
}: {
  collection: Collection | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const ids = { slug: useId(), nameEn: useId(), nameAr: useId(), descEn: useId(), descAr: useId() };
  const [draft, setDraft] = useState<Draft>(() => (collection ? draftOf(collection) : EMPTY));
  const [error, setError] = useState<string | null>(null);
  const create = useCollectionsCreate();
  const update = useCollectionsUpdate();
  const taken = new Set(draft.items.map((item) => `${item.type}:${item.id}`));
  const valid = draft.name_en.trim() !== "" && draft.name_ar.trim() !== "";

  function patch(values: Partial<Draft>): void {
    setDraft((current) => ({ ...current, ...values }));
  }

  function move(index: number, step: -1 | 1): void {
    const items = [...draft.items];
    const [moved] = items.splice(index, 1);
    if (moved === undefined) return;
    items.splice(index + step, 0, moved);
    patch({ items });
  }

  async function save(): Promise<void> {
    setError(null);
    const data: CollectionWriteRequest = {
      ...(draft.slug.trim() ? { slug: draft.slug.trim() } : {}),
      name_en: draft.name_en.trim(),
      name_ar: draft.name_ar.trim(),
      description_en: draft.description_en.trim(),
      description_ar: draft.description_ar.trim(),
      published: draft.published,
      show_on_home: draft.show_on_home,
      items: draft.items.map((item) => ({ type: item.type, id: item.id })),
    };
    try {
      if (collection) await update.mutateAsync({ id: collection.id, data });
      else await create.mutateAsync({ data });
      await queryClient.invalidateQueries({ queryKey: getCollectionsListQueryKey() });
      toast.success(collection ? t("collections.saved") : t("collections.created"));
      onOpenChange(false);
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-2xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>
            {collection ? t("collections.form.editTitle") : t("collections.form.newTitle")}
          </SheetTitle>
          <SheetDescription>{t("collections.form.description")}</SheetDescription>
        </SheetHeader>
        <form
          noValidate
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) void save();
          }}
        >
          <SheetBody className="flex flex-col gap-4 [&>*]:shrink-0">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={ids.nameEn}>{t("collections.fields.nameEn")}</Label>
                <Input
                  id={ids.nameEn}
                  dir="ltr"
                  maxLength={150}
                  value={draft.name_en}
                  onChange={(event) => {
                    patch({ name_en: event.target.value });
                  }}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={ids.nameAr}>{t("collections.fields.nameAr")}</Label>
                <Input
                  id={ids.nameAr}
                  dir="rtl"
                  maxLength={150}
                  value={draft.name_ar}
                  onChange={(event) => {
                    patch({ name_ar: event.target.value });
                  }}
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={ids.slug}>{t("collections.fields.slug")}</Label>
              <Input
                id={ids.slug}
                dir="ltr"
                className="font-mono"
                maxLength={100}
                value={draft.slug}
                onChange={(event) => {
                  patch({ slug: event.target.value });
                }}
              />
              <p className="text-xs text-muted-foreground">{t("collections.fields.slugHelp")}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={ids.descEn}>{t("collections.fields.descriptionEn")}</Label>
                <Textarea
                  id={ids.descEn}
                  dir="ltr"
                  rows={2}
                  value={draft.description_en}
                  onChange={(event) => {
                    patch({ description_en: event.target.value });
                  }}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={ids.descAr}>{t("collections.fields.descriptionAr")}</Label>
                <Textarea
                  id={ids.descAr}
                  dir="rtl"
                  rows={2}
                  value={draft.description_ar}
                  onChange={(event) => {
                    patch({ description_ar: event.target.value });
                  }}
                />
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Label className="flex items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5 font-normal">
                <span className="font-medium text-foreground">
                  {t("collections.fields.published")}
                </span>
                <Switch
                  checked={draft.published}
                  onCheckedChange={(published) => {
                    patch({ published });
                  }}
                />
              </Label>
              <Label className="flex items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5 font-normal">
                <span className="font-medium text-foreground">{t("collections.fields.home")}</span>
                <Switch
                  checked={draft.show_on_home}
                  onCheckedChange={(show_on_home) => {
                    patch({ show_on_home });
                  }}
                />
              </Label>
            </div>
            <section className="grid gap-2" aria-labelledby="collection-items">
              <h3 id="collection-items" className="text-sm font-semibold text-foreground">
                {t("collections.form.items", { count: draft.items.length })}
              </h3>
              <TitleSearch
                taken={taken}
                onAdd={(item) => {
                  patch({ items: [...draft.items, item] });
                }}
              />
              {draft.items.length === 0 ? (
                <p className="text-ui text-muted-foreground">{t("collections.form.noItems")}</p>
              ) : (
                <ol className="grid gap-1.5">
                  {draft.items.map((item, index) => {
                    const name = localTitle(item, i18n.language);
                    return (
                      <li
                        key={`${item.type}-${item.id}`}
                        className="flex items-center gap-2 rounded-input border border-border px-2 py-1.5"
                      >
                        <span className="w-5 text-xs text-muted-foreground tabular-nums">
                          {index + 1}
                        </span>
                        <PosterImage
                          src={imageSource(item.poster)}
                          blurhash={item.poster?.blurhash}
                          alt=""
                          className="w-7 shrink-0 rounded-[3px]"
                        />
                        <bdi className="min-w-0 flex-1 truncate text-ui text-foreground">
                          {name}
                        </bdi>
                        <Badge>{t(`dashboard.kinds.${item.type}`)}</Badge>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          disabled={index === 0}
                          aria-label={t("collections.form.moveUp", { name })}
                          onClick={() => {
                            move(index, -1);
                          }}
                        >
                          <ArrowUp aria-hidden="true" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          disabled={index === draft.items.length - 1}
                          aria-label={t("collections.form.moveDown", { name })}
                          onClick={() => {
                            move(index, 1);
                          }}
                        >
                          <ArrowDown aria-hidden="true" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("collections.form.remove", { name })}
                          onClick={() => {
                            patch({
                              items: draft.items.filter((_, position) => position !== index),
                            });
                          }}
                        >
                          <X aria-hidden="true" />
                        </Button>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
            {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
          </SheetBody>
          <SheetFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={!valid} pending={create.isPending || update.isPending}>
              {collection ? t("common.save") : t("collections.form.create")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function Collections() {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const queryClient = useQueryClient();
  const query = useCollectionsList({ page_size: 100 });
  const remove = useCollectionsDelete();
  const [editing, setEditing] = useState<Collection | null>(null);
  const [sheet, setSheet] = useState(false);
  const [deleting, setDeleting] = useState<Collection | null>(null);
  const editable = can("library.manage");
  const newButton = editable ? (
    <Button
      onClick={() => {
        setEditing(null);
        setSheet(true);
      }}
    >
      <Plus aria-hidden="true" />
      {t("collections.new")}
    </Button>
  ) : null;
  const collections = [...(query.data?.results ?? [])].sort((a, b) => a.sort - b.sort);
  return (
    <>
      <PageHeader
        title={t("collections.title")}
        description={t("collections.description")}
        actions={newButton}
      />
      {query.isPending ? (
        <div className="grid gap-3" role="status">
          <span className="sr-only">{t("layout.loading")}</span>
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : query.isError ? (
        <QueryError
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : collections.length === 0 ? (
        <EmptyState
          icon={<Library />}
          title={t("collections.empty.title")}
          description={t("collections.empty.description")}
          action={newButton}
        />
      ) : (
        <ul className="grid gap-3" aria-label={t("collections.title")}>
          {collections.map((collection) => (
            <li key={collection.id}>
              <Card>
                <CardContent className="grid gap-3 pt-(--density-card)">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="grid min-w-0 gap-0.5">
                      <h2 className="truncate text-base font-semibold text-foreground">
                        <bdi>{localName(collection, i18n.language)}</bdi>
                      </h2>
                      <code className="font-mono text-xs text-muted-foreground" dir="ltr">
                        {collection.slug}
                      </code>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {collection.published ? (
                        <StatusBadge status="active" label={t("collections.published")} />
                      ) : (
                        <Badge>{t("collections.draft")}</Badge>
                      )}
                      {collection.show_on_home ? (
                        <Badge tone="primary">{t("collections.onHome")}</Badge>
                      ) : null}
                      <Badge>
                        {t("collections.count", {
                          count: collection.items.length,
                          formatted: format.number(collection.items.length),
                        })}
                      </Badge>
                      {editable ? (
                        <>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={t("collections.edit", {
                              name: localName(collection, i18n.language),
                            })}
                            onClick={() => {
                              setEditing(collection);
                              setSheet(true);
                            }}
                          >
                            <Pencil aria-hidden="true" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={t("collections.delete.action", {
                              name: localName(collection, i18n.language),
                            })}
                            onClick={() => {
                              setDeleting(collection);
                            }}
                          >
                            <Trash2 aria-hidden="true" />
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                  {collection.items.length > 0 ? (
                    <ul
                      className="flex gap-2 overflow-x-auto pb-1"
                      aria-label={t("collections.itemsLabel")}
                    >
                      {collection.items.slice(0, 12).map((item) => (
                        <li key={`${item.type}-${item.id}`} className="w-16 shrink-0">
                          <PosterImage
                            src={imageSource(item.poster)}
                            blurhash={item.poster?.blurhash}
                            alt={localTitle(item, i18n.language)}
                            className="w-16 rounded-[4px]"
                          />
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
      {sheet ? (
        <CollectionSheet
          key={editing?.id ?? "new"}
          collection={editing}
          open={sheet}
          onOpenChange={setSheet}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        tone="danger"
        title={t("collections.delete.title", {
          name: deleting ? localName(deleting, i18n.language) : "",
        })}
        description={t("collections.delete.description")}
        confirmLabel={t("collections.delete.confirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
            await queryClient.invalidateQueries({ queryKey: getCollectionsListQueryKey() });
            toast.success(t("collections.deleted"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </>
  );
}

export function CollectionsPage() {
  const { t } = useTranslation();
  usePageTitle(t("collections.title"));
  return (
    <RequirePermission permission={LIBRARY_VIEW}>
      <Collections />
    </RequirePermission>
  );
}
