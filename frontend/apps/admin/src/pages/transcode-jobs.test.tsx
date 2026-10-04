import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { installEventSource, mediaSignedIn, transcodeJob } from "../test/media-fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const JOBS = "/api/v1/admin/transcode-jobs";
const STREAM = `${JOBS}/stream`;

const FAILED = transcodeJob({
  id: "job-2",
  status: "failed",
  progress: 12,
  backend: "cpu",
  encoder: "libx264",
  error: "ffmpeg exited with status 1",
  error_tail: "Invalid data found when processing input",
  attempts: 3,
  file: { id: "file-2", library: "Movies", relative_path: "Broken.mkv", duration_ms: null },
  title: null,
});

async function openFeed(EventSource: ReturnType<typeof installEventSource>) {
  await waitFor(() => {
    expect(EventSource.open(STREAM)).toHaveLength(1);
  });
  const [feed] = EventSource.open(STREAM);
  if (feed === undefined) throw new Error("no transcode feed");
  feed.open();
  return feed;
}

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  if (!(row instanceof HTMLElement)) throw new Error(`no row for ${text}`);
  return row;
}

describe("transcode jobs", () => {
  it("lists jobs with progress, fps, speed, ETA and the encoder, updated live by the feed", async () => {
    const EventSource = installEventSource();
    mockApi(mediaSignedIn({ [`GET ${JOBS}`]: { body: page([transcodeJob(), FAILED]) } }));
    renderApp("/transcode");
    const feed = await openFeed(EventSource);
    const running = (
      await screen.findByText("Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.mkv")
    ).closest("tr") as HTMLElement;
    expect(within(running).getByText("NVENC")).toBeTruthy();
    expect(within(running).getByText("240 fps")).toBeTruthy();
    expect(within(running).getByText("9.8× real time")).toBeTruthy();
    expect(within(running).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("42");

    feed.emit("job", { ...transcodeJob(), progress: 87, fps: 251, eta_s: 12 });
    await waitFor(() => {
      expect(within(running).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("87");
    });
    expect(within(running).getByText("251 fps")).toBeTruthy();

    const failed = rowOf("Broken.mkv");
    expect(within(failed).getByText("Failed")).toBeTruthy();
    expect(within(failed).getByText("Attempt 3")).toBeTruthy();
  });

  it("shows a failed job's error tail, and retries it", async () => {
    installEventSource();
    const api = mockApi(
      mediaSignedIn({
        [`GET ${JOBS}`]: { body: page([FAILED]) },
        [`POST ${JOBS}/job-2/retry`]: { body: { ...FAILED, status: "queued" } },
      }),
    );
    const { user } = renderApp("/transcode?status=failed");
    await user.click(await screen.findByRole("button", { name: /ffmpeg exited with status 1/ }));
    const dialog = await screen.findByRole("dialog", { name: "Why the job failed" });
    expect(within(dialog).getByText("Invalid data found when processing input")).toBeTruthy();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Job queued again")).toBeTruthy();
    expect(api.sent("POST", `${JOBS}/job-2/retry`)).toHaveLength(1);
    expect(api.sent("GET", JOBS)[0]?.query.getAll("status")).toEqual(["failed"]);
  });

  it("cancels a running job after confirmation, and explains a 409", async () => {
    installEventSource();
    let attempts = 0;
    const api = mockApi(
      mediaSignedIn({
        [`GET ${JOBS}`]: { body: page([transcodeJob()]) },
        [`POST ${JOBS}/job-1/cancel`]: () => {
          attempts += 1;
          return attempts === 1
            ? { body: transcodeJob({ status: "cancelled" }) }
            : problem(409, "CONFLICT");
        },
      }),
    );
    const { user } = renderApp("/transcode");
    for (const message of ["Job cancelled", "That job had already finished"]) {
      await user.click(await screen.findByRole("button", { name: "Cancel" }));
      const confirm = await screen.findByRole("alertdialog", { name: "Cancel this job?" });
      await user.click(within(confirm).getByRole("button", { name: "Cancel job" }));
      expect(await screen.findByText(message)).toBeTruthy();
    }
    expect(api.sent("POST", `${JOBS}/job-1/cancel`)).toHaveLength(2);
  });

  it("changes a job's priority", async () => {
    installEventSource();
    const api = mockApi(
      mediaSignedIn({
        [`GET ${JOBS}`]: { body: page([transcodeJob({ status: "queued", progress: 0 })]) },
        [`POST ${JOBS}/job-1/priority`]: { body: transcodeJob({ status: "queued", priority: 9 }) },
      }),
    );
    const { user } = renderApp("/transcode");
    await user.click(await screen.findByRole("combobox", { name: /^Priority of/ }));
    await user.click(await screen.findByRole("option", { name: "9" }));
    expect(await screen.findByText("Priority set to 9")).toBeTruthy();
    expect(api.sent("POST", `${JOBS}/job-1/priority`)[0]?.body).toEqual({ priority: 9 });
  });

  it("is read-only without library.manage", async () => {
    installEventSource();
    mockApi(
      mediaSignedIn({ [`GET ${JOBS}`]: { body: page([transcodeJob(), FAILED]) } }, [
        "dashboard.view",
        "library.view",
      ]),
    );
    renderApp("/transcode");
    expect(await screen.findByText("Broken.mkv")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /^Priority of/ })).toBeNull();
  });
});
