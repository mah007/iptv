import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithUi } from "../test-utils";
import { DiffViewer } from "./diff-viewer";
import { ErrorBoundary } from "./error-boundary";
import { FacetFilter, FilterBar, SearchInput } from "./filter-bar";
import { QRCodeCard } from "./qr-code-card";
import { StatTile } from "./stat-tile";
import { Stepper } from "./stepper";

describe("DiffViewer", () => {
  it("lists changes and hides unchanged fields until asked", async () => {
    const user = userEvent.setup();
    renderWithUi(
      <DiffViewer
        before={{ status: "active", plan: "basic", streams: 1 }}
        after={{ status: "suspended", plan: "basic", streams: 1 }}
      />,
    );
    expect(screen.getByText("status")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
    expect(screen.getByText("suspended")).toBeTruthy();
    expect(screen.getByText("Changed: 1")).toBeTruthy();
    expect(screen.queryByText("plan")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show unchanged fields (2)" }));
    expect(screen.getByText("plan")).toBeTruthy();
    expect(screen.getByText("streams")).toBeTruthy();
  });

  it("says so when nothing changed", () => {
    renderWithUi(<DiffViewer before={null} after={null} />);
    expect(screen.getByText("No changes recorded.")).toBeTruthy();
  });
});

describe("ErrorBoundary", () => {
  function Flaky({ fail }: { fail: boolean }) {
    if (fail) throw new Error("boom");
    return <p>Recovered</p>;
  }

  it("contains a render error and recovers on retry", async () => {
    const user = userEvent.setup();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Harness() {
      const [fail, setFail] = useState(true);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              setFail(false);
            }}
          >
            fix
          </button>
          <ErrorBoundary>
            <Flaky fail={fail} />
          </ErrorBoundary>
        </>
      );
    }
    renderWithUi(<Harness />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Something went wrong")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "fix" }));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("Recovered")).toBeTruthy();
  });

  it("resets when a reset key changes", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = renderWithUi(
      <ErrorBoundary resetKeys={["/a"]}>
        <Flaky fail />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    rerender(
      <ErrorBoundary resetKeys={["/b"]}>
        <Flaky fail={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("Recovered")).toBeTruthy();
  });
});

describe("StatTile", () => {
  it("shows a rise as good news", () => {
    renderWithUi(<StatTile label="Active subscriptions" value="1,284" delta={0.125} />);
    expect(screen.getByText("1,284")).toBeTruthy();
    expect(screen.getByText("Up 12.5% on the previous period")).toBeTruthy();
    const delta = document.querySelector("[data-trend=up]");
    expect(delta?.className).toContain("text-success-text");
  });

  it("treats a fall as good news when the metric is inverted", () => {
    renderWithUi(
      <StatTile label="Failed jobs" value="3" delta={-2} deltaFormat="number" invertDelta />,
    );
    expect(screen.getByText("Down 2 on the previous period")).toBeTruthy();
    expect(document.querySelector("[data-trend=down]")?.className).toContain("text-success-text");
  });

  it("shows skeletons while loading", () => {
    const { container } = renderWithUi(<StatTile label="Customers" value="—" loading />);
    expect(container.querySelector("[aria-busy=true]")).toBeTruthy();
    expect(screen.queryByText("—")).toBeNull();
  });
});

describe("QRCodeCard", () => {
  it("renders a labelled QR image", () => {
    renderWithUi(<QRCodeCard value="otpauth://totp/x?secret=ABC" label="Authenticator QR code" />);
    expect(screen.getByRole("img", { name: "Authenticator QR code" })).toBeTruthy();
  });
});

describe("FilterBar", () => {
  it("debounces search and focuses it with the / key", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    renderWithUi(<FilterBar search={<SearchInput value="" onValueChange={onValueChange} />} />);
    const input = screen.getByRole<HTMLInputElement>("searchbox");

    await user.keyboard("/");
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("");

    await user.type(input, "ahmed ");
    expect(onValueChange).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(onValueChange).toHaveBeenCalledWith("ahmed");
    });
    expect(onValueChange).toHaveBeenCalledOnce();
    // The trailing space being typed survives the commit.
    expect(input.value).toBe("ahmed ");
  });

  it("adopts an external value, e.g. after the filters are cleared", () => {
    const { rerender } = renderWithUi(<SearchInput value="ahmed" onValueChange={vi.fn()} />);
    expect(screen.getByRole<HTMLInputElement>("searchbox").value).toBe("ahmed");
    rerender(<SearchInput value="" onValueChange={vi.fn()} />);
    expect(screen.getByRole<HTMLInputElement>("searchbox").value).toBe("");
  });

  it("toggles facet values in option order and removes chips", async () => {
    const user = userEvent.setup();
    const onSelectedChange = vi.fn();
    const onRemove = vi.fn();
    renderWithUi(
      <FilterBar
        filters={
          <FacetFilter
            title="Status"
            options={[
              { value: "active", label: "Active" },
              { value: "grace", label: "Grace" },
            ]}
            selected={["grace"]}
            onSelectedChange={onSelectedChange}
          />
        }
        chips={[{ id: "status", label: "Status: Grace", onRemove }]}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Status/ }));
    await user.click(await screen.findByRole("checkbox", { name: "Active" }));
    expect(onSelectedChange).toHaveBeenLastCalledWith(["active", "grace"]);

    await user.click(screen.getByRole("button", { name: "Remove filter" }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("replaces the choice of a single-value facet and can clear it", async () => {
    const user = userEvent.setup();
    const onSelectedChange = vi.fn();
    renderWithUi(
      <FacetFilter
        single
        title="Access"
        options={[
          { value: "active", label: "Active" },
          { value: "expired", label: "Expired" },
        ]}
        selected={["active"]}
        onSelectedChange={onSelectedChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Access/ }));
    const group = await screen.findByRole("radiogroup", { name: "Access" });
    expect(screen.getByRole("radio", { name: "Active" }).getAttribute("aria-checked")).toBe("true");
    expect(group.querySelectorAll("[role=checkbox]")).toHaveLength(0);

    await user.click(screen.getByRole("radio", { name: "Expired" }));
    expect(onSelectedChange).toHaveBeenLastCalledWith(["expired"]);

    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onSelectedChange).toHaveBeenLastCalledWith([]);
  });
});

describe("Stepper", () => {
  it("marks the current step and ticks the finished ones", () => {
    renderWithUi(<Stepper label="Steps" steps={["Profile", "Access", "Device"]} current={1} />);
    const nav = screen.getByRole("navigation", { name: "Steps" });
    const items = nav.querySelectorAll("li");
    expect(items).toHaveLength(3);
    expect(items[1]?.getAttribute("aria-current")).toBe("step");
    expect(items[0]?.textContent).toContain("Profile (completed)");
    expect(items[2]?.textContent).toBe("3Device");
  });
});
