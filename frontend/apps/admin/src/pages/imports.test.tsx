import type { ImportJob, ImportSource, ImportStatus } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page } from "../test/fixtures";
import { mediaSignedIn } from "../test/media-fixtures";
import { mockApi, problem, type MockRequest, type Routes } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const BASE = "/api/v1/admin/imports";

function status(overrides: Partial<ImportStatus> = {}): ImportStatus {
  return {
    importer_online: true,
    max_concurrent: 1,
    reserve_bytes: 10 * 1024 ** 3,
    bandwidth_mbit: 0,
    libraries: [
      {
        id: "lib-1",
        name: "Movies",
        kind: "movies",
        free_bytes: 500e9,
        total_bytes: 2e12,
      },
    ],
    ...overrides,
  };
}

function source(overrides: Partial<ImportSource> = {}): ImportSource {
  return {
    id: "src-1",
    name: "Wasabi",
    kind: "s3",
    options: { provider: "wasabi", endpoint: "https://s3.wasabisys.com", root: "films" },
    summary: "s3.wasabisys.com/films",
    secrets_set: ["access_key_id", "secret_access_key"],
    last_test_at: null,
    last_test_status: "ok",
    last_test_message: "",
    created_at: "2026-10-16T10:00:00Z",
    updated_at: "2026-10-16T10:00:00Z",
    ...overrides,
  };
}

function job(overrides: Partial<ImportJob> = {}): ImportJob {
  return {
    id: "job-1",
    origin: "source",
    source: { id: "src-1", name: "Wasabi" },
    source_name: "Wasabi",
    source_kind: "s3",
    items: [{ path: "films/Metropolis (1927)", is_dir: true }],
    url: "",
    file_name: "",
    has_checksum: false,
    library: { id: "lib-1", name: "Movies" },
    library_name: "Movies",
    subfolder: "Classics",
    rights_holder: "Public domain",
    license_ref: "https://archive.org/details/metropolis",
    license_expires_at: null,
    status: "running",
    phase: "copying",
    error_code: "",
    error: "",
    error_params: {},
    attempts: 1,
    retry_at: null,
    cancel_requested: false,
    files_total: 2,
    files_done: 0,
    bytes_total: 4e9,
    bytes_done: 1e9,
    speed_bps: 50 * 1024 ** 2,
    eta_s: 60,
    skipped: { count: 0, names: [] },
    created_by_name: "Owner",
    created_at: "2026-10-16T10:00:00Z",
    started_at: "2026-10-16T10:00:05Z",
    finished_at: null,
    ...overrides,
  };
}

function routes(extra: Routes = {}, history: ImportJob[] = []): Routes {
  return {
    [`GET ${BASE}/status`]: { body: status() },
    [`GET ${BASE}/sources`]: { body: [source()] },
    [`GET ${BASE}/jobs`]: (request: MockRequest) => ({
      body: page(request.query.get("state") === "active" ? [job()] : history),
    }),
    ...extra,
  };
}

describe("imports", () => {
  it("shows the queue with progress, the sources and the history", async () => {
    mockApi(
      mediaSignedIn(
        routes({}, [
          job({
            id: "job-2",
            status: "failed",
            error_code: "insufficient_space",
            items: [{ path: "films/Nosferatu (1922)", is_dir: true }],
            error_params: { needed: 5e9, free: 1e9, reserve: 10e9 },
            finished_at: "2026-10-16T09:00:00Z",
          }),
        ]),
      ),
    );
    renderApp("/libraries/import");
    expect(await screen.findByRole("heading", { name: "Import media" })).toBeTruthy();
    const queue = (await screen.findByText("Metropolis (1927)")).closest("li");
    expect(queue?.textContent).toContain("into Movies / Classics");
    expect(within(queue as HTMLElement).getByRole("progressbar")).toBeTruthy();
    expect(queue?.textContent).toContain("1 GB of 4 GB");
    expect(await screen.findByText("Wasabi", { selector: "span.font-medium" })).toBeTruthy();
    expect(
      screen.getByText(/Not enough free space: it needs 5 GB, 10 GB must stay free/u),
    ).toBeTruthy();
    expect(screen.getByText("One import at a time")).toBeTruthy();
  });

  it("warns when the importer isn't running", async () => {
    mockApi(
      mediaSignedIn(
        routes({ [`GET ${BASE}/status`]: { body: status({ importer_online: false }) } }),
      ),
    );
    renderApp("/libraries/import");
    expect(await screen.findByText("The importer isn't running")).toBeTruthy();
  });

  it("adds an S3 source and shows the server's refusal on its field", async () => {
    let attempts = 0;
    const api = mockApi(
      mediaSignedIn(
        routes({
          [`GET ${BASE}/sources`]: { body: [] },
          [`POST ${BASE}/sources`]: () => {
            attempts += 1;
            return attempts === 1
              ? problem(400, "VALIDATION_ERROR", { endpoint: ["Not allowed: internal_host."] })
              : { status: 201, body: source({ name: "B2 films" }) };
          },
        }),
      ),
    );
    const { user } = renderApp("/libraries/import");
    await user.click(await screen.findByRole("button", { name: "Add source" }));
    const sheet = await screen.findByRole("dialog", { name: "Add source" });
    await user.type(within(sheet).getByLabelText("Name"), "B2 films");
    await user.click(within(sheet).getByRole("combobox", { name: "Provider" }));
    await user.click(await screen.findByRole("option", { name: "Backblaze B2" }));
    await user.type(within(sheet).getByLabelText("Endpoint"), "http://redis-state:6379");
    await user.type(within(sheet).getByLabelText("Access key ID"), "key-id");
    await user.type(within(sheet).getByLabelText("Secret access key"), "key-secret");
    await user.click(within(sheet).getByRole("button", { name: "Add source" }));
    expect(await within(sheet).findByText("Not allowed: internal_host.")).toBeTruthy();
    const endpoint = within(sheet).getByLabelText("Endpoint");
    await user.clear(endpoint);
    await user.type(endpoint, "https://s3.eu-central-003.backblazeb2.com");
    await user.click(within(sheet).getByRole("button", { name: "Add source" }));
    expect(await screen.findByText("B2 films saved")).toBeTruthy();
    expect(api.sent("POST", `${BASE}/sources`).at(-1)?.body).toEqual({
      name: "B2 films",
      kind: "s3",
      options: {
        provider: "b2",
        endpoint: "https://s3.eu-central-003.backblazeb2.com",
        region: "",
        path_style: false,
        root: "",
      },
      secrets: { access_key_id: "key-id", secret_access_key: "key-secret" },
    });
  });

  it("adds an Xtream VOD source with encrypted fields and warns about HTTP", async () => {
    const api = mockApi(
      mediaSignedIn(
        routes({
          [`GET ${BASE}/sources`]: { body: [] },
          [`POST ${BASE}/sources`]: {
            status: 201,
            body: source({
              name: "Licensed VOD",
              kind: "xtream",
              options: { url: "http://vod.example:8080" },
              summary: "vod.example:8080",
              secrets_set: ["account_username", "password"],
            }),
          },
        }),
      ),
    );
    const { user } = renderApp("/libraries/import");
    await user.click(await screen.findByRole("button", { name: "Add source" }));
    const sheet = await screen.findByRole("dialog", { name: "Add source" });
    await user.click(within(sheet).getByRole("combobox", { name: "Kind" }));
    await user.click(await screen.findByRole("option", { name: "Xtream / IPTV VOD" }));
    await user.type(within(sheet).getByLabelText("Name"), "Licensed VOD");
    await user.type(within(sheet).getByLabelText("IPTV server address"), "http://vod.example:8080");
    expect(await within(sheet).findByText("This provider uses unencrypted HTTP")).toBeTruthy();
    await user.type(within(sheet).getByLabelText("User name"), "viewer");
    await user.type(within(sheet).getByLabelText("Password"), "account-password");
    await user.click(within(sheet).getByRole("button", { name: "Add source" }));
    expect(await screen.findByText("Licensed VOD saved")).toBeTruthy();
    expect(api.sent("POST", `${BASE}/sources`)[0]?.body).toEqual({
      name: "Licensed VOD",
      kind: "xtream",
      options: { url: "http://vod.example:8080" },
      secrets: { account_username: "viewer", password: "account-password" },
    });
  });

  it("browses a source, picks a folder and starts its import under the stated rights", async () => {
    const api = mockApi(
      mediaSignedIn(
        routes({
          [`POST ${BASE}/sources/src-1/browse`]: { status: 202, body: { request_id: "req1" } },
          [`GET ${BASE}/requests/req1`]: {
            body: {
              status: "done",
              result: {
                ok: true,
                code: "ok",
                message: "",
                path: "",
                truncated: false,
                entries: [
                  {
                    name: "Metropolis (1927)",
                    path: "Metropolis (1927)",
                    is_dir: true,
                    size: null,
                    modified: null,
                    importable: true,
                  },
                  {
                    name: "notes.txt",
                    path: "notes.txt",
                    is_dir: false,
                    size: 12,
                    modified: null,
                    importable: false,
                  },
                ],
              },
            },
          },
          [`POST ${BASE}/jobs`]: {
            status: 201,
            body: job({ status: "queued", phase: "planning" }),
          },
        }),
      ),
    );
    const { user } = renderApp("/libraries/import");
    await user.click(await screen.findByRole("button", { name: "Browse" }));
    const dialog = await screen.findByRole("dialog", { name: "Browse Wasabi" });
    const folder = await within(dialog).findByRole("checkbox", {
      name: "Choose Metropolis (1927)",
    });
    expect(
      within(dialog).getByRole<HTMLButtonElement>("checkbox", { name: "Choose notes.txt" })
        .disabled,
    ).toBe(true);
    await user.click(folder);
    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    const form = await screen.findByRole("dialog", { name: "Where and under which rights" });
    await user.click(within(form).getByRole("combobox", { name: "Library" }));
    await user.click(await screen.findByRole("option", { name: "Movies" }));
    expect(within(form).getByText(/500 GB free of 2 TB/u)).toBeTruthy();
    await user.type(within(form).getByLabelText("Rights holder"), "Public domain");
    await user.type(
      within(form).getByLabelText("Licence reference"),
      "https://archive.org/details/metropolis",
    );
    await user.click(within(form).getByRole("button", { name: "Start import" }));
    expect(await screen.findByText("Import queued")).toBeTruthy();
    expect(api.sent("POST", `${BASE}/sources/src-1/browse`)[0]?.body).toEqual({ path: "" });
    expect(api.sent("POST", `${BASE}/jobs`)[0]?.body).toEqual({
      source: "src-1",
      items: [{ path: "Metropolis (1927)", is_dir: true }],
      library: "lib-1",
      subfolder: "",
      rights_holder: "Public domain",
      license_ref: "https://archive.org/details/metropolis",
      license_expires_at: null,
    });
  });

  it("imports one direct link and asks for the rights first", async () => {
    const api = mockApi(
      mediaSignedIn(
        routes({
          [`POST ${BASE}/links`]: {
            status: 201,
            body: job({ id: "job-3", origin: "link", status: "queued" }),
          },
        }),
      ),
    );
    const { user } = renderApp("/libraries/import");
    await user.click(await screen.findByRole("button", { name: "Direct link" }));
    const dialog = await screen.findByRole("dialog", { name: "Import from a direct link" });
    await user.type(
      within(dialog).getByLabelText("Link"),
      "https://archive.org/download/night/Night.mp4",
    );
    await user.click(within(dialog).getByRole("button", { name: "Start import" }));
    expect(await within(dialog).findByText("Say who holds the rights.")).toBeTruthy();
    expect(api.sent("POST", `${BASE}/links`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("combobox", { name: "Library" }));
    await user.click(await screen.findByRole("option", { name: "Movies" }));
    await user.type(within(dialog).getByLabelText("Rights holder"), "Public domain");
    await user.type(within(dialog).getByLabelText("Licence reference"), "PD");
    await user.type(within(dialog).getByLabelText("Licence ends"), "2030-01-31");
    await user.click(within(dialog).getByRole("button", { name: "Start import" }));
    await waitFor(() => {
      expect(api.sent("POST", `${BASE}/links`)).toHaveLength(1);
    });
    expect(api.sent("POST", `${BASE}/links`)[0]?.body).toMatchObject({
      url: "https://archive.org/download/night/Night.mp4",
      library: "lib-1",
      license_expires_at: "2030-01-31T23:59:59Z",
    });
  });

  it("is read-only without library.manage", async () => {
    mockApi(mediaSignedIn(routes(), ["dashboard.view", "library.view"]));
    renderApp("/libraries/import");
    expect(await screen.findByText("Metropolis (1927)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Direct link" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Browse" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("is reached from the Libraries page", async () => {
    mockApi(mediaSignedIn({ ...routes(), "GET /api/v1/admin/scans": { body: page([]) } }));
    const { user } = renderApp("/libraries");
    await user.click(await screen.findByRole("link", { name: "Import" }));
    expect(await screen.findByRole("heading", { name: "Import media" })).toBeTruthy();
  });
});
