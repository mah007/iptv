import { CalendarRange, ListFilter, Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Badge } from "./badge";
import { Button } from "./button";
import { Checkbox } from "./checkbox";
import { Input } from "./input";
import { Kbd } from "./kbd";
import { Label } from "./label";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { Separator } from "./separator";

const SEARCH_DEBOUNCE_MS = 300;

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

export interface SearchInputProps {
  /** Committed value (e.g. from the URL). */
  value: string;
  /** Called with the new value after typing pauses. */
  onValueChange: (value: string) => void;
  placeholder?: string;
  /** Focus with the `/` key from anywhere on the page (SPEC §8.2). */
  shortcut?: boolean;
  className?: string;
}

/** Debounced search field that stays in sync with an external (URL) value. */
export function SearchInput({
  value,
  onValueChange,
  placeholder,
  shortcut = true,
  className,
}: SearchInputProps) {
  const { t } = useTranslation("ui");
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(value);
  const [committed, setCommitted] = useState(value);
  // Adopt external changes (back button, cleared filters) without an effect,
  // but keep what is being typed when it already matches (e.g. a trailing space).
  if (value !== committed) {
    setCommitted(value);
    if (value !== draft.trim()) setDraft(value);
  }

  useEffect(() => {
    if (draft.trim() === committed) return;
    const timer = setTimeout(() => {
      onValueChange(draft.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [draft, committed, onValueChange]);

  useEffect(() => {
    if (!shortcut) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      event.preventDefault();
      input.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [shortcut]);

  return (
    <div className={cn("relative w-full sm:w-72", className)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        ref={input}
        type="search"
        role="searchbox"
        value={draft}
        placeholder={placeholder ?? t("filter.search")}
        aria-label={placeholder ?? t("filter.search")}
        className="h-8 pe-8 ps-8 text-ui [&::-webkit-search-cancel-button]:hidden"
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && draft !== "") {
            event.preventDefault();
            setDraft("");
          }
        }}
      />
      {draft ? (
        <button
          type="button"
          className="absolute end-1.5 top-1/2 grid size-5 -translate-y-1/2 cursor-pointer place-items-center rounded-[4px] text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={t("filter.clearSearch")}
          onClick={() => {
            setDraft("");
            input.current?.focus();
          }}
        >
          <X aria-hidden="true" className="size-3.5" />
        </button>
      ) : shortcut ? (
        <Kbd className="absolute end-2 top-1/2 -translate-y-1/2">/</Kbd>
      ) : null}
    </div>
  );
}

export interface FacetOption {
  value: string;
  label: string;
  /** Number of matching rows, when the API reports it. */
  count?: number;
}

export interface FacetFilterProps {
  title: string;
  options: readonly FacetOption[];
  selected: readonly string[];
  onSelectedChange: (values: string[]) => void;
}

/** Multi-select facet (e.g. Status, Plan) in a popover; the trigger shows what is chosen. */
export function FacetFilter({ title, options, selected, onSelectedChange }: FacetFilterProps) {
  const { t } = useTranslation("ui");
  const id = useId();
  const chosen = options.filter((option) => selected.includes(option.value));

  function toggle(value: string, checked: boolean): void {
    const next = checked
      ? [...selected, value]
      : selected.filter((candidate) => candidate !== value);
    // Keep the options' order so URLs are stable.
    onSelectedChange(
      options.map((option) => option.value).filter((candidate) => next.includes(candidate)),
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn("border-dashed", chosen.length > 0 && "border-solid")}
        >
          <ListFilter aria-hidden="true" />
          {title}
          {chosen.length > 0 ? (
            <>
              <Separator orientation="vertical" className="mx-0.5 h-4" />
              {chosen.length <= 2 ? (
                chosen.map((option) => (
                  <Badge key={option.value} tone="primary" className="ring-0">
                    {option.label}
                  </Badge>
                ))
              ) : (
                <Badge tone="primary" className="ring-0">
                  {t("filter.selectedCount", { count: chosen.length })}
                </Badge>
              )}
            </>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-60 p-1">
        <fieldset>
          <legend className="sr-only">{title}</legend>
          <ul className="grid gap-0.5">
            {options.map((option) => {
              const optionId = `${id}-${option.value}`;
              const checked = selected.includes(option.value);
              return (
                <li key={option.value}>
                  <Label
                    htmlFor={optionId}
                    className="flex h-8 cursor-pointer items-center gap-2 rounded-badge px-2 font-normal hover:bg-accent"
                  >
                    <Checkbox
                      id={optionId}
                      checked={checked}
                      onCheckedChange={(value) => {
                        toggle(option.value, value === true);
                      }}
                    />
                    <span className="flex-1 truncate">{option.label}</span>
                    {option.count === undefined ? null : (
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {option.count}
                      </span>
                    )}
                  </Label>
                </li>
              );
            })}
          </ul>
        </fieldset>
        {chosen.length > 0 ? (
          <>
            <Separator className="my-1" />
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-center"
              onClick={() => {
                onSelectedChange([]);
              }}
            >
              {t("filter.clear")}
            </Button>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

export interface DateRange {
  /** ISO dates (YYYY-MM-DD), inclusive. */
  from?: string | undefined;
  to?: string | undefined;
}

export interface DateRangeFilterProps {
  title: string;
  value: DateRange;
  onValueChange: (value: DateRange) => void;
}

/** From/to date filter using native date inputs (Gregorian, keyboard accessible). */
export function DateRangeFilter({ title, value, onValueChange }: DateRangeFilterProps) {
  const { t } = useTranslation("ui");
  const id = useId();
  const active = Boolean(value.from ?? value.to);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn("border-dashed", active && "border-solid")}
        >
          <CalendarRange aria-hidden="true" />
          {title}
          {active ? (
            <>
              <Separator orientation="vertical" className="mx-0.5 h-4" />
              <Badge tone="primary" className="ring-0 font-mono" dir="ltr">
                {t("filter.range", {
                  from: value.from ?? t("filter.open"),
                  to: value.to ?? t("filter.open"),
                })}
              </Badge>
            </>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="grid w-64 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-from`}>{t("filter.from")}</Label>
          <Input
            id={`${id}-from`}
            type="date"
            value={value.from ?? ""}
            max={value.to}
            onChange={(event) => {
              onValueChange({ ...value, from: event.target.value || undefined });
            }}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-to`}>{t("filter.to")}</Label>
          <Input
            id={`${id}-to`}
            type="date"
            value={value.to ?? ""}
            min={value.from}
            onChange={(event) => {
              onValueChange({ ...value, to: event.target.value || undefined });
            }}
          />
        </div>
        {active ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onValueChange({});
            }}
          >
            {t("filter.clear")}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

export interface FilterChip {
  id: string;
  label: ReactNode;
  onRemove: () => void;
}

export interface FilterBarProps {
  /** Search field (usually a <SearchInput>). */
  search?: ReactNode;
  /** Facet and date controls. */
  filters?: ReactNode;
  /** Active filters as removable chips. */
  chips?: readonly FilterChip[];
  /** Clears every filter; shown when chips are present. */
  onClearAll?: () => void;
  /** End-aligned controls, e.g. the column picker or an export button. */
  actions?: ReactNode;
  className?: string;
}

export function FilterBar({
  search,
  filters,
  chips = [],
  onClearAll,
  actions,
  className,
}: FilterBarProps) {
  const { t } = useTranslation("ui");
  return (
    <div data-slot="filter-bar" className={cn("grid gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        {search}
        {filters}
        {actions ? <div className="ms-auto flex items-center gap-2">{actions}</div> : null}
      </div>
      {chips.length > 0 ? (
        <ul className="flex flex-wrap items-center gap-1.5" aria-label={t("filter.active")}>
          {chips.map((chip) => (
            <li key={chip.id}>
              <span className="inline-flex h-6 items-center gap-1 rounded-badge border border-border bg-muted/60 ps-2 pe-0.5 text-xs text-foreground">
                {chip.label}
                <button
                  type="button"
                  className="grid size-5 cursor-pointer place-items-center rounded-[4px] text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={t("filter.remove")}
                  onClick={chip.onRemove}
                >
                  <X aria-hidden="true" className="size-3" />
                </button>
              </span>
            </li>
          ))}
          {onClearAll ? (
            <li>
              <Button variant="link" size="xs" className="text-xs" onClick={onClearAll}>
                {t("filter.clearAll")}
              </Button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
