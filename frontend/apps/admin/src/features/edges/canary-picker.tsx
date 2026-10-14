import { useCustomersList } from "@smart-iptv/api";
import { Badge, Button, SearchInput } from "@smart-iptv/ui";
import { X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/** Customers a canary edge serves: search by name, user name, email or phone; chips. */
export function CanaryPicker({
  value,
  onChange,
  labelledBy,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
  labelledBy?: string;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const query = useCustomersList(
    { search, page_size: 8 },
    { query: { enabled: search.trim().length >= 2 } },
  );
  const results = (query.data?.results ?? []).filter((c) => !value.includes(c.id));
  return (
    <div className="grid gap-2" aria-labelledby={labelledBy} role="group">
      <SearchInput
        value={search}
        onValueChange={setSearch}
        placeholder={t("edges.form.canarySearch")}
        shortcut={false}
      />
      {results.length > 0 ? (
        <ul className="grid gap-1 rounded-input border border-border p-1">
          {results.map((customer) => (
            <li key={customer.id}>
              <button
                type="button"
                className="w-full rounded-badge px-2 py-1.5 text-start text-ui hover:bg-accent"
                onClick={() => {
                  setNames((known) => ({
                    ...known,
                    [customer.id]: customer.name || customer.username,
                  }));
                  onChange([...value, customer.id]);
                  setSearch("");
                }}
              >
                <span className="font-medium">{customer.name || customer.username}</span>{" "}
                <span className="text-muted-foreground" dir="ltr">
                  {customer.username}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {value.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("edges.form.canaryNone")}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {value.map((id) => (
            <li key={id}>
              <Badge tone="neutral" className="gap-1">
                <span dir="auto">{names[id] ?? t("edges.form.canaryCustomer")}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="size-5"
                  aria-label={t("edges.form.canaryRemove", { name: names[id] ?? id })}
                  onClick={() => {
                    onChange(value.filter((item) => item !== id));
                  }}
                >
                  <X aria-hidden="true" />
                </Button>
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
