import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { installEventSource, library, mediaSignedIn, scanJob } from "../test/media-fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const LIBRARIES = "/api/v1/admin/libraries";
const STREAM = "/api/v1/admin/libraries/lib-1/scan/stream";

describe("libraries", () => {
  it("lists libraries with their stats and scan history", async () => {
    mockApi(
      mediaSignedIn({
        [`GET ${LIBRARIES}`]: { body: page([library()]) },
        "GET /api/v1/admin/scans": { body: page([scanJob()]) },
      }),
    );
    renderApp("/libraries");
    const card = (await screen.findByRole("heading", { name: /^Movies/ })).closest(
      "[data-slot=card]",
    );
    expect(card?.textContent).toContain("/media/movies");
    expect(card?.textContent).toContain("Transcode at ingest");
    expect(card?.textContent).toContain("4 movies");
    expect(card?.textContent).toContain("1 to review");
    expect(screen.getByRole("heading", { name: "Scan history" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Log" })).toBeTruthy();
  });

  it("starts a scan and follows its progress on the scan stream", async () => {
    const EventSource = installEventSource();
    const api = mockApi(
      mediaSignedIn({
        [`GET ${LIBRARIES}`]: { body: page([library()]) },
        "GET /api/v1/admin/scans": { body: page([]) },
        [`POST ${LIBRARIES}/lib-1/scan`]: {
          status: 202,
          body: { job: scanJob({ id: "scan-2", status: "queued" }), created: true },
        },
      }),
    );
    const { user } = renderApp("/libraries");
    await user.click(await screen.findByRole("button", { name: "Scan now" }));
    expect(await screen.findByText("Scan of Movies started")).toBeTruthy();
    expect(api.sent("POST", `${LIBRARIES}/lib-1/scan`)).toHaveLength(1);

    await waitFor(() => {
      expect(EventSource.open(STREAM)).toHaveLength(1);
    });
    const [stream] = EventSource.open(STREAM);
    stream?.open();
    stream?.emit("scan", {
      ...scanJob({ id: "scan-2", status: "running", found: 3, new: 2, errors: 1 }),
      library_id: "lib-1",
      finished_at: null,
    });
    const card = screen.getByRole("heading", { name: /^Movies/ }).closest("[data-slot=card]");
    expect(within(card as HTMLElement).getByText("Running")).toBeTruthy();
    expect(within(card as HTMLElement).getByText("Live")).toBeTruthy();
    expect(within(card as HTMLElement).getByRole("progressbar")).toBeTruthy();

    stream?.emit("scan", {
      ...scanJob({ id: "scan-2", status: "done", found: 5, new: 2 }),
      library_id: "lib-1",
    });
    // The scan ended: the stream closes and the library list is fetched again.
    await waitFor(() => {
      expect(EventSource.open(STREAM)).toHaveLength(0);
    });
    await waitFor(() => {
      expect(api.sent("GET", LIBRARIES).length).toBeGreaterThan(1);
    });
  });

  it("adds a library and shows the server's refusal of a path outside /media", async () => {
    let attempts = 0;
    const api = mockApi(
      mediaSignedIn({
        [`GET ${LIBRARIES}`]: { body: page([]) },
        "GET /api/v1/admin/scans": { body: page([]) },
        [`POST ${LIBRARIES}`]: () => {
          attempts += 1;
          return attempts === 1
            ? problem(400, "VALIDATION_ERROR", {
                path: ["Libraries must be folders under /media."],
              })
            : { status: 201, body: library({ id: "lib-9", name: "Kids" }) };
        },
      }),
    );
    const { user } = renderApp("/libraries");
    expect(await screen.findByText("No libraries yet")).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "Add library" })[0] as HTMLElement);
    const sheet = await screen.findByRole("dialog", { name: "Add library" });
    await user.type(within(sheet).getByLabelText("Name"), "Kids");
    const folder = within(sheet).getByLabelText("Folder");
    await user.clear(folder);
    await user.type(folder, "/etc");
    await user.click(within(sheet).getByRole("button", { name: "Add library" }));
    expect(await within(sheet).findByText("Libraries must be folders under /media.")).toBeTruthy();

    await user.clear(folder);
    await user.type(folder, "/media/kids");
    await user.click(within(sheet).getByRole("radio", { name: /Serve the source/ }));
    await user.click(within(sheet).getByRole("button", { name: "Add library" }));
    expect(await screen.findByText("Kids added")).toBeTruthy();
    expect(api.sent("POST", LIBRARIES).at(-1)?.body).toEqual({
      name: "Kids",
      kind: "movies",
      path: "/media/kids",
      processing_policy: "passthrough",
      scan_interval_min: 15,
      enabled: true,
    });
  });

  it("is read-only without library.manage", async () => {
    mockApi(
      mediaSignedIn(
        {
          [`GET ${LIBRARIES}`]: { body: page([library()]) },
          "GET /api/v1/admin/scans": { body: page([]) },
        },
        ["dashboard.view", "library.view"],
      ),
    );
    renderApp("/libraries");
    expect(await screen.findByRole("heading", { name: /^Movies/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Scan now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add library" })).toBeNull();
  });
});
