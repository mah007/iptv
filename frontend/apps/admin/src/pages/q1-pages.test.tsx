import type { DeviceListItem, Lockout } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ALL_PERMISSIONS, me, page, setting, signedIn } from "../test/fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const OWNER = me({ permissions: [...ALL_PERMISSIONS, "lockouts.view", "lockouts.manage"] });
const DEVICES = "/api/v1/admin/devices";
const LOCKOUTS = "/api/v1/admin/lockouts";

function deviceItem(overrides: Partial<DeviceListItem> = {}): DeviceListItem {
  return {
    id: "dev-1",
    kind: "xtream",
    name: "Living room TV",
    app_hint: "tivimate",
    status: "active",
    approved: true,
    blocked: false,
    blocked_reason: "",
    revoked_at: null,
    xtream_username: "sar-k3p9qa",
    first_seen: null,
    last_seen: "2026-10-15T20:00:00Z",
    last_ip: "203.0.113.7",
    last_country: "SA",
    created_at: "2026-10-01T10:00:00Z",
    customer: { id: "cust-1", username: "cus-abc123", name: "Sara Ahmed" },
    credential_last_used_at: null,
    ...overrides,
  };
}

function lockout(overrides: Partial<Lockout> = {}): Lockout {
  return {
    id: "sign_in:7",
    kind: "sign_in",
    failures: 6,
    until: new Date(Date.now() + 10 * 60_000).toISOString(),
    login: "sara@example.com",
    ip: "203.0.113.7",
    network: "",
    account: { id: "cust-1", username: "cus-abc123", name: "Sara Ahmed", is_staff: false },
    ...overrides,
  };
}

describe("devices page", () => {
  it("lists every customer's devices with filters in the URL", async () => {
    const api = mockApi(signedIn({ [`GET ${DEVICES}`]: { body: page([deviceItem()]) } }, OWNER));
    const { user } = renderApp("/devices?status=active&app=tivimate");
    const table = await screen.findByRole("table", { name: "Devices" });
    expect(await within(table).findByText("Living room TV")).toBeTruthy();
    expect(within(table).getByRole("link", { name: "Sara Ahmed" })).toBeTruthy();
    expect(within(table).getByText("sar-k3p9qa")).toBeTruthy();
    const query = api.sent("GET", DEVICES).at(-1)?.query;
    expect(query?.get("status")).toBe("active");
    expect(query?.get("app_hint")).toBe("tivimate");
    expect(screen.getByText("App: TiviMate")).toBeTruthy();
    await user.type(screen.getByRole("searchbox"), "sar");
    await waitFor(() => {
      expect(api.sent("GET", DEVICES).at(-1)?.query.get("search")).toBe("sar");
    });
  });

  it("blocks selected devices together, with a reason", async () => {
    const api = mockApi(
      signedIn(
        {
          [`GET ${DEVICES}`]: {
            body: page([deviceItem(), deviceItem({ id: "dev-2", name: "Phone" })]),
          },
          [`POST ${DEVICES}/bulk`]: { body: { changed: 2, skipped: 0 } },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/devices");
    const table = await screen.findByRole("table", { name: "Devices" });
    const [selectAll] = within(table).getAllByRole("checkbox");
    if (!selectAll) throw new Error("no selection checkbox");
    await user.click(selectAll);
    await user.click(screen.getByRole("button", { name: "Block" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.type(within(dialog).getByLabelText("Reason (optional)"), "shared");
    await user.click(within(dialog).getByRole("button", { name: "Block" }));
    await waitFor(() => {
      expect(api.sent("POST", `${DEVICES}/bulk`)[0]?.body).toEqual({
        ids: ["dev-1", "dev-2"],
        action: "block",
        reason: "shared",
      });
    });
    expect(await screen.findByText("2 devices blocked (0 skipped)")).toBeTruthy();
  });

  it("offers no bulk actions without devices.manage", async () => {
    mockApi(
      signedIn(
        { [`GET ${DEVICES}`]: { body: page([deviceItem()]) } },
        me({ permissions: ["customers.view"] }),
      ),
    );
    renderApp("/devices");
    const table = await screen.findByRole("table", { name: "Devices" });
    expect(within(table).queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Revoke by app" })).toBeNull();
  });
});

describe("lockouts page", () => {
  it("lists lockouts and unlocks one", async () => {
    const api = mockApi(
      signedIn(
        {
          [`GET ${LOCKOUTS}`]: {
            body: [
              lockout(),
              lockout({
                id: "xtream_address:198.51.100.4",
                kind: "xtream_address",
                login: "",
                ip: "198.51.100.4",
                account: null,
              }),
            ],
          },
          [`POST ${LOCKOUTS}/unlock`]: { status: 204 },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/lockouts");
    const table = await screen.findByRole("table", { name: "Lockouts" });
    expect(within(table).getByText("Sign-in")).toBeTruthy();
    expect(within(table).getByText("TV address")).toBeTruthy();
    expect(within(table).getByText("sara@example.com")).toBeTruthy();
    const [first] = within(table).getAllByRole("button", { name: "Unlock" });
    if (!first) throw new Error("no unlock button");
    await user.click(first);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Unlock" }));
    await waitFor(() => {
      expect(api.sent("POST", `${LOCKOUTS}/unlock`)[0]?.body).toEqual({ id: "sign_in:7" });
    });
    expect(await screen.findByText("Unlocked")).toBeTruthy();
  });

  it("keeps an admin's lockout to those who manage admins", async () => {
    mockApi(
      signedIn(
        {
          [`GET ${LOCKOUTS}`]: {
            body: [
              lockout({
                account: { id: "adm-1", username: "ops", name: "Ops", is_staff: true },
              }),
            ],
          },
        },
        me({ permissions: ["lockouts.view", "lockouts.manage"] }),
      ),
    );
    renderApp("/lockouts");
    const table = await screen.findByRole("table", { name: "Lockouts" });
    expect(within(table).getByText("Admin")).toBeTruthy();
    expect(within(table).queryByRole("button", { name: "Unlock" })).toBeNull();
  });

  it("says when no one is locked out", async () => {
    mockApi(signedIn({ [`GET ${LOCKOUTS}`]: { body: [] } }, OWNER));
    renderApp("/lockouts");
    expect(await screen.findByText("No one is locked out")).toBeTruthy();
  });
});

describe("my password", () => {
  it("changes it and shows the API's errors in the admin's language", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/auth/webauthn/credentials": { body: [] },
        "POST /api/v1/auth/password": (request) =>
          (request.body as { current_password: string }).current_password === "right-one"
            ? { status: 204 }
            : {
                status: 400,
                body: {
                  type: "about:blank",
                  title: "Invalid input",
                  status: 400,
                  code: "VALIDATION_ERROR",
                  detail: "The current password is not right.",
                  field_errors: { current_password: ["The current password is not right."] },
                  field_error_codes: { current_password: ["wrong_password"] },
                },
              },
      }),
    );
    const { user } = renderApp("/account/security");
    await user.type(await screen.findByLabelText("Current password"), "wrong-one");
    await user.type(screen.getByLabelText("New password"), "a-new-long-password");
    await user.type(screen.getByLabelText("New password again"), "a-new-long-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("The current password is not right.")).toBeTruthy();
    await user.clear(screen.getByLabelText("Current password"));
    await user.type(screen.getByLabelText("Current password"), "right-one");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(
      await screen.findByText("Password changed. Your other sessions have ended."),
    ).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/password").at(-1)?.body).toEqual({
      current_password: "right-one",
      new_password: "a-new-long-password",
    });
  });

  it("refuses two different new passwords", async () => {
    mockApi(signedIn({ "GET /api/v1/auth/webauthn/credentials": { body: [] } }));
    const { user } = renderApp("/account/security");
    await user.type(await screen.findByLabelText("Current password"), "x");
    await user.type(screen.getByLabelText("New password"), "one-long-password");
    await user.type(screen.getByLabelText("New password again"), "another-password");
    expect(screen.getByText("The two new passwords are different.")).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "Change password" }).disabled,
    ).toBe(true);
  });
});

describe("legal texts in Settings (ADR-0023)", () => {
  const TEMPLATE = "# Privacy policy\n\nWe keep **little**.";
  const PRIVACY = setting({
    key: "legal.privacy_en",
    group: "legal",
    kind: "str",
    widget: "text",
    default: TEMPLATE,
    value: TEMPLATE,
  });

  it("warns while a text is still the template and previews the Markdown", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/settings": { body: [PRIVACY] } }, OWNER));
    const { user } = renderApp("/settings");
    const row = (await screen.findByText("Privacy policy (English)")).closest("[data-setting]");
    if (!(row instanceof HTMLElement)) throw new Error("no row");
    expect(
      within(row).getByText("Template text: review it with a lawyer before go-live."),
    ).toBeTruthy();
    await user.click(within(row).getByText("Preview"));
    expect(within(row).getByRole("heading", { level: 1, name: "Privacy policy" })).toBeTruthy();
    expect(within(row).getByText("little").tagName).toBe("STRONG");
  });

  it("drops the warning once the text is the admin's own", async () => {
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/settings": {
            body: [{ ...PRIVACY, value: "# Ours", source: "database", is_default: false }],
          },
        },
        OWNER,
      ),
    );
    renderApp("/settings");
    const row = (await screen.findByText("Privacy policy (English)")).closest("[data-setting]");
    if (!(row instanceof HTMLElement)) throw new Error("no row");
    expect(within(row).queryByText(/Template text/u)).toBeNull();
  });
});
