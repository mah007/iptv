import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { nth, renderWithUi } from "../test-utils";
import { createDataTableColumnHelper, DataTable, type DataTableProps } from "./data-table";

interface Person {
  id: string;
  name: string;
  age: number;
}

const helper = createDataTableColumnHelper<Person>();
const columns = helper.columns([
  helper.accessor("name", { header: "Name" }),
  helper.accessor("age", { header: "Age", meta: { align: "end" } }),
]);
const people: Person[] = [
  { id: "p1", name: "Ada", age: 36 },
  { id: "p2", name: "Linus", age: 54 },
];

function renderTable(overrides: Partial<DataTableProps<Person>> = {}) {
  const onPaginationChange = vi.fn();
  const onSortingChange = vi.fn();
  const props: DataTableProps<Person> = {
    label: "People",
    columns,
    data: people,
    getRowId: (person) => person.id,
    rowCount: 60,
    pagination: { pageIndex: 0, pageSize: 25 },
    onPaginationChange,
    sorting: [],
    onSortingChange,
    ...overrides,
  };
  const view = renderWithUi(<DataTable {...props} />);
  return { ...view, onPaginationChange, onSortingChange, user: userEvent.setup() };
}

function headerButton(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^${name}`) });
}

describe("DataTable", () => {
  it("renders the rows with the server range and page count", () => {
    renderTable();
    const table = screen.getByRole("table", { name: "People" });
    expect(within(table).getByText("Ada")).toBeTruthy();
    expect(within(table).getByText("54")).toBeTruthy();
    expect(screen.getByText("1–25 of 60")).toBeTruthy();
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  });

  it("cycles sorting ascending, descending, then off", async () => {
    const { user, onSortingChange, rerender } = renderTable();
    const nameHeader = screen.getByRole("columnheader", { name: /Name/ });
    expect(nameHeader.getAttribute("aria-sort")).toBe("none");

    await user.click(headerButton("Name"));
    expect(onSortingChange).toHaveBeenLastCalledWith([{ id: "name", desc: false }]);

    rerender(
      <DataTable
        label="People"
        columns={columns}
        data={people}
        getRowId={(person) => person.id}
        rowCount={60}
        pagination={{ pageIndex: 0, pageSize: 25 }}
        onPaginationChange={vi.fn()}
        sorting={[{ id: "name", desc: false }]}
        onSortingChange={onSortingChange}
      />,
    );
    expect(screen.getByRole("columnheader", { name: /Name/ }).getAttribute("aria-sort")).toBe(
      "ascending",
    );
    await user.click(headerButton("Name"));
    expect(onSortingChange).toHaveBeenLastCalledWith([{ id: "name", desc: true }]);
  });

  it("clears sorting after descending", async () => {
    const { user, onSortingChange } = renderTable({ sorting: [{ id: "age", desc: true }] });
    expect(screen.getByRole("columnheader", { name: /Age/ }).getAttribute("aria-sort")).toBe(
      "descending",
    );
    await user.click(headerButton("Age"));
    expect(onSortingChange).toHaveBeenLastCalledWith([]);
  });

  it("pages forward and to the end, never past the bounds", async () => {
    const { user, onPaginationChange } = renderTable();
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "First page" })).toHaveProperty("disabled", true);

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPaginationChange).toHaveBeenLastCalledWith({ pageIndex: 1, pageSize: 25 });

    await user.click(screen.getByRole("button", { name: "Last page" }));
    expect(onPaginationChange).toHaveBeenLastCalledWith({ pageIndex: 2, pageSize: 25 });
  });

  it("disables next on the last page and goes back from there", async () => {
    const { user, onPaginationChange } = renderTable({
      pagination: { pageIndex: 2, pageSize: 25 },
    });
    expect(screen.getByText("51–60 of 60")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next page" })).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPaginationChange).toHaveBeenLastCalledWith({ pageIndex: 1, pageSize: 25 });
  });

  it("changing the page size returns to the first page", async () => {
    const { user, onPaginationChange } = renderTable({
      pagination: { pageIndex: 1, pageSize: 25 },
    });
    await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await user.click(await screen.findByRole("option", { name: "50" }));
    expect(onPaginationChange).toHaveBeenLastCalledWith({ pageIndex: 0, pageSize: 50 });
  });

  it("shows skeleton rows while the first page loads", () => {
    renderTable({ data: undefined, rowCount: undefined, loading: true });
    const table = screen.getByRole("table", { name: "People" });
    expect(table.getAttribute("aria-busy")).toBe("true");
    expect(within(table).queryByText("Ada")).toBeNull();
    expect(table.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
  });

  it("shows an empty state when the page has no rows", () => {
    renderTable({ data: [], rowCount: 0 });
    expect(screen.getAllByText("No results").length).toBeGreaterThan(0);
    expect(screen.getByText("Nothing matches these filters yet.")).toBeTruthy();
  });

  it("shows an error state with a retry", async () => {
    const onRetry = vi.fn();
    const { user } = renderTable({ data: undefined, error: true, onRetry });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Couldn't load this list")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("selects rows and shows bulk actions for the selection", async () => {
    const onRowSelectionChange = vi.fn();
    const { user } = renderTable({ rowSelection: {}, onRowSelectionChange });
    const rowBoxes = screen.getAllByRole("checkbox", { name: "Select row" });
    expect(rowBoxes).toHaveLength(2);
    await user.click(nth(rowBoxes, 0));
    expect(onRowSelectionChange).toHaveBeenLastCalledWith({ p1: true });
  });

  it("selects the whole page from the header checkbox", async () => {
    const onRowSelectionChange = vi.fn();
    const { user } = renderTable({ rowSelection: {}, onRowSelectionChange });
    await user.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
    expect(onRowSelectionChange).toHaveBeenLastCalledWith({ p1: true, p2: true });
  });

  it("renders the bulk bar for selected ids and clears it", async () => {
    const onRowSelectionChange = vi.fn();
    const bulkActions = vi.fn((ids: string[]) => (
      <button type="button">Suspend {ids.length}</button>
    ));
    const { user } = renderTable({
      rowSelection: { p2: true },
      onRowSelectionChange,
      bulkActions,
    });
    const bar = screen.getByRole("region", { name: "Actions for selected rows" });
    expect(within(bar).getByText("1 selected")).toBeTruthy();
    expect(bulkActions).toHaveBeenLastCalledWith(["p2"]);
    await user.click(within(bar).getByRole("button", { name: "Clear selection" }));
    expect(onRowSelectionChange).toHaveBeenLastCalledWith({});
  });

  it("hides a column from the column picker", async () => {
    const onColumnVisibilityChange = vi.fn();
    const { user } = renderTable({ onColumnVisibilityChange });
    expect(screen.getByRole("columnheader", { name: /Age/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Columns" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Age" }));
    expect(onColumnVisibilityChange).toHaveBeenLastCalledWith({ age: false });
    expect(screen.queryByRole("columnheader", { name: /Age/ })).toBeNull();
  });

  it("opens a row on click but not when a control inside it is used", async () => {
    const onRowClick = vi.fn();
    const onRowSelectionChange = vi.fn();
    const { user } = renderTable({ onRowClick, rowSelection: {}, onRowSelectionChange });
    await user.click(screen.getByText("Linus"));
    expect(onRowClick).toHaveBeenLastCalledWith(people[1]);
    await user.click(nth(screen.getAllByRole("checkbox", { name: "Select row" }), 0));
    expect(onRowClick).toHaveBeenCalledOnce();
  });
});
