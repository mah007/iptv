import type {
  Category,
  EpgSource,
  LiveChannel,
  LiveIntegration,
  LiveOverview,
  Programme,
  Unmatched,
} from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { archiveEstimate } from "../features/live/channel-sheet";
import { parseEpgSearch, parseLiveSearch } from "../features/live/search";
import { ALL_PERMISSIONS, me, page, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The M12 pages: Live TV (channels, groups, integrations) and the EPG. */

const PERMISSIONS = [...ALL_PERMISSIONS, "library.view", "library.manage"];
const NOW = new Date().toISOString();

const GROUP: Category = {
  id: "grp-1",
  xc_id: 31,
  kind: "live",
  name_en: "Showcase",
  name_ar: "عروض",
  slug: "showcase",
  sort: 10,
  is_adult: false,
  visible_in_xtream: true,
  icon: "",
  parent: null,
};

function channel(overrides: Partial<LiveChannel> = {}): LiveChannel {
  return {
    id: "chan-1",
    xc_id: 3001,
    name: "Test Pattern",
    name_ar: "نمط الاختبار",
    group: {
      id: GROUP.id,
      xc_id: GROUP.xc_id,
      name_en: GROUP.name_en,
      name_ar: GROUP.name_ar,
      sort: GROUP.sort,
      is_adult: false,
      visible_in_xtream: true,
    },
    sort: 10,
    epg_channel_id: "test-pattern.smart-iptv",
    epg_source: null,
    logo_url: "",
    source_info: { scheme: "rtsp", host: "mediamtx", port: 8554 },
    output: "ts",
    transcode: "copy",
    catchup_days: 1,
    always_on: false,
    enabled: true,
    rights_holder: "Smart IPTV",
    license_ref: "sample-media",
    license_expires_at: null,
    license_valid: true,
    origin: "manual",
    origin_ref: "",
    integration: null,
    probe: null,
    status: {
      state: "live",
      since: NOW,
      error: "",
      detail: "",
      bitrate_kbps: 700,
      viewers: 2,
      recording: true,
      archive_bytes: 52_428_800,
      archive_from: NOW,
    },
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

const OVERVIEW: LiveOverview = {
  packager_running: true,
  running_channels: 1,
  channels: 2,
  enabled_channels: 1,
  catchup_channels: 1,
  archive_bytes: 52_428_800,
  archive_budget_bytes: 1_073_741_824,
  viewers: 2,
};

function live(routes: Parameters<typeof signedIn>[0] = {}) {
  return signedIn(
    {
      "GET /api/v1/admin/live/overview": { body: OVERVIEW },
      "GET /api/v1/admin/categories": { body: page([GROUP]) },
      "GET /api/v1/admin/live/channels": {
        body: page([
          channel(),
          channel({
            id: "chan-2",
            xc_id: 3002,
            name: "Archive Night",
            name_ar: "",
            enabled: false,
            rights_holder: "",
            catchup_days: 0,
            status: { ...channel().status, state: "disabled", viewers: 0, recording: false },
          }),
        ]),
      },
      ...routes,
    },
    me({ permissions: PERMISSIONS }),
  );
}

describe("live search params", () => {
  it("keeps only known values", () => {
    expect(parseLiveSearch({ tab: "groups", group: " grp-1 ", q: "" })).toEqual({
      tab: "groups",
      group: "grp-1",
    });
    expect(parseLiveSearch({ tab: "nope" })).toEqual({});
    expect(parseEpgSearch({ channel: "chan-1", day: "2026-10-04" })).toEqual({
      channel: "chan-1",
      day: "2026-10-04",
    });
    expect(parseEpgSearch({ day: "tomorrow" })).toEqual({});
  });

  it("estimates the archive's disk use", () => {
    expect(archiveEstimate(1000, 1)).toBe(10_800_000_000);
    expect(archiveEstimate(0, 7)).toBe(0);
  });
});

describe("live TV", () => {
  it("lists channels with their state, catch-up and rights", async () => {
    mockApi(live());
    renderApp("/live");
    expect(await screen.findByRole("heading", { level: 1, name: "Live TV" })).toBeTruthy();
    expect(await screen.findByText("Test Pattern")).toBeTruthy();
    expect(screen.getByText("Live")).toBeTruthy();
    expect(screen.getByText("Recording")).toBeTruthy();
    expect(screen.getByText("Rights holder missing")).toBeTruthy();
    expect(screen.getByText("1 day")).toBeTruthy();
    expect(await screen.findByText("Running 1 channel")).toBeTruthy();
  });

  it("disables a channel from its switch", async () => {
    const api = mockApi(
      live({
        "POST /api/v1/admin/live/channels/bulk": { body: [channel({ enabled: false })] },
      }),
    );
    const { user } = renderApp("/live");
    await user.click(await screen.findByRole("switch", { name: "Enable Test Pattern" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/live/channels/bulk")[0]?.body).toEqual({
        ids: ["chan-1"],
        enabled: false,
      });
    });
  });

  it("creates a channel and tests its source first", async () => {
    const api = mockApi(
      live({
        "POST /api/v1/admin/live/source-tests": { status: 202, body: { request_id: "req1" } },
        "GET /api/v1/admin/live/source-tests/req1": {
          body: {
            status: "done",
            result: {
              ok: true,
              video: { codec: "h264", width: 1280, height: 720 },
              audio: { codec: "aac" },
              height: 720,
              bitrate_kbps: 2000,
              copy_ok: true,
            },
          },
        },
        "POST /api/v1/admin/live/channels": { status: 201, body: channel({ id: "chan-9" }) },
      }),
    );
    const { user } = renderApp("/live");
    await user.click(await screen.findByRole("button", { name: "New channel" }));
    const sheet = await screen.findByRole("dialog", { name: "New channel" });
    await user.type(within(sheet).getByLabelText("Name"), "Studio One");
    await user.type(within(sheet).getByLabelText("Source URL"), "rtsp://encoder.local:8554/studio");
    await user.click(within(sheet).getByRole("button", { name: "Test" }));
    expect(await within(sheet).findByText("The source works")).toBeTruthy();
    expect(within(sheet).getByText("It can be copied as it is.")).toBeTruthy();
    await user.clear(within(sheet).getByLabelText("Catch-up (days)"));
    await user.type(within(sheet).getByLabelText("Catch-up (days)"), "2");
    expect(within(sheet).getByText(/About .* of disk/u)).toBeTruthy();
    await user.type(within(sheet).getByLabelText("Rights holder"), "Our studio");
    await user.click(within(sheet).getByRole("button", { name: "Create channel" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/live/channels")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/live/source-tests")[0]?.body).toEqual({
      url: "rtsp://encoder.local:8554/studio",
    });
    expect(api.sent("POST", "/api/v1/admin/live/channels")[0]?.body).toMatchObject({
      name: "Studio One",
      group: "grp-1",
      source_url: "rtsp://encoder.local:8554/studio",
      catchup_days: 2,
      rights_holder: "Our studio",
    });
  });

  it("shows a refused source on its field, in the admin's language", async () => {
    mockApi(
      live({
        "POST /api/v1/admin/live/channels": {
          status: 400,
          body: {
            ...problem(400, "VALIDATION_ERROR").body,
            field_errors: {
              source_url: ["The URL points at an address that is never fetched from."],
            },
            field_error_codes: { source_url: ["unsafe_destination"] },
          },
        },
      }),
    );
    const { user } = renderApp("/live");
    await user.click(await screen.findByRole("button", { name: "New channel" }));
    const sheet = await screen.findByRole("dialog", { name: "New channel" });
    await user.type(within(sheet).getByLabelText("Name"), "Bad");
    await user.type(within(sheet).getByLabelText("Source URL"), "http://web:8000/");
    await user.click(within(sheet).getByRole("button", { name: "Create channel" }));
    expect(await within(sheet).findByText("This address is never fetched from.")).toBeTruthy();
  });

  it("orders a group's channels when a group is picked", async () => {
    const api = mockApi(
      live({
        "POST /api/v1/admin/live/channels/reorder": { body: [] },
      }),
    );
    const { user } = renderApp("/live?group=grp-1");
    await user.click(await screen.findByRole("button", { name: "Move Archive Night up" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/live/channels/reorder")[0]?.body).toEqual({
        group: "grp-1",
        ids: ["chan-2", "chan-1"],
      });
    });
  });

  it("lists groups and syncs an integration", async () => {
    const integration: LiveIntegration = {
      id: "int-1",
      kind: "mediamtx",
      name: "Studio",
      base_url: "http://mediamtx:9997",
      stream_base: { scheme: "rtsp", host: "mediamtx", port: 8554 },
      group: null,
      rights_holder: "Our studio",
      license_ref: "",
      has_credentials: true,
      last_sync_at: NOW,
      last_error: "",
      last_result: { created: 2, updated: 0, missing: 0, total: 2 },
      channel_count: 2,
      created_at: NOW,
    };
    const api = mockApi(
      live({
        "GET /api/v1/admin/live/integrations": { body: [integration] },
        "POST /api/v1/admin/live/integrations/int-1/sync": { status: 202, body: { queued: true } },
      }),
    );
    const { user } = renderApp("/live?tab=groups");
    expect(await screen.findByText("Showcase")).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "Integrations" }));
    expect(await screen.findByText(/2 new, 0 changed, 0 gone/u)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/live/integrations/int-1/sync")).toHaveLength(1);
    });
  });

  it("speaks Arabic", async () => {
    mockApi(live());
    renderApp("/live", { language: "ar" });
    expect(await screen.findByRole("heading", { level: 1, name: "البث المباشر" })).toBeTruthy();
    expect(await screen.findByText("مباشر")).toBeTruthy();
  });
});

describe("EPG", () => {
  const source: EpgSource = {
    id: "src-1",
    name: "Demo guide",
    kind: "url",
    url: { scheme: "https", host: "guide.example.com", port: null },
    upload_name: "",
    refresh_cron: "0 */6 * * *",
    priority: 100,
    enabled: true,
    epg_id: 1,
    integration: null,
    last_run_at: NOW,
    last_ok_at: NOW,
    last_error: "",
    stats: { programmes: 240 },
    channel_count: 3,
    created_at: NOW,
  };
  const unmatched: Unmatched = {
    channels: [
      {
        id: "chan-2",
        name: "Archive Night",
        epg_channel_id: "",
        reason: "no_id",
        suggestions: [
          {
            id: "ec-1",
            source: "src-1",
            source_name: "Demo guide",
            xmltv_id: "archive.example",
            name: "Archive",
            names: { en: "Archive" },
            icon_url: "",
          },
        ],
      },
    ],
    guide_channels: [
      { xmltv_id: "other.example", name: "Other", source_id: "src-1", source_name: "Demo guide" },
    ],
  };
  const start = new Date();
  start.setUTCMinutes(0, 0, 0);
  const programme: Programme = {
    id: "p-1",
    start: start.toISOString(),
    stop: new Date(start.getTime() + 3_600_000).toISOString(),
    title: "Evening Showcase",
    title_ar: "عرض المساء",
    description: "A curated selection.",
    description_ar: "",
    category: "Showcase",
    lang: "en",
  };

  function epg(routes: Parameters<typeof signedIn>[0] = {}) {
    return live({
      "GET /api/v1/admin/live/epg/sources": { body: [source] },
      "GET /api/v1/admin/live/epg/unmatched": { body: unmatched },
      "GET /api/v1/admin/live/channels/chan-1/programmes": { body: [programme] },
      ...routes,
    });
  }

  it("lists sources, unmatched channels and a programme preview", async () => {
    mockApi(epg());
    renderApp("/epg");
    expect(await screen.findByRole("heading", { level: 1, name: "EPG" })).toBeTruthy();
    expect(await screen.findByText("Demo guide")).toBeTruthy();
    expect(screen.getByText("3 channels, 240 programmes")).toBeTruthy();
    expect(await screen.findByText("No guide id")).toBeTruthy();
    expect(screen.getByText("other.example")).toBeTruthy();
    expect(await screen.findByText("Evening Showcase")).toBeTruthy();
    expect(screen.getByText("On air")).toBeTruthy();
  });

  it("refreshes a source and maps a suggested guide channel", async () => {
    const api = mockApi(
      epg({
        "POST /api/v1/admin/live/epg/sources/src-1/refresh": { status: 202 },
        "PATCH /api/v1/admin/live/channels/chan-2": { body: channel({ id: "chan-2" }) },
      }),
    );
    const { user } = renderApp("/epg");
    await user.click(await screen.findByRole("button", { name: "Import Demo guide now" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/live/epg/sources/src-1/refresh")).toHaveLength(1);
    });
    await user.click(
      await screen.findByRole("button", { name: "Use archive.example for Archive Night" }),
    );
    await waitFor(() => {
      expect(api.sent("PATCH", "/api/v1/admin/live/channels/chan-2")[0]?.body).toEqual({
        epg_channel_id: "archive.example",
      });
    });
  });
});
