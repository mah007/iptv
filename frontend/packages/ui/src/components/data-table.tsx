import {
  columnVisibilityFeature,
  createColumnHelper,
  functionalUpdate,
  metaHelper,
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type Column,
  type ColumnVisibilityState,
  type PaginationState,
  type RowData,
  type RowSelectionState,
  type SortingState,
  type TableOptions,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  Columns3,
} from "lucide-react";
import { useId, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";
import { PAGE_SIZE_OPTIONS } from "../lib/table-search";
import { Button } from "./button";
import { Checkbox } from "./checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./dropdown-menu";
import { EmptyState, ErrorState } from "./empty-state";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./select";
import { Skeleton } from "./skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";

export interface DataTableColumnMeta {
  /** Name in the column picker when the header isn't a plain string. */
  label?: string;
  /** Numbers and money align to the inline end. */
  align?: "start" | "center" | "end";
  headerClassName?: string;
  cellClassName?: string;
}

/** The TanStack Table v9 features every DataTable registers (server-driven). */
export const dataTableFeatures = tableFeatures({
  rowSortingFeature,
  rowPaginationFeature,
  columnVisibilityFeature,
  rowSelectionFeature,
  columnMeta: metaHelper<DataTableColumnMeta>(),
});

export type DataTableFeatures = typeof dataTableFeatures;
export type DataTableColumns<TData extends RowData> = TableOptions<
  DataTableFeatures,
  TData
>["columns"];

/** Typed column helper for DataTable columns: `helper.columns([helper.accessor(...)])`. */
export function createDataTableColumnHelper<TData extends RowData>() {
  return createColumnHelper<DataTableFeatures, TData>();
}

export interface DataTableProps<TData extends RowData> {
  /** Accessible name of the table, e.g. "Customers". */
  label: string;
  columns: DataTableColumns<TData>;
  /** The current page from the server; undefined while the first page loads. */
  data: readonly TData[] | undefined;
  /** Stable id (e.g. the UUID) so selection survives paging and refetches. */
  getRowId: (row: TData) => string;
  /** Total matching rows on the server. */
  rowCount: number | undefined;
  pagination: PaginationState;
  onPaginationChange: (pagination: PaginationState) => void;
  /** Column ids double as the API's ordering fields. */
  sorting: SortingState;
  /** Called with the new sorting; go back to the first page in the same URL update. */
  onSortingChange: (sorting: SortingState) => void;
  /** Hidden columns are `false`; uncontrolled when omitted. */
  columnVisibility?: ColumnVisibilityState;
  onColumnVisibilityChange?: (visibility: ColumnVisibilityState) => void;
  /** Passing a handler adds the checkbox column. */
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: (selection: RowSelectionState) => void;
  /** Actions for the selected rows, shown in a bar above the table. */
  bulkActions?: (selectedIds: string[]) => ReactNode;
  /** Filters and search, placed before the column picker. */
  toolbar?: ReactNode;
  /** First load: skeleton rows. */
  loading?: boolean;
  /** Background refetch: a thin progress bar, rows stay visible. */
  fetching?: boolean;
  error?: boolean;
  errorTitle?: ReactNode;
  errorDescription?: ReactNode;
  onRetry?: () => void;
  /** Shown when the page has no rows. */
  emptyState?: ReactNode;
  pageSizeOptions?: readonly number[];
  /** Mouse shortcut to a row's detail; keep a real link in the row for keyboard users. */
  onRowClick?: (row: TData) => void;
  className?: string;
}

const EMPTY: readonly never[] = [];
const SELECT_COLUMN_ID = "__select";
const INTERACTIVE =
  "a, button, input, select, textarea, label, [role='checkbox'], [role='menuitem']";

function columnLabel<TData extends RowData>(column: Column<DataTableFeatures, TData>) {
  const header = column.columnDef.header;
  return column.columnDef.meta?.label ?? (typeof header === "string" ? header : column.id);
}

function alignClass(align: DataTableColumnMeta["align"]): string | undefined {
  if (align === "end") return "text-end";
  if (align === "center") return "text-center";
  return undefined;
}

/**
 * Server-driven data table (SPEC §8.1): sorting and pagination happen on the
 * API, state lives in the URL (see `table-search`), with column visibility,
 * row selection plus bulk actions, sticky header, skeleton, empty and error
 * states.
 */
export function DataTable<TData extends RowData>({
  label,
  columns,
  data,
  getRowId,
  rowCount,
  pagination,
  onPaginationChange,
  sorting,
  onSortingChange,
  columnVisibility,
  onColumnVisibilityChange,
  rowSelection,
  onRowSelectionChange,
  bulkActions,
  toolbar,
  loading = false,
  fetching = false,
  error = false,
  errorTitle,
  errorDescription,
  onRetry,
  emptyState,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  onRowClick,
  className,
}: DataTableProps<TData>) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const pageSizeLabelId = useId();
  const [ownVisibility, setOwnVisibility] = useState<ColumnVisibilityState>({});
  const visibility = columnVisibility ?? ownVisibility;
  const selectable = onRowSelectionChange !== undefined;
  const selection = rowSelection ?? {};

  const allColumns = useMemo<DataTableColumns<TData>>(() => {
    if (!selectable) return columns;
    const helper = createDataTableColumnHelper<TData>();
    const select = helper.display({
      id: SELECT_COLUMN_ID,
      enableHiding: false,
      enableSorting: false,
      header: ({ table }) => (
        <Checkbox
          aria-label={t("dataTable.selectPage")}
          checked={
            table.getIsAllPageRowsSelected()
              ? true
              : table.getIsSomePageRowsSelected()
                ? "indeterminate"
                : false
          }
          onCheckedChange={(value) => {
            table.toggleAllPageRowsSelected(value === true);
          }}
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          aria-label={t("dataTable.selectRow")}
          checked={row.getIsSelected()}
          onCheckedChange={(value) => {
            row.toggleSelected(value === true);
          }}
        />
      ),
      meta: { headerClassName: "w-10", cellClassName: "w-10" },
    });
    return [select, ...columns];
  }, [columns, selectable, t]);

  const table = useTable({
    features: dataTableFeatures,
    columns: allColumns,
    data: data ?? EMPTY,
    getRowId: (row) => getRowId(row),
    rowCount: rowCount ?? 0,
    manualPagination: true,
    manualSorting: true,
    autoResetPageIndex: false,
    enableMultiSort: false,
    enableRowSelection: selectable,
    state: { pagination, sorting, columnVisibility: visibility, rowSelection: selection },
    onPaginationChange: (updater) => {
      onPaginationChange(functionalUpdate(updater, pagination));
    },
    onSortingChange: (updater) => {
      onSortingChange(functionalUpdate(updater, sorting));
    },
    onColumnVisibilityChange: (updater) => {
      const next = functionalUpdate(updater, visibility);
      setOwnVisibility(next);
      onColumnVisibilityChange?.(next);
    },
    onRowSelectionChange: (updater) => {
      onRowSelectionChange?.(functionalUpdate(updater, selection));
    },
  });

  const rows = table.getRowModel().rows;
  const visibleColumnCount = table.getVisibleLeafColumns().length;
  const hideableColumns = table.getAllLeafColumns().filter((column) => column.getCanHide());
  const selectedIds = Object.keys(selection);
  const total = rowCount ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pagination.pageSize));
  const firstRow = total === 0 ? 0 : pagination.pageIndex * pagination.pageSize + 1;
  const lastRow = Math.min(total, (pagination.pageIndex + 1) * pagination.pageSize);
  const canPrevious = pagination.pageIndex > 0;
  const canNext = pagination.pageIndex + 1 < pageCount;

  function goToPage(pageIndex: number): void {
    onPaginationChange({ ...pagination, pageIndex });
  }

  function toggleSort(column: Column<DataTableFeatures, TData>): void {
    // Ascending, then descending, then back to the API's default order.
    const current = column.getIsSorted();
    if (current === false) onSortingChange([{ id: column.id, desc: false }]);
    else if (current === "asc") onSortingChange([{ id: column.id, desc: true }]);
    else onSortingChange([]);
  }

  function handleRowClick(event: MouseEvent<HTMLTableRowElement>, row: TData): void {
    if (!onRowClick) return;
    if (event.target instanceof Element && event.target.closest(INTERACTIVE)) return;
    onRowClick(row);
  }

  let body: ReactNode;
  if (loading) {
    body = Array.from({ length: Math.min(pagination.pageSize, 10) }, (_, index) => (
      <TableRow key={`skeleton-${String(index)}`} className="hover:bg-transparent">
        {table.getVisibleLeafColumns().map((column) => (
          <TableCell key={column.id}>
            <Skeleton
              className={cn("h-4", column.id === SELECT_COLUMN_ID ? "size-4" : "w-[70%] max-w-40")}
            />
          </TableCell>
        ))}
      </TableRow>
    ));
  } else if (error) {
    body = (
      <TableRow className="hover:bg-transparent">
        <TableCell colSpan={visibleColumnCount} className="h-auto">
          <ErrorState
            title={errorTitle ?? t("dataTable.error.title")}
            description={errorDescription ?? t("dataTable.error.description")}
            onRetry={onRetry}
          />
        </TableCell>
      </TableRow>
    );
  } else if (rows.length === 0) {
    body = (
      <TableRow className="hover:bg-transparent">
        <TableCell colSpan={visibleColumnCount} className="h-auto">
          {emptyState ?? (
            <EmptyState
              title={t("dataTable.empty.title")}
              description={t("dataTable.empty.description")}
            />
          )}
        </TableCell>
      </TableRow>
    );
  } else {
    body = rows.map((row) => (
      <TableRow
        key={row.id}
        data-state={row.getIsSelected() ? "selected" : undefined}
        className={cn(onRowClick && "cursor-pointer")}
        onClick={
          onRowClick
            ? (event) => {
                handleRowClick(event, row.original);
              }
            : undefined
        }
      >
        {row.getVisibleCells().map((cell) => {
          const meta = cell.column.columnDef.meta;
          return (
            <TableCell key={cell.id} className={cn(alignClass(meta?.align), meta?.cellClassName)}>
              <table.FlexRender cell={cell} />
            </TableCell>
          );
        })}
      </TableRow>
    ));
  }

  return (
    <div data-slot="data-table" className={cn("grid gap-3", className)}>
      {toolbar !== undefined || hideableColumns.length > 0 ? (
        <div className="flex flex-wrap items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{toolbar}</div>
          {hideableColumns.length > 0 ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Columns3 aria-hidden="true" />
                  {t("dataTable.columns")}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>{t("dataTable.toggleColumns")}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {hideableColumns.map((column) => (
                  <DropdownMenuCheckboxItem
                    key={column.id}
                    checked={column.getIsVisible()}
                    onCheckedChange={(value) => {
                      column.toggleVisibility(value);
                    }}
                    onSelect={(event) => {
                      // Keep the menu open while picking several columns.
                      event.preventDefault();
                    }}
                  >
                    {columnLabel(column)}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      ) : null}

      <div className="relative overflow-hidden rounded-card border border-border bg-card shadow-xs">
        {fetching && !loading ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 z-20 h-0.5 overflow-hidden"
          >
            <div className="h-full w-1/3 animate-indeterminate bg-primary" />
          </div>
        ) : null}

        {selectable && bulkActions && selectedIds.length > 0 ? (
          <div
            role="region"
            aria-label={t("dataTable.bulkActions")}
            className="flex flex-wrap items-center gap-2 border-b border-border bg-primary/5 px-3 py-2"
          >
            <span className="text-ui font-medium text-foreground">
              {t("dataTable.selected", { count: selectedIds.length })}
            </span>
            <div className="flex items-center gap-2">{bulkActions(selectedIds)}</div>
            <Button
              variant="ghost"
              size="xs"
              className="ms-auto"
              onClick={() => {
                onRowSelectionChange({});
              }}
            >
              {t("dataTable.clearSelection")}
            </Button>
          </div>
        ) : null}

        <Table aria-label={label} aria-busy={loading || fetching || undefined}>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id} className="hover:bg-transparent">
                {group.headers.map((header) => {
                  const column = header.column;
                  const meta = column.columnDef.meta;
                  const sortable = column.getCanSort();
                  const sorted = column.getIsSorted();
                  const alignEnd = meta?.align === "end";
                  return (
                    <TableHead
                      key={header.id}
                      colSpan={header.colSpan}
                      aria-sort={
                        sortable
                          ? sorted === "asc"
                            ? "ascending"
                            : sorted === "desc"
                              ? "descending"
                              : "none"
                          : undefined
                      }
                      className={cn(alignClass(meta?.align), meta?.headerClassName)}
                    >
                      {header.isPlaceholder ? null : sortable ? (
                        <button
                          type="button"
                          onClick={() => {
                            toggleSort(column);
                          }}
                          className={cn(
                            "group inline-flex h-7 cursor-pointer items-center gap-1 rounded-badge px-2 outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                            alignEnd ? "-me-2 flex-row-reverse" : "-ms-2",
                            sorted !== false && "text-foreground",
                          )}
                        >
                          <table.FlexRender header={header} />
                          {sorted === "asc" ? (
                            <ArrowUp aria-hidden="true" className="size-3.5" />
                          ) : sorted === "desc" ? (
                            <ArrowDown aria-hidden="true" className="size-3.5" />
                          ) : (
                            <ChevronsUpDown
                              aria-hidden="true"
                              className="size-3.5 opacity-40 group-hover:opacity-100"
                            />
                          )}
                        </button>
                      ) : (
                        <table.FlexRender header={header} />
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>{body}</TableBody>
        </Table>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border px-3 py-2 text-ui text-muted-foreground">
          <div aria-live="polite">
            {loading ? (
              <Skeleton className="h-4 w-36" />
            ) : total > 0 ? (
              t("dataTable.range", {
                from: format.number(firstRow),
                to: format.number(lastRow),
                total: format.number(total),
              })
            ) : (
              t("dataTable.noRows")
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-2">
              <span id={pageSizeLabelId}>{t("dataTable.rowsPerPage")}</span>
              <Select
                value={String(pagination.pageSize)}
                onValueChange={(value) => {
                  onPaginationChange({ pageIndex: 0, pageSize: Number(value) });
                }}
              >
                <SelectTrigger size="sm" className="w-18" aria-labelledby={pageSizeLabelId}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {pageSizeOptions.map((size) => (
                    <SelectItem key={size} value={String(size)}>
                      {format.number(size)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <span>
              {t("dataTable.page", {
                page: format.number(pagination.pageIndex + 1),
                pages: format.number(pageCount),
              })}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("dataTable.firstPage")}
                disabled={!canPrevious || loading}
                onClick={() => {
                  goToPage(0);
                }}
              >
                <ChevronsLeft aria-hidden="true" className="rtl:rotate-180" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("dataTable.previousPage")}
                disabled={!canPrevious || loading}
                onClick={() => {
                  goToPage(pagination.pageIndex - 1);
                }}
              >
                <ChevronLeft aria-hidden="true" className="rtl:rotate-180" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("dataTable.nextPage")}
                disabled={!canNext || loading}
                onClick={() => {
                  goToPage(pagination.pageIndex + 1);
                }}
              >
                <ChevronRight aria-hidden="true" className="rtl:rotate-180" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("dataTable.lastPage")}
                disabled={!canNext || loading}
                onClick={() => {
                  goToPage(pageCount - 1);
                }}
              >
                <ChevronsRight aria-hidden="true" className="rtl:rotate-180" />
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
