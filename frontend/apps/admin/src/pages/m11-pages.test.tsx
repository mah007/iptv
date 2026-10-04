import type {
  Collection,
  Invoice,
  Outbox,
  Payment,
  Plan,
  Rendition,
  StorageUsage,
  Subscription,
  Template,
  TitleFile,
} from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ALL_PERMISSIONS, me, page, signedIn } from "../test/fixtures";
import { mediaSignedIn, movieDetail } from "../test/media-fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/*
 * The M11 pages: commerce (plans, subscriptions, payments, invoices), engage
 * (notifications, templates), collections, storage and a title's media.
 */

const COMMERCE_PERMISSIONS = [
  ...ALL_PERMISSIONS,
  "plans.view",
  "plans.edit",
  "subscriptions.view",
  "subscriptions.edit",
  "billing.view",
  "billing.manage",
  "billing.refund",
  "notifications.view",
  "notifications.manage",
  "library.view",
  "library.manage",
  "library.review",
];

const CREATED = "2026-09-01T10:00:00Z";
const CUSTOMER = {
  id: "cust-1",
  username: "cus-abc123",
  name: "Sara Ahmed",
  email: "sara@example.com",
  phone: "+966501234567",
};
const PLAN_REF = {
  id: "plan-1",
  code: "monthly",
  name_en: "Monthly",
  name_ar: "شهري",
  is_trial: false,
};

function commerce(routes: Parameters<typeof signedIn>[0] = {}) {
  return signedIn(routes, me({ permissions: COMMERCE_PERMISSIONS }));
}

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    ...PLAN_REF,
    description_en: "One month, two screens.",
    description_ar: "شهر واحد، شاشتان.",
    duration_months: 1,
    duration_days: 0,
    price: 2900,
    currency: "SAR",
    price_total: {
      net: 2522,
      vat: 378,
      total: 2900,
      vat_rate: "0.15",
      display_en: "SAR 29.00",
      display_ar: "29.00 ر.س",
    },
    max_streams: 2,
    max_devices: 3,
    max_quality: 1080,
    allow_movies: true,
    allow_series: true,
    allow_live: false,
    allow_download: false,
    bandwidth_cap_mbps: null,
    concurrency_policy: "reject",
    trial_limit_per_phone: null,
    category_ids: [],
    sort: 0,
    active: true,
    version: 1,
    subscribers: 12,
    created_at: CREATED,
    updated_at: CREATED,
    ...overrides,
  };
}

function subscription(overrides: Partial<Subscription> = {}): Subscription {
  return {
    id: "sub-1",
    user: CUSTOMER,
    plan: PLAN_REF,
    plan_snapshot: {},
    status: "active",
    starts_at: CREATED,
    ends_at: new Date(Date.now() + 20 * 86_400_000).toISOString(),
    grace_days: 3,
    grace_until: null,
    source: "manual",
    auto_renew: false,
    trial_identity: "",
    reminded_7d_at: null,
    reminded_1d_at: null,
    ended_at: null,
    end_reason: "expired",
    note: "",
    created_by: null,
    created_at: CREATED,
    updated_at: CREATED,
    ...overrides,
  };
}

function payment(overrides: Partial<Payment> = {}): Payment {
  return {
    id: "pay-1",
    user: CUSTOMER,
    plan: PLAN_REF,
    subscription_id: "sub-1",
    invoice_id: "inv-1",
    invoice_number: "INV-2026-000042",
    provider: "manual",
    method: "bank_transfer",
    checkout_ref: "",
    provider_ref: "",
    amount: 2900,
    currency: "SAR",
    status: "succeeded",
    refunded_amount: 0,
    reference: "TRX-7781",
    failure_reason: "",
    paid_at: CREATED,
    recorded_by: "admin-1",
    created_at: CREATED,
    ...overrides,
  };
}

function invoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: "inv-1",
    number: "INV-2026-000042",
    status: "paid",
    user: CUSTOMER,
    plan: PLAN_REF,
    subscription_id: "sub-1",
    locale: "en",
    currency: "SAR",
    lines: [],
    subtotal: 2522,
    vat_rate: "0.15",
    vat_amount: 378,
    total: 2900,
    prices_include_vat: true,
    refunded_amount: 0,
    bill_to: {},
    seller: {},
    issued_at: CREATED,
    created_at: CREATED,
    ...overrides,
  };
}

function outbox(overrides: Partial<Outbox> = {}): Outbox {
  return {
    id: "msg-1",
    user: { id: "cust-1", username: "cus-abc123", name: "Sara Ahmed" },
    channel: "email",
    template_key: "subscription_expiring_7d",
    locale: "en",
    status: "sent",
    attempts: 1,
    next_attempt_at: null,
    to_address: "sara@example.com",
    subject: "Your subscription ends in 7 days",
    sent_at: CREATED,
    error: "",
    created_at: CREATED,
    ...overrides,
  } as Outbox;
}

function template(overrides: Partial<Template> = {}): Template {
  return {
    key: "subscription_expiring_7d",
    channel: "email",
    locale: "en",
    description: "Seven days before a subscription ends.",
    variables: ["name", "ends_at"],
    subject: "Your subscription ends in 7 days",
    body_text: "Hello {{ name }}",
    body_html: "<p>Hello {{ name }}</p>",
    enabled: true,
    is_default: true,
    secret: false,
    updated_at: null,
    ...overrides,
  };
}

function collection(overrides: Partial<Collection> = {}): Collection {
  return {
    id: "col-1",
    slug: "ramadan-picks",
    name_en: "Ramadan picks",
    name_ar: "مختارات رمضان",
    description_en: "",
    description_ar: "",
    sort: 0,
    published: true,
    show_on_home: true,
    items: [],
    created_at: CREATED,
    updated_at: CREATED,
    ...overrides,
  };
}

describe("commerce pages", () => {
  it("lists plans with their price and subscribers", async () => {
    mockApi(commerce({ "GET /api/v1/admin/plans": { body: [plan()] } }));
    renderApp("/plans");
    expect(await screen.findByRole("heading", { level: 1, name: "Plans" })).toBeTruthy();
    expect(await screen.findByText("Monthly")).toBeTruthy();
    expect(document.body.textContent).toContain("12");
  });

  it("extends a subscription by the days chosen", async () => {
    const api = mockApi(
      commerce({
        "GET /api/v1/admin/plans": { body: [plan()] },
        "GET /api/v1/admin/subscriptions": { body: page([subscription()]) },
        "POST /api/v1/admin/subscriptions/sub-1/extend": { body: subscription() },
      }),
    );
    const { user } = renderApp("/subscriptions");
    expect(await screen.findByRole("link", { name: "Sara Ahmed" })).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Actions for the subscription of Sara Ahmed" }),
    );
    await user.click(await screen.findByRole("menuitem", { name: "Extend" }));
    const dialog = await screen.findByRole("dialog", { name: "Extend subscription" });
    const days = within(dialog).getByLabelText("Days to add");
    await user.clear(days);
    await user.type(days, "15");
    await user.click(within(dialog).getByRole("button", { name: "Extend" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/subscriptions/sub-1/extend")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/subscriptions/sub-1/extend")[0]?.body).toEqual({
      days: 15,
    });
  });

  it("lists payments with their amount and invoice number", async () => {
    mockApi(commerce({ "GET /api/v1/admin/payments": { body: page([payment()]) } }));
    renderApp("/payments");
    expect(await screen.findByRole("heading", { level: 1, name: "Payments" })).toBeTruthy();
    expect((await screen.findAllByText("Sara Ahmed")).length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("INV-2026-000042");
    expect(document.body.textContent).toMatch(/SAR\s29\.00/u);
  });

  it("lists invoices by number", async () => {
    mockApi(commerce({ "GET /api/v1/admin/invoices": { body: page([invoice()]) } }));
    renderApp("/invoices");
    expect(await screen.findByRole("heading", { level: 1, name: "Invoices" })).toBeTruthy();
    expect((await screen.findAllByText("INV-2026-000042")).length).toBeGreaterThan(0);
  });
});

describe("engage pages", () => {
  it("lists sent messages with their template and recipient", async () => {
    mockApi(commerce({ "GET /api/v1/admin/notifications": { body: page([outbox()]) } }));
    renderApp("/notifications");
    expect(await screen.findByRole("heading", { level: 1, name: "Notifications" })).toBeTruthy();
    expect((await screen.findAllByText(/sara@example.com|Sara Ahmed/u)).length).toBeGreaterThan(0);
  });

  it("lists the templates to edit", async () => {
    mockApi(
      commerce({
        "GET /api/v1/admin/templates": {
          body: [template(), template({ locale: "ar", subject: "ينتهي اشتراكك خلال 7 أيام" })],
        },
      }),
    );
    renderApp("/templates");
    expect(await screen.findByRole("heading", { level: 1, name: "Templates" })).toBeTruthy();
    expect(await screen.findByText("Seven days before a subscription ends.")).toBeTruthy();
  });

  it("lists collections", async () => {
    mockApi(commerce({ "GET /api/v1/admin/collections": { body: page([collection()]) } }));
    renderApp("/collections");
    expect(await screen.findByRole("heading", { level: 1, name: "Collections" })).toBeTruthy();
    expect(await screen.findByText("Ramadan picks")).toBeTruthy();
  });
});

const USAGE: StorageUsage = {
  as_of: new Date().toISOString(),
  time_zone: "Asia/Riyadh",
  files: 3,
  sources: 3_000_000_000,
  renditions: 1_500_000_000,
  libraries: [
    {
      id: "lib-1",
      name: "Movies",
      kind: "movies",
      files: 3,
      sources: 3_000_000_000,
      renditions: 1_500_000_000,
    },
  ],
  growth: Array.from({ length: 90 }, (_, index) => ({
    date: new Date(Date.UTC(2026, 6, 7 + index)).toISOString().slice(0, 10),
    sources: 2_000_000_000 + index * 10_000_000,
    renditions: 1_000_000_000,
  })),
  largest: [
    {
      kind: "movie",
      id: "movie-1",
      title: "The Matrix",
      title_ar: "المصفوفة",
      year: 1999,
      files: 1,
      sources: 2_000_000_000,
      renditions: 900_000_000,
    },
  ],
};

describe("storage", () => {
  it("shows usage by library and the largest titles, and starts a dry run first", async () => {
    const api = mockApi(
      mediaSignedIn({
        "GET /api/v1/admin/storage": { body: USAGE },
        "GET /api/v1/admin/renditions/cleanup": problem(404, "NOT_FOUND"),
        "POST /api/v1/admin/renditions/cleanup": { status: 202, body: { queued: true } },
      }),
    );
    const { user } = renderApp("/storage");
    expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeTruthy();
    expect(await screen.findByText("4.5 GB")).toBeTruthy();
    expect(screen.getByRole("link", { name: "The Matrix" }).getAttribute("href")).toBe(
      "/movies/movie-1",
    );
    expect(await screen.findByText("No cleanup has run yet. Start with a dry run.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete files" }).hasAttribute("disabled")).toBe(
      true,
    );
    await user.click(screen.getByRole("button", { name: "Dry run" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/renditions/cleanup")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/renditions/cleanup")[0]?.body).toEqual({
      dry_run: true,
    });
  });

  it("deletes what a dry run found after confirmation", async () => {
    const api = mockApi(
      mediaSignedIn({
        "GET /api/v1/admin/storage": { body: USAGE },
        "GET /api/v1/admin/renditions/cleanup": {
          body: {
            dry_run: true,
            finished_at: new Date().toISOString(),
            bytes: 2_000_000,
            entries: 1,
            removals: [{ path: "a1b2c3/hls", reason: "orphaned", bytes: 2_000_000 }],
          },
        },
        "POST /api/v1/admin/renditions/cleanup": { status: 202, body: { queued: true } },
      }),
    );
    const { user } = renderApp("/storage");
    expect(await screen.findByText("1 entry would free 2 MB")).toBeTruthy();
    expect(screen.getByText("Orphaned")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Delete files" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Delete these renditions?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete files" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/renditions/cleanup")[0]?.body).toEqual({
        dry_run: false,
      });
    });
  });
});

function rendition(overrides: Partial<Rendition> = {}): Rendition {
  return {
    id: "ren-1",
    kind: "compat_mp4",
    name: "compat",
    status: "ready",
    width: 1920,
    height: 1080,
    bitrate: 4_000_000,
    codec: "h264",
    container: "mp4",
    size: 900_000_000,
    disk_bytes: 900_000_000,
    encoder_used: "h264_nvenc",
    duration_s: 8160,
    error: "",
    details: {},
    ready_at: CREATED,
    created_at: CREATED,
    ...overrides,
  };
}

function titleFile(overrides: Partial<TitleFile> = {}): TitleFile {
  return {
    id: "file-1",
    relative_path: "The Matrix (1999)/The.Matrix.1999.1080p.BluRay.x265.mkv",
    library: "lib-1",
    size: 2_000_000_000,
    duration_ms: 8_160_000,
    video_codec: "hevc",
    width: 1920,
    height: 1080,
    hdr: "sdr",
    is_primary: true,
    disk_bytes: 900_000_000,
    renditions: [rendition()],
    audio: [],
    subtitles: [],
    jobs: [],
    ...overrides,
  };
}

describe("a title's media", () => {
  it("lists renditions with their encoder and reprocesses the chosen outputs", async () => {
    const api = mockApi(
      mediaSignedIn({
        "GET /api/v1/admin/movies/movie-1": { body: movieDetail() },
        "GET /api/v1/admin/titles/movie-1/renditions": {
          body: {
            title: { kind: "movie", id: "movie-1", title: "The Matrix" },
            files: [titleFile()],
          },
        },
        "GET /api/v1/admin/titles/movie-1/images": {
          body: {
            title: { kind: "movie", id: "movie-1", title: "The Matrix" },
            images: [],
            alternatives: [],
            alternatives_error: null,
          },
        },
        "POST /api/v1/admin/titles/movie-1/reprocess": {
          status: 202,
          body: { files: ["file-1"], outputs: ["hls"] },
        },
      }),
    );
    const { user } = renderApp("/movies/movie-1");
    const table = await screen.findByRole("table", { name: "Renditions" });
    const row = within(table).getByText("compat").closest("tr");
    expect(row?.textContent).toContain("h264_nvenc");
    expect(row?.textContent).toContain("900 MB");
    await user.click(screen.getByRole("button", { name: "Reprocess" }));
    const dialog = await screen.findByRole("dialog", { name: "Reprocess this title" });
    await user.click(within(dialog).getByRole("checkbox", { name: "HLS ladder" }));
    await user.click(within(dialog).getByRole("button", { name: "Queue jobs" }));
    expect(await screen.findByText("Reprocessing 1 file")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/titles/movie-1/reprocess")[0]?.body).toEqual({
      outputs: ["hls"],
    });
  });
});
