import { useCategoriesList, type CategoryKind } from "@smart-iptv/api";
import { Checkbox, Label, Skeleton } from "@smart-iptv/ui";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { localName } from "./artwork";

const KIND_ORDER: readonly CategoryKind[] = ["vod", "series", "live"];

/** Categories grouped by kind, each a checkbox (plans and access profiles). */
export function CategoryChecklist({
  value,
  onChange,
  disabled = false,
  label,
}: {
  value: readonly string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  /** Names the group for assistive technology. */
  label: string;
}) {
  const { t, i18n } = useTranslation();
  const categories = useCategoriesList({ page_size: 100 });
  const groups = useMemo(
    () =>
      KIND_ORDER.map((kind) => ({
        kind,
        items: (categories.data?.results ?? []).filter((category) => category.kind === kind),
      })).filter((group) => group.items.length > 0),
    [categories.data],
  );
  if (categories.isPending) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-52" />
      </div>
    );
  }
  if (categories.isError) {
    return <p className="text-ui text-danger-text">{t("customers.access.categoriesError")}</p>;
  }
  if (groups.length === 0) {
    return <p className="text-ui text-muted-foreground">{t("customers.access.noCategories")}</p>;
  }
  return (
    <div
      role="group"
      aria-label={label}
      className="grid gap-4 rounded-input border border-border p-3 sm:grid-cols-3"
    >
      {groups.map((group) => (
        <div key={group.kind} className="grid content-start gap-2">
          <p className="text-xs font-medium uppercase text-muted-foreground ltr:tracking-wide">
            {t(`customers.categoryKinds.${group.kind}`)}
          </p>
          {group.items.map((category) => (
            <Label key={category.id} className="flex items-center gap-2 font-normal">
              <Checkbox
                checked={value.includes(category.id)}
                disabled={disabled}
                onCheckedChange={(checked) => {
                  onChange(
                    checked === true
                      ? [...value, category.id]
                      : value.filter((id) => id !== category.id),
                  );
                }}
              />
              <span className="truncate">{localName(category, i18n.language)}</span>
            </Label>
          ))}
        </div>
      ))}
    </div>
  );
}
