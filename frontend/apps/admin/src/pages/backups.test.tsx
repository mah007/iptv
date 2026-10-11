import type { BackupRun, BackupSummary } from "@smart-iptv/api";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ALL_PERMISSIONS, me, page, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The Backups page (ADR-0020): the backup service, where and when, the last restore
 * drill, the history, and "Back up now". */

const HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();

function run(overrides: Partial<BackupRun> = {}): BackupRun {
  return {
    id: "run-1",
    kind: "db_diff",
    status: "succeeded",
    trigger: "schedule",
    requested_by: null,
    created_at: HOUR_AGO,
    started_at: HOUR_AGO,
    finished_at: HOUR_AGO,
    duration_s: 2.5,
    size_bytes: 1_048_576,
    detail: {},
    error: "",
    ...overrides,
  };
}

const DRILL = run({
  id: "run-2",
  kind: "drill",
  duration_s: 4.4,
  size_bytes: null,
  detail: {
    restore_s: 2.1,
    ready_s: 4.4,
    recovered_to: HOUR_AGO,
    migrations: [["core", "0001"]],
    counts: { accounts_user: 57 },
    verify_ok: true,
    verdict: { problems: [], live_counts: { accounts_user: 57 } },
  },
});

const SUMMARY: BackupSummary = {
  enabled: true,
  agent: {
    seen_at: new Date().toISOString(),
    healthy: true,
    archiving: true,
    passphrase: true,
    disk_total_bytes: 500 * 1024 ** 3,
    disk_free_bytes: 120 * 1024 ** 3,
    db_backups: [],
    local_snapshots: { ok: true, snapshots: 3, latest: HOUR_AGO },
    offsite_snapshots: null,
  },
  local_dir: "/var/backups/smart-iptv",
  s3: { enabled: false, configured: false, endpoint: "", bucket: "", prefix: "smart-iptv" },
  next_backup_at: new Date(Date.now() + 3_600_000).toISOString(),
  next_drill_at: new Date(Date.now() + 86_400_000).toISOString(),
  last_success_db_at: HOUR_AGO,
  last_success_drill_at: HOUR_AGO,
  pending: 0,
  latest: { db: run(), files: null, offsite: null, drill: DRILL, check: null },
};

const OWNER = me({ permissions: [...ALL_PERMISSIONS, "backups.view", "backups.run"] });
const VIEWER = me({ roles: ["viewer"], permissions: ["backups.view"] });

describe("backups", () => {
  it("shows the backups and the last drill, and backs up now", async () => {
    const api = mockApi(
      signedIn(
        {
          "GET /api/v1/admin/backups": { body: SUMMARY },
          "GET /api/v1/admin/backups/runs": { body: page([run(), DRILL]) },
          "POST /api/v1/admin/backups/run": { status: 202, body: [run({ status: "queued" })] },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/backups");
    expect(await screen.findByRole("heading", { level: 1, name: "Backups" })).toBeTruthy();
    expect(await screen.findByText("/var/backups/smart-iptv")).toBeTruthy();
    expect(screen.getByRole("table", { name: "Rows restored" })).toBeTruthy();
    expect(await screen.findByText("Database (differential)")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back up now" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/backups/run")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/backups/run")[0]?.body).toEqual({ what: "backup" });
  });

  it("lets viewers look but not start jobs", async () => {
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/backups": { body: SUMMARY },
          "GET /api/v1/admin/backups/runs": { body: page([]) },
        },
        VIEWER,
      ),
    );
    renderApp("/backups");
    expect(await screen.findByText("No backups yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Back up now" })).toBeNull();
  });

  it("warns when the backup service is down or backups are off", async () => {
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/backups": {
            body: {
              ...SUMMARY,
              enabled: false,
              agent: { ...SUMMARY.agent, healthy: false },
              latest: { ...SUMMARY.latest, drill: null },
            },
          },
          "GET /api/v1/admin/backups/runs": { body: page([]) },
        },
        OWNER,
      ),
    );
    renderApp("/backups");
    expect(await screen.findByText("Backups are switched off")).toBeTruthy();
    expect(screen.getByText("The backup service is not answering")).toBeTruthy();
    expect(screen.getByText("No drill yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Back up now" })).toHaveProperty("disabled", true);
  });

  it("shows a failed job's reason", async () => {
    const failed = run({ id: "run-3", status: "failed", error: "restic: repository locked\nmore" });
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/backups": { body: SUMMARY },
          "GET /api/v1/admin/backups/runs": { body: page([failed]) },
        },
        OWNER,
      ),
    );
    renderApp("/backups");
    expect(await screen.findByText("restic: repository locked")).toBeTruthy();
  });

  it("is refused without the permission", async () => {
    mockApi(
      signedIn(
        { "GET /api/v1/admin/backups": problem(403, "PERMISSION_DENIED") },
        me({ roles: ["support"], permissions: ["customers.view"] }),
      ),
    );
    renderApp("/backups");
    expect(await screen.findByText("You don't have access to this")).toBeTruthy();
  });
});
