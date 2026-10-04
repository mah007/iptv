import { usePlansList, useCustomersList, type Plan } from "@smart-iptv/api";
import {
  Button,
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandLoading,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  cn,
  useFormatters,
} from "@smart-iptv/ui";
import { ChevronsUpDown, UserRound } from "lucide-react";
import { forwardRef, useDeferredValue, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { localName } from "../catalog/artwork";

/** A customer chosen in a billing form. */
export interface PickedCustomer {
  id: string;
  name: string;
}

/**
 * Search customers by name, email, phone or username and pick one (a combobox:
 * the trigger opens a filtered listbox; the API does the matching).
 */
export const CustomerPicker = forwardRef<
  HTMLButtonElement,
  {
    value: PickedCustomer | null;
    onChange: (customer: PickedCustomer) => void;
  } & Omit<ComponentProps<"button">, "value" | "onChange">
>(function CustomerPicker({ value, onChange, className, ...props }, ref) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const search = useDeferredValue(query.trim());
  const customers = useCustomersList(search ? { search, page_size: 8 } : { page_size: 8 }, {
    query: { enabled: open },
  });
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          ref={ref}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn("w-full justify-between font-normal", className)}
          {...props}
        >
          <span className={cn("truncate", value === null && "text-muted-foreground")}>
            {value === null ? t("billing.pickers.customer") : <bdi>{value.name}</bdi>}
          </span>
          <ChevronsUpDown aria-hidden="true" className="opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) min-w-72 p-0" align="start">
        <Command shouldFilter={false} label={t("billing.pickers.customer")}>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder={t("billing.pickers.customerSearch")}
          />
          <CommandList>
            {customers.isFetching ? (
              <CommandLoading>{t("palette.searching")}</CommandLoading>
            ) : null}
            <CommandEmpty>{t("palette.empty")}</CommandEmpty>
            <CommandGroup>
              {(customers.data?.results ?? []).map((customer) => (
                <CommandItem
                  key={customer.id}
                  value={customer.id}
                  onSelect={() => {
                    onChange({ id: customer.id, name: customer.name || customer.username });
                    setOpen(false);
                  }}
                >
                  <UserRound aria-hidden="true" />
                  <bdi className="truncate">{customer.name || customer.username}</bdi>
                  <span className="ms-auto truncate text-xs text-muted-foreground" dir="ltr">
                    {customer.phone || customer.email || customer.username}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
});

/** A plan's name in the admin's language. */
export function planName(plan: Pick<Plan, "name_en" | "name_ar">, language: string): string {
  return localName({ name_en: plan.name_en, name_ar: plan.name_ar }, language);
}

/** Pick a plan on sale (trial plans only when `trial`). */
export const PlanSelect = forwardRef<
  HTMLButtonElement,
  {
    value: string;
    onChange: (planId: string) => void;
    trial?: boolean;
    /** A plan to leave out, e.g. the subscription's current one. */
    exclude?: string | undefined;
  } & Omit<ComponentProps<"button">, "value" | "onChange">
>(function PlanSelect({ value, onChange, trial = false, exclude, ...props }, ref) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const plans = usePlansList({ active: true, is_trial: trial });
  const options = (plans.data ?? []).filter((plan) => plan.id !== exclude);
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger ref={ref} {...props}>
        <SelectValue placeholder={t("billing.pickers.plan")} />
      </SelectTrigger>
      <SelectContent>
        {options.map((plan) => (
          <SelectItem key={plan.id} value={plan.id}>
            <span className="flex items-center gap-2">
              <bdi>{planName(plan, i18n.language)}</bdi>
              <span className="text-xs text-muted-foreground tabular-nums">
                {format.money(plan.price_total.total, plan.currency)}
              </span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
});
