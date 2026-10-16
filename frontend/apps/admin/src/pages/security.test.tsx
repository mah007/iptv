import type { GeoIpStatus, RiskOverview, WebAuthnCredential } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { customerDetail, me, page, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* Application security in the admin (M14a, ADR-0021): "Confirm it's you" before sensitive
 * actions, security keys, the anti-sharing panel, revoke by app and the GeoIP card. */

const NOW = new Date().toISOString();
const LOCKED = me({ step_up_until: null });

function key(overrides: Partial<WebAuthnCredential> = {}): WebAuthnCredential {
  return {
    id: "key-1",
    name: "YubiKey 5C",
    created_at: NOW,
    last_used_at: null,
    multi_device: false,
    backed_up: false,
    ...overrides,
  };
}

const RISK: RiskOverview = {
  enabled: true,
  geoip_installed: true,
  flag: {
    id: "flag-1",
    status: "open",
    score: 9,
    assessment: null,
    opened_at: NOW,
    last_scored_at: NOW,
    actions: [],
    reviewed_at: null,
    reviewed_by_name: "",
    note: "",
  },
  assessment: {
    score: 9,
    flagged: true,
    signals: [
      { name: "asns_1h", value: 4, limit: 2, points: 2 },
      { name: "countries_24h", value: 3, limit: 1, points: 2 },
      { name: "impossible_travel", value: 1, limit: 0, points: 3 },
      { name: "excess_bytes_24h", value: 1, limit: 0, points: 4 },
    ],
    travel: [{ from_country: "SA", to_country: "GB", km: 4_900, hours: 1, at: NOW }],
  },
  sightings: [
    {
      id: "s-1",
      network: "81.2.69.0/24",
      asn: 20712,
      asn_org: "Andrews & Arnold",
      country: "GB",
      source: "xtream",
      device: "dev-1",
      device_name: "Living room TV",
      app: "tivimate",
      first_seen: NOW,
      last_seen: NOW,
      hits: 12,
    },
  ],
  excess_bytes: [
    {
      id: "x-1",
      session: "session-1",
      title_name: "The Matrix",
      rendition: "compat",
      network: "81.2.69.0/24",
      device: "dev-1",
      device_name: "Living room TV",
      bytes_sent: 15_000_000_000,
      expected_bytes: 2_700_000_000,
      ratio: 5.56,
      detected_at: NOW,
      started_at: NOW,
      ended_at: null,
    },
  ],
  attribution_url: "https://db-ip.com",
};

const GEOIP: GeoIpStatus = {
  enabled: true,
  auto_update: true,
  databases: {
    country: {
      installed: true,
      built_at: "2026-10-01T00:00:00Z",
      updated_at: NOW,
      size: 7_340_032,
    },
    asn: { installed: true, built_at: "2026-10-01T00:00:00Z", updated_at: NOW, size: 9_437_184 },
  },
  attribution: "https://db-ip.com",
};

const RISK_ROUTES = {
  "GET /api/v1/admin/customers/cust-1": { body: customerDetail() },
  "GET /api/v1/admin/customers/cust-1/risk": { body: RISK },
  "GET /api/v1/admin/access-rules": { body: page([]) },
};
const RESET = "/api/v1/admin/customers/cust-1/risk/reset-credentials";

describe("confirm it's you", () => {
  it("asks for a code when the API wants a fresh confirmation, then retries", async () => {
    let runs = 0;
    const api = mockApi(
      signedIn(
        {
          ...RISK_ROUTES,
          [`POST ${RESET}`]: () => {
            runs += 1;
            return runs === 1 ? problem(403, "STEP_UP_REQUIRED") : { body: { logins: 2 } };
          },
          "POST /api/v1/auth/step-up": {
            body: { step_up_until: new Date(Date.now() + 300_000).toISOString() },
          },
        },
        LOCKED,
      ),
    );
    const { user } = renderApp("/customers/cust-1?tab=security");
    await user.click(await screen.findByRole("button", { name: "Reset app logins" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Reset every TV app login?" });
    await user.click(within(confirm).getByRole("button", { name: "Reset logins" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(within(dialog).queryByRole("button", { name: /security key/ })).toBeNull();
    await user.type(within(dialog).getByLabelText("Code from your authenticator app"), "123456");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect((await screen.findAllByText("2 logins reset.")).length).toBeGreaterThan(0);
    expect(api.sent("POST", RESET)).toHaveLength(2);
    expect(api.sent("POST", "/api/v1/auth/step-up")[0]?.body).toEqual({ code: "123456" });
    expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).toBeNull();
  });

  it("says why nothing happened when the admin cancels", async () => {
    const api = mockApi(
      signedIn({ ...RISK_ROUTES, [`POST ${RESET}`]: problem(403, "STEP_UP_REQUIRED") }, LOCKED),
    );
    const { user } = renderApp("/customers/cust-1?tab=security");
    await user.click(await screen.findByRole("button", { name: "Reset app logins" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Reset every TV app login?" });
    await user.click(within(confirm).getByRole("button", { name: "Reset logins" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Not done: confirm it's you to continue.")).toBeTruthy();
    expect(api.sent("POST", RESET)).toHaveLength(1);
  });

  it("explains a wrong code in Arabic and keeps the dialog open", async () => {
    mockApi(
      signedIn(
        {
          ...RISK_ROUTES,
          [`POST ${RESET}`]: problem(403, "STEP_UP_REQUIRED"),
          "POST /api/v1/auth/step-up": problem(400, "MFA_INVALID"),
        },
        LOCKED,
      ),
    );
    const { user } = renderApp("/customers/cust-1?tab=security", { language: "ar" });
    await user.click(await screen.findByRole("button", { name: "إعادة تعيين تسجيلات الدخول" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "إعادة تعيين كل تسجيلات دخول التطبيقات؟",
    });
    await user.click(within(confirm).getByRole("button", { name: "إعادة التعيين" }));
    const dialog = await screen.findByRole("dialog", { name: "أكّد هويتك" });
    await user.type(within(dialog).getByLabelText("الرمز من تطبيق المصادقة"), "000000");
    await user.click(within(dialog).getByRole("button", { name: "تأكيد" }));
    expect(await within(dialog).findByText("لم ينجح ذلك. راجع الرمز وحاول مجددًا.")).toBeTruthy();
  });
});

describe("sign-in and security", () => {
  it("lists the admin's keys and locks sensitive actions", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/auth/webauthn/credentials": {
          body: [key(), key({ id: "key-2", name: "iPhone", multi_device: true })],
        },
        "DELETE /api/v1/auth/step-up": { status: 204 },
      }),
    );
    const { user } = renderApp("/account/security");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Sign-in and security" }),
    ).toBeTruthy();
    expect(await screen.findByText("YubiKey 5C")).toBeTruthy();
    expect(screen.getByText("Passkey")).toBeTruthy();
    expect(screen.getByText("On")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Lock now" }));
    await waitFor(() => {
      expect(api.sent("DELETE", "/api/v1/auth/step-up")).toHaveLength(1);
    });
  });

  it("keeps an admin who must add a key on the security page", async () => {
    mockApi(
      signedIn(
        { "GET /api/v1/auth/webauthn/credentials": { body: [] } },
        me({ webauthn_enrolment_required: true }),
      ),
    );
    renderApp("/customers");
    expect(await screen.findByText("Add a security key to continue")).toBeTruthy();
    expect(await screen.findByText("No keys yet")).toBeTruthy();
  });
});

describe("sharing detection", () => {
  it("shows the evidence and resets the customer's app logins", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/customers/cust-1": { body: customerDetail() },
        "GET /api/v1/admin/customers/cust-1/risk": { body: RISK },
        "GET /api/v1/admin/access-rules": { body: page([]) },
        "POST /api/v1/admin/customers/cust-1/risk/reset-credentials": { body: { logins: 2 } },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=security");
    const heading = await screen.findByRole("heading", { name: "Account sharing" });
    const panel = heading.closest<HTMLElement>("[data-slot='card']");
    if (panel === null) throw new Error("The sharing panel is not a card.");
    expect(await within(panel).findByText("Flagged")).toBeTruthy();
    expect(within(panel).getByText("Providers in the last hour")).toBeTruthy();
    expect(within(panel).getByText(/Saudi Arabia → United Kingdom/)).toBeTruthy();
    expect(within(panel).getByText("Andrews & Arnold")).toBeTruthy();
    // G-17 (ADR-0024): a session served far more than its title explains.
    expect(within(panel).getByText("Sessions served too many bytes (24 h)")).toBeTruthy();
    const excess = within(panel).getByRole("heading", { name: "Sessions served too many bytes" });
    const table = excess.parentElement?.querySelector("table");
    if (!table) throw new Error("No table of sessions served too many bytes.");
    expect(within(table).getByText("The Matrix")).toBeTruthy();
    expect(within(table).getByText("5.6×")).toBeTruthy();
    expect(within(table).getByText("Playing")).toBeTruthy();
    expect(within(panel).getByRole("link", { name: /DB-IP/ })).toBeTruthy();
    await user.click(within(panel).getByRole("button", { name: "Reset app logins" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Reset every TV app login?" });
    await user.click(within(confirm).getByRole("button", { name: "Reset logins" }));
    expect((await screen.findAllByText("2 logins reset.")).length).toBeGreaterThan(0);
    expect(api.sent("POST", "/api/v1/admin/customers/cust-1/risk/reset-credentials")).toHaveLength(
      1,
    );
  });

  it("switches a customer's two-factor sign-in off for support (ADR-0024)", async () => {
    let enabled = true;
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/customers/cust-1": () => ({
          body: customerDetail({ mfa_enabled: enabled }),
        }),
        "GET /api/v1/admin/customers/cust-1/risk": { body: RISK },
        "GET /api/v1/admin/access-rules": { body: page([]) },
        "POST /api/v1/admin/customers/cust-1/reset-mfa": () => {
          enabled = false;
          return { status: 204 };
        },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=security");
    const heading = await screen.findByRole("heading", { name: /Two-factor sign-in/u });
    const card = heading.closest<HTMLElement>("[data-slot='card']");
    if (card === null) throw new Error("No two-factor card.");
    expect(within(card).getByText("On")).toBeTruthy();
    await user.click(within(card).getByRole("button", { name: "Switch two-factor off" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "Switch off two-factor sign-in for Sara Ahmed?",
    });
    await user.click(within(confirm).getByRole("button", { name: "Switch two-factor off" }));
    expect(
      (await screen.findAllByText("Two-factor sign-in is off for Sara Ahmed.")).length,
    ).toBeGreaterThan(0);
    expect(await within(card).findByText("Off")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/customers/cust-1/reset-mfa")).toHaveLength(1);
  });

  it("filters the customers list to flagged customers", async () => {
    const api = mockApi(signedIn());
    renderApp("/customers?risk=flagged");
    expect(await screen.findByText("Sharing: flagged")).toBeTruthy();
    await waitFor(() => {
      expect(
        api.sent("GET", "/api/v1/admin/customers").some((r) => r.query.get("risk") === "flagged"),
      ).toBe(true);
    });
  });

  it("counts before revoking every login of one app", async () => {
    const api = mockApi(
      signedIn({
        "POST /api/v1/admin/devices/revoke-by-app": (request) => ({
          body: {
            devices: 3,
            dry_run: (request.body as { dry_run?: boolean }).dry_run !== false,
          },
        }),
      }),
    );
    const { user } = renderApp("/customers");
    await user.click(await screen.findByRole("button", { name: "Revoke by app" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke every login of one app" });
    await user.click(within(dialog).getByRole("combobox", { name: "App" }));
    await user.click(await screen.findByRole("option", { name: "TiviMate" }));
    expect(await within(dialog).findByText("3 logins would be revoked.")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Revoke 3 logins" }));
    expect(await screen.findByText("3 logins revoked.")).toBeTruthy();
    const sent = api.sent("POST", "/api/v1/admin/devices/revoke-by-app").map((r) => r.body);
    expect(sent).toEqual([
      { app_hint: "tivimate", dry_run: true },
      { app_hint: "tivimate", dry_run: false, notify: true },
    ]);
  });
});

describe("IP geolocation", () => {
  it("shows the databases, credits DB-IP and looks an address up", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/integrations": { body: [] },
        "GET /api/v1/admin/geoip": { body: GEOIP },
        "POST /api/v1/admin/geoip/lookup": {
          body: {
            ip: "81.2.69.142",
            country: "GB",
            asn: 20712,
            asn_org: "Andrews & Arnold",
            local: false,
          },
        },
      }),
    );
    const { user } = renderApp("/settings/integrations");
    expect(await screen.findByText("IP geolocation (DB-IP Lite)")).toBeTruthy();
    expect(await screen.findByText("Installed")).toBeTruthy();
    expect(screen.getAllByText(/October 2026 release/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: /DB-IP/ })).toBeTruthy();
    await user.type(screen.getByLabelText("Look up an address"), "81.2.69.142");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText(/United Kingdom · Andrews & Arnold/)).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/geoip/lookup")[0]?.body).toEqual({ ip: "81.2.69.142" });
  });
});

describe("about", () => {
  it("credits TMDB and DB-IP", async () => {
    mockApi(signedIn());
    renderApp("/about");
    expect(await screen.findByRole("heading", { level: 1, name: "About" })).toBeTruthy();
    const main = screen.getByRole("main");
    expect(within(main).getByRole("img", { name: /TMDB/ })).toBeTruthy();
    expect(within(main).getByRole("link", { name: /DB-IP/ })).toBeTruthy();
  });
});
