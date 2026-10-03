import { zodResolver } from "@hookform/resolvers/zod";
import {
  CategoryKind,
  getCategoriesListQueryKey,
  useCategoriesCreate,
  useCategoriesDelete,
  useCategoriesList,
  useCategoriesReorder,
  useCategoriesUpdate,
  type Category,
  type CategoryWriteRequest,
  type PaginatedCategoryList,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  PageHeader,
  Skeleton,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsList,
  TabsTrigger,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, FolderTree, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { QueryError, RequirePermission } from "../components/states";
import { localName } from "../features/catalog/artwork";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { applyFieldErrors, notifyError } from "../lib/problems";

const route = getRouteApi("/app/categories");
const KINDS = Object.values(CategoryKind);
/** The API's page limit; a kind with more categories than this is not a POC concern. */
const PAGE_SIZE = 100;
/** Who may read categories (the API's rule: access-profile editors too). */
const CATEGORY_VIEW = ["customers.view", "library.view", "library.manage", "library.review"];

const categorySchema = z.object({
  name_en: z
    .string()
    .trim()
    .min(1, "categories.validation.nameRequired")
    .max(100, "categories.validation.nameTooLong"),
  name_ar: z
    .string()
    .trim()
    .min(1, "categories.validation.nameRequired")
    .max(100, "categories.validation.nameTooLong"),
  slug: z
    .string()
    .trim()
    .max(100, "categories.validation.nameTooLong")
    .refine(
      (value) => value === "" || /^[-a-zA-Z0-9_]+$/u.test(value),
      "categories.validation.slug",
    ),
  visible_in_xtream: z.boolean(),
  is_adult: z.boolean(),
});

type CategoryFormInput = z.input<typeof categorySchema>;
type CategoryFormValues = z.output<typeof categorySchema>;

const FIELD_PATHS = {
  name_en: "name_en",
  name_ar: "name_ar",
  slug: "slug",
  visible_in_xtream: "visible_in_xtream",
  is_adult: "is_adult",
} as const;

function CategoryForm({
  kind,
  category,
  onDone,
}: {
  kind: CategoryKind;
  category: Category | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useCategoriesCreate();
  const update = useCategoriesUpdate();
  const form = useForm<CategoryFormInput, unknown, CategoryFormValues>({
    resolver: zodResolver(categorySchema),
    defaultValues: category
      ? {
          name_en: category.name_en,
          name_ar: category.name_ar,
          slug: category.slug,
          visible_in_xtream: category.visible_in_xtream,
          is_adult: category.is_adult,
        }
      : { name_en: "", name_ar: "", slug: "", visible_in_xtream: true, is_adult: false },
  });
  const submit = form.handleSubmit(async (values) => {
    const { slug, ...rest } = values;
    const data: CategoryWriteRequest = { kind, ...rest, ...(slug ? { slug } : {}) };
    try {
      if (category) {
        await update.mutateAsync({ id: category.id, data });
      } else {
        await create.mutateAsync({ data });
      }
      await queryClient.invalidateQueries({ queryKey: getCategoriesListQueryKey() });
      toast.success(
        category
          ? t("categories.form.saved", { name: values.name_en })
          : t("categories.form.created", { name: values.name_en }),
      );
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, FIELD_PATHS).length === 0) {
        notifyError(t, error);
      }
    }
  });

  return (
    <Form {...form}>
      <form
        noValidate
        className="grid gap-4"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            control={form.control}
            name="name_en"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("categories.form.nameEn")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="ltr" lang="en" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="name_ar"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("categories.form.nameAr")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="rtl" lang="ar" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField
          control={form.control}
          name="slug"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("categories.form.slug")}</FormLabel>
              <FormControl>
                <Input
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  dir="ltr"
                  className="font-mono"
                  {...field}
                />
              </FormControl>
              <FormDescription>{t("categories.form.slugHelp")}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        {(["visible_in_xtream", "is_adult"] as const).map((name) => (
          <FormField
            key={name}
            control={form.control}
            name={name}
            render={({ field }) => (
              <FormItem className="flex flex-row items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5">
                <div className="grid gap-0.5">
                  <FormLabel>{t(`categories.form.${name}`)}</FormLabel>
                  <FormDescription>{t(`categories.form.${name}Help`)}</FormDescription>
                </div>
                <FormControl>
                  <Switch checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
              </FormItem>
            )}
          />
        ))}
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {category ? t("common.save") : t("categories.form.create")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

function CategoryRows({
  kind,
  categories,
  manage,
  onEdit,
  onDelete,
}: {
  kind: CategoryKind;
  categories: readonly Category[];
  manage: boolean;
  onEdit: (category: Category) => void;
  onDelete: (category: Category) => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const reorder = useCategoriesReorder();
  const update = useCategoriesUpdate();
  const queryKey = getCategoriesListQueryKey({ kind, page_size: PAGE_SIZE });

  function move(index: number, offset: -1 | 1): void {
    const ids = categories.map((category) => category.id);
    const target = index + offset;
    const moved = ids[index];
    const other = ids[target];
    if (moved === undefined || other === undefined) return;
    ids[index] = other;
    ids[target] = moved;
    // Show the new order at once; the server's answer (or a refetch on failure) settles it.
    const byId = new Map(categories.map((category) => [category.id, category]));
    queryClient.setQueryData<PaginatedCategoryList>(queryKey, (previous) =>
      previous
        ? {
            ...previous,
            results: ids.flatMap((id) => {
              const category = byId.get(id);
              return category ? [category] : [];
            }),
          }
        : previous,
    );
    reorder.mutate(
      { data: { kind, ids } },
      {
        onError: (error) => {
          notifyError(t, error);
        },
        onSettled: () => {
          void queryClient.invalidateQueries({ queryKey: getCategoriesListQueryKey() });
        },
      },
    );
  }

  function toggleVisible(category: Category, visible: boolean): void {
    update.mutate(
      { id: category.id, data: { visible_in_xtream: visible } },
      {
        onSuccess: () => {
          toast.success(
            visible
              ? t("categories.shown", { name: localName(category, i18n.language) })
              : t("categories.hidden", { name: localName(category, i18n.language) }),
          );
        },
        onError: (error) => {
          notifyError(t, error);
        },
        onSettled: () => {
          void queryClient.invalidateQueries({ queryKey: getCategoriesListQueryKey() });
        },
      },
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12 text-end">{t("categories.columns.order")}</TableHead>
            <TableHead>{t("categories.columns.nameEn")}</TableHead>
            <TableHead>{t("categories.columns.nameAr")}</TableHead>
            <TableHead>{t("categories.columns.slug")}</TableHead>
            <TableHead>{t("categories.columns.xtream")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("categories.columns.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {categories.map((category, index) => {
            const name = localName(category, i18n.language);
            return (
              <TableRow key={category.id}>
                <TableCell className="text-end tabular-nums text-muted-foreground">
                  {index + 1}
                </TableCell>
                <TableCell className="font-medium" lang="en" dir="ltr">
                  <span className="inline-flex items-center gap-1.5">
                    {category.name_en}
                    {category.is_adult ? (
                      <Badge tone="danger">{t("categories.adult")}</Badge>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell lang="ar" dir="rtl">
                  {category.name_ar}
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-muted-foreground" dir="ltr">
                    {category.slug}
                  </span>
                </TableCell>
                <TableCell>
                  <Switch
                    checked={category.visible_in_xtream}
                    disabled={!manage}
                    aria-label={t("categories.visibleLabel", { name })}
                    onCheckedChange={(checked) => {
                      toggleVisible(category, checked);
                    }}
                  />
                </TableCell>
                <TableCell>
                  {manage ? (
                    <div className="flex items-center justify-end gap-0.5">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        disabled={index === 0 || reorder.isPending}
                        aria-label={t("categories.moveUp", { name })}
                        onClick={() => {
                          move(index, -1);
                        }}
                      >
                        <ArrowUp aria-hidden="true" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        disabled={index === categories.length - 1 || reorder.isPending}
                        aria-label={t("categories.moveDown", { name })}
                        onClick={() => {
                          move(index, 1);
                        }}
                      >
                        <ArrowDown aria-hidden="true" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={t("categories.edit", { name })}
                        onClick={() => {
                          onEdit(category);
                        }}
                      >
                        <Pencil aria-hidden="true" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-danger-text"
                        aria-label={t("categories.delete.label", { name })}
                        onClick={() => {
                          onDelete(category);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </div>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function Categories() {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  const queryClient = useQueryClient();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const kind = search.kind ?? "vod";
  const query = useCategoriesList({ kind, page_size: PAGE_SIZE });
  const remove = useCategoriesDelete();
  const [editing, setEditing] = useState<Category | "new" | null>(null);
  const [deleting, setDeleting] = useState<Category | null>(null);

  const newButton = manage ? (
    <Button
      onClick={() => {
        setEditing("new");
      }}
    >
      <Plus aria-hidden="true" />
      {t("categories.new")}
    </Button>
  ) : null;

  return (
    <>
      <PageHeader
        title={t("categories.title")}
        description={t("categories.description")}
        actions={newButton}
      />
      <Tabs
        value={kind}
        onValueChange={(value) => {
          const next = KINDS.find((candidate) => candidate === value);
          void navigate({ search: next && next !== "vod" ? { kind: next } : {}, replace: true });
        }}
        className="mb-4"
      >
        <TabsList>
          {KINDS.map((value) => (
            <TabsTrigger key={value} value={value}>
              {t(`categories.kinds.${value}`)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <Card className="p-0">
        {query.isPending ? (
          <div className="grid gap-2 p-4" role="status" aria-live="polite">
            <span className="sr-only">{t("layout.loading")}</span>
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : query.isError ? (
          <QueryError
            error={query.error}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.results.length === 0 ? (
          <EmptyState
            icon={<FolderTree />}
            title={t("categories.empty.title")}
            description={t("categories.empty.description")}
            action={newButton}
          />
        ) : (
          <CategoryRows
            kind={kind}
            categories={query.data.results}
            manage={manage}
            onEdit={setEditing}
            onDelete={setDeleting}
          />
        )}
      </Card>
      <Dialog
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editing === "new"
                ? t("categories.form.newTitle", { kind: t(`categories.kinds.${kind}`) })
                : t("categories.form.editTitle")}
            </DialogTitle>
            <DialogDescription>{t("categories.form.description")}</DialogDescription>
          </DialogHeader>
          {editing !== null ? (
            <CategoryForm
              kind={editing === "new" ? kind : editing.kind}
              category={editing === "new" ? null : editing}
              onDone={() => {
                setEditing(null);
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        tone="danger"
        title={t("categories.delete.title", {
          name: deleting ? localName(deleting, i18n.language) : "",
        })}
        description={t("categories.delete.description")}
        confirmLabel={t("categories.delete.confirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: deleting.id });
            toast.success(
              t("categories.delete.done", { name: localName(deleting, i18n.language) }),
            );
          } catch (error) {
            notifyError(t, error);
            throw error;
          } finally {
            void queryClient.invalidateQueries({ queryKey: getCategoriesListQueryKey() });
          }
        }}
      />
    </>
  );
}

export function CategoriesPage() {
  const { t } = useTranslation();
  usePageTitle(t("categories.title"));
  return (
    <RequirePermission permission={CATEGORY_VIEW}>
      <Categories />
    </RequirePermission>
  );
}
