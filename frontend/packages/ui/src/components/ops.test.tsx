import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { nth, renderWithUi } from "../test-utils";
import { countryFlagEmoji, CountryFlag } from "./country-flag";
import { DescriptionItem, DescriptionList } from "./description-list";
import { DeviceIcon, deviceKind } from "./device-icon";
import { LiveIndicator } from "./live-indicator";
import { LiveDuration, RelativeTime } from "./relative-time";
import { Timeline, TimelineItem } from "./timeline";

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

describe("RelativeTime", () => {
  it("says how long ago, with the exact Riyadh time in a tooltip", async () => {
    const user = userEvent.setup();
    renderWithUi(<RelativeTime value="2026-10-03T09:30:00Z" addSuffix={false} />);
    const time = document.querySelector("time");
    expect(time?.getAttribute("dateTime")).toBe("2026-10-03T09:30:00.000Z");
    if (!time) throw new Error("no <time>");
    await user.hover(time);
    expect((await screen.findByRole("tooltip")).textContent).toBe("3 Oct 2026, 12:30 GMT+3");
  });

  it("speaks the UI language", () => {
    const value = Date.now() - 3 * 60_000;
    const { unmount } = renderWithUi(<RelativeTime value={value} />);
    expect(screen.getByText("3 minutes ago").tagName).toBe("TIME");
    unmount();
    renderWithUi(<RelativeTime value={value} />, { language: "ar" });
    expect(screen.getByText("منذ 3 دقائق")).toBeTruthy();
  });

  it("never calls a moment that just passed the future", () => {
    renderWithUi(<RelativeTime value={Date.now() + 5_000} />);
    expect(screen.getByText("less than a minute ago")).toBeTruthy();
  });

  it("renders nothing for an invalid date", () => {
    renderWithUi(<RelativeTime value="not a date" />);
    expect(document.querySelector("time")).toBeNull();
  });
});

describe("LiveDuration", () => {
  it("ticks every second and pauses while the tab is hidden", () => {
    vi.useFakeTimers({ now: new Date("2026-10-03T12:00:00Z") });
    renderWithUi(<LiveDuration since="2026-10-03T10:47:55Z" />);
    expect(screen.getByText("1:12:05").getAttribute("dateTime")).toBe("PT1H12M5S");

    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByText("1:12:07")).toBeTruthy();

    act(() => {
      setVisibility("hidden");
    });
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByText("1:12:07")).toBeTruthy();

    act(() => {
      setVisibility("visible");
    });
    expect(screen.getByText("1:12:12")).toBeTruthy();
  });
});

describe("LiveIndicator", () => {
  it("shows the count and names it for screen readers", () => {
    const { container } = renderWithUi(<LiveIndicator count={1284} />);
    expect(screen.getByText("1,284").getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByText("1284 live streams").className).toContain("sr-only");
    const indicator = container.querySelector("[data-slot=live-indicator]");
    expect(indicator?.getAttribute("data-live")).toBe("true");
    expect(indicator?.querySelector(".motion-safe\\:animate-ping")).toBeTruthy();
  });

  it.each([
    [0, "لا توجد عمليات بث نشطة"],
    [1, "عملية بث نشطة واحدة"],
    [2, "عمليتا بث نشطتان"],
    [3, "3 عمليات بث نشطة"],
    [11, "11 عملية بث نشطة"],
    [100, "100 عملية بث نشطة"],
  ])("uses the Arabic plural form for %d", (count, label) => {
    renderWithUi(<LiveIndicator count={count} />, { language: "ar" });
    expect(screen.getByText(label)).toBeTruthy();
  });

  it("stops pulsing at zero", () => {
    const { container } = renderWithUi(<LiveIndicator count={0} />);
    expect(container.querySelector("[data-live]")).toBeNull();
    expect(container.querySelector(".motion-safe\\:animate-ping")).toBeNull();
  });
});

describe("CountryFlag", () => {
  it("shows the flag labelled with the country name", () => {
    renderWithUi(<CountryFlag code="sa" />);
    expect(screen.getByRole("img", { name: "Saudi Arabia" }).textContent).toBe("🇸🇦");
  });

  it("names the country in Arabic", () => {
    renderWithUi(<CountryFlag code="EG" showName />, { language: "ar" });
    expect(screen.getByText("مصر")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it.each(["XX", "ZZ", "A1", "", null])("shows a globe for %j", (code) => {
    renderWithUi(<CountryFlag code={code} />);
    const flag = screen.getByRole("img", { name: "Unknown country" });
    expect(flag.querySelector("svg")).toBeTruthy();
  });

  it("maps UK to the GB flag", () => {
    expect(countryFlagEmoji("uk")).toBe("🇬🇧");
    expect(countryFlagEmoji("A1")).toBeNull();
  });
});

describe("DeviceIcon", () => {
  it.each([
    ["tv", "tv"],
    ["TiviMate", "tv"],
    ["Mozilla/5.0 (Linux; Android 11; AFTKA) AppleWebKit/537.36", "tv"],
    ["Mozilla/5.0 (Linux; Android 12; BRAVIA 4K) AppleWebKit/537.36", "tv"],
    ["IPTV Smarters Pro (iPhone)", "smartphone"],
    ["smartphone", "smartphone"],
    ["Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Mobile/15E148", "tablet"],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0", "monitor"],
    ["IPTVnator", "monitor"],
    ["IPTV Smarters", "unknown"],
    [null, "unknown"],
  ])("reads %j as %s", (hint, kind) => {
    expect(deviceKind(hint)).toBe(kind);
  });

  it("labels the icon, or hides it when decorative", () => {
    const { container } = renderWithUi(
      <>
        <DeviceIcon hint="TiviMate" />
        <DeviceIcon hint="iPad" decorative />
      </>,
    );
    expect(screen.getByRole("img", { name: "TV" }).getAttribute("data-kind")).toBe("tv");
    expect(container.querySelector("[data-kind=tablet]")?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("Timeline", () => {
  it("lists events with actor, time and collapsible details", async () => {
    const user = userEvent.setup();
    renderWithUi(
      <Timeline aria-label="History">
        <TimelineItem
          title="Subscription extended"
          actor={null}
          at={Date.now() - 2 * 60_000}
          details={<pre>{"{ days: 30 }"}</pre>}
        >
          30 days added
        </TimelineItem>
        <TimelineItem title="Customer created" actor="Mahmoud" />
      </Timeline>,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getByText("System")).toBeTruthy();
    expect(screen.getByText("2 minutes ago")).toBeTruthy();
    expect(screen.getByText("30 days added")).toBeTruthy();
    expect(screen.getByText("Mahmoud")).toBeTruthy();

    const details = nth(items, 0).querySelector("details");
    expect(details?.open).toBe(false);
    await user.click(screen.getByText("Show details"));
    expect(details?.open).toBe(true);
    expect(nth(items, 1).querySelector("details")).toBeNull();
  });
});

describe("DescriptionList", () => {
  it("pairs labels with values and marks empty values", () => {
    renderWithUi(
      <DescriptionList>
        <DescriptionItem label="Plan">Basic</DescriptionItem>
        <DescriptionItem label="Notes">{null}</DescriptionItem>
      </DescriptionList>,
    );
    const terms = screen.getAllByRole("term").map((term) => term.textContent);
    const values = screen.getAllByRole("definition").map((value) => value.textContent);
    expect(terms).toEqual(["Plan", "Notes"]);
    expect(values).toEqual(["Basic", "—Not set"]);
    expect(screen.getByText("—").getAttribute("aria-hidden")).toBe("true");
  });
});
