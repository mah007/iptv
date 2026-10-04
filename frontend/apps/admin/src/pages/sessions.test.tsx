import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { applySessionEvent } from "../features/sessions/live-sessions";
import { installEventSource, liveSession, mediaSignedIn } from "../test/media-fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const STREAM = "/api/v1/admin/sessions/stream";

async function openFeed(EventSource: ReturnType<typeof installEventSource>) {
  await waitFor(() => {
    expect(EventSource.open(STREAM)).toHaveLength(1);
  });
  const [feed] = EventSource.open(STREAM);
  if (feed === undefined) throw new Error("no session feed");
  feed.open();
  return feed;
}

describe("live sessions", () => {
  it("applies the feed: a snapshot replaces, a diff adds, updates and removes", () => {
    const first = applySessionEvent(new Map(), "snapshot", {
      sessions: [liveSession(), liveSession({ id: "sess-2" })],
    });
    const next = applySessionEvent(first, "diff", {
      added: [liveSession({ id: "sess-3" })],
      updated: [liveSession({ bytes_sent: 1 })],
      removed: ["sess-2"],
    });
    expect([...next.keys()]).toEqual(["sess-1", "sess-3"]);
    expect(next.get("sess-1")?.bytes_sent).toBe(1);
    expect(applySessionEvent(next, "diff", { nonsense: true })).toBe(next);
  });

  it("shows the snapshot, then follows diffs", async () => {
    const EventSource = installEventSource();
    mockApi(mediaSignedIn());
    renderApp("/sessions");
    const feed = await openFeed(EventSource);
    feed.emit("snapshot", { sessions: [liveSession()] });
    const row = (await screen.findByRole("link", { name: "Sara Ahmed" })).closest("tr");
    expect(row?.textContent).toContain("Living room TV");
    expect(row?.textContent).toContain("The Matrix");
    expect(row?.textContent).toContain("198.51.100.7");
    expect(row?.textContent).toContain("52 MB");
    expect(within(row as HTMLElement).getByText(/^1:0\d$/)).toBeTruthy();
    expect(screen.getByText("Live")).toBeTruthy();

    feed.emit("diff", {
      added: [liveSession({ id: "sess-2", user: { id: "cust-2", name: "Omar Ali" } })],
      updated: [],
      removed: ["sess-1"],
    });
    expect(await screen.findByRole("link", { name: "Omar Ali" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Sara Ahmed" })).toBeNull();
  });

  it("polls the open sessions while the feed is refused", async () => {
    const EventSource = installEventSource();
    const listed = {
      id: "sess-1",
      user: { id: "cust-1", username: "sara", name: "Sara Ahmed" },
      device: { id: "dev-1", name: "Living room TV", kind: "xtream", app_hint: "smarters" },
      title_kind: "movie",
      title_id: "movie-1",
      title_name: "The Matrix",
      rendition: "compat",
      ip: "198.51.100.7",
      country: "SA",
      user_agent: "",
      player: "",
      started_at: new Date(Date.now() - 65_000).toISOString(),
      last_heartbeat_at: new Date().toISOString(),
      ended_at: null,
      bytes_sent: 52_000_000,
      end_reason: "",
      is_active: true,
      log_ref: "sess-1",
    };
    const api = mockApi(
      mediaSignedIn({
        "GET /api/v1/admin/sessions": {
          body: { count: 1, next: null, previous: null, results: [listed] },
        },
      }),
    );
    renderApp("/sessions");
    await waitFor(() => {
      expect(EventSource.open(STREAM)).toHaveLength(1);
    });
    EventSource.open(STREAM)[0]?.refuse();
    const row = (await screen.findByRole("link", { name: "Sara Ahmed" })).closest("tr");
    expect(row?.textContent).toContain("Living room TV");
    expect(screen.getByText("Reconnecting; refreshing every 5 s")).toBeTruthy();
    expect(api.sent("GET", "/api/v1/admin/sessions")[0]?.query.get("active")).toBe("true");
  });

  it("kills a session after confirmation and fades its row until the feed drops it", async () => {
    const EventSource = installEventSource();
    const api = mockApi(mediaSignedIn({ "POST /api/v1/admin/sessions/sess-1/kill": { body: {} } }));
    const { user } = renderApp("/sessions");
    const feed = await openFeed(EventSource);
    feed.emit("snapshot", { sessions: [liveSession()] });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Stop this session?" });
    expect(confirm.textContent).toContain("Sara Ahmed stops watching The Matrix");
    await user.click(within(confirm).getByRole("button", { name: "Stop session" }));
    expect(await screen.findByText("Session of Sara Ahmed stopped")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/sessions/sess-1/kill")).toHaveLength(1);
    expect(screen.getByText("Stopping…").closest("tr")?.dataset.state).toBe("stopping");

    feed.emit("diff", { added: [], updated: [], removed: ["sess-1"] });
    expect(await screen.findByText("Nobody is watching")).toBeTruthy();
  });

  it("says so when the session had already ended (409)", async () => {
    const EventSource = installEventSource();
    mockApi(mediaSignedIn({ "POST /api/v1/admin/sessions/sess-1/kill": problem(409, "CONFLICT") }));
    const { user } = renderApp("/sessions");
    const feed = await openFeed(EventSource);
    feed.emit("snapshot", { sessions: [liveSession()] });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Stop this session?" });
    await user.click(within(confirm).getByRole("button", { name: "Stop session" }));
    expect(await screen.findByText("That session had already ended")).toBeTruthy();
    expect(await screen.findByText("Nobody is watching")).toBeTruthy();
  });

  it("has no stop button without sessions.kill", async () => {
    const EventSource = installEventSource();
    mockApi(mediaSignedIn({}, ["dashboard.view", "customers.view"]));
    renderApp("/sessions");
    const feed = await openFeed(EventSource);
    feed.emit("snapshot", { sessions: [liveSession()] });
    expect(await screen.findByRole("link", { name: "Sara Ahmed" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
