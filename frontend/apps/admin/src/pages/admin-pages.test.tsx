import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PERMISSIONS, auditEntry, me, page, role, setting, signedIn } from "../test/fixtures";
import { mockApi, problem, type MockRequest } from "../test/mock-api";
import { renderApp } from "../test/render-app";

describe("settings", () => {
  const SETTINGS = [
    setting({ key: "features.approve_new_devices" }),
    setting({
      key: "trials.limit_per_phone",
      kind: "int",
      default: 1,
      value: 1,
      min_value: 0,
      max_value: 10,
    }),
    setting({
      key: "branding.service_name_en",
      kind: "str",
      default: "Smart IPTV",
      value: "Smart IPTV",
    }),
  ];

  it("groups the registry and saves a switch at once", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/settings": { body: SETTINGS },
        "PATCH /api/v1/admin/settings/features.approve_new_devices": {
          body: setting({ key: "features.approve_new_devices", value: true, is_default: false }),
        },
      }),
    );
    const { user } = renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Feature flags" })).toBeTruthy();
    // Branding comes first, as in SPEC §8.3.18.
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(headings.indexOf("Branding")).toBeLessThan(headings.indexOf("Feature flags"));

    await user.click(screen.getByRole("switch", { name: "Approve new devices" }));
    expect(await screen.findByText("Approve new devices saved")).toBeTruthy();
    expect(
      api.sent("PATCH", "/api/v1/admin/settings/features.approve_new_devices")[0]?.body,
    ).toEqual({ value: true });
  });

  it("checks a number's range before saving, and shows the API's validation message", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/settings": { body: SETTINGS },
        "PATCH /api/v1/admin/settings/branding.service_name_en": problem(400, "VALIDATION_ERROR", {
          value: ["Use at most 60 characters."],
        }),
      }),
    );
    const { user } = renderApp("/settings");
    const input = await screen.findByLabelText("Trials per phone number");
    await user.clear(input);
    await user.type(input, "12");
    expect(screen.getByText("Enter a value from 0 to 10.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    await user.clear(input);
    await user.type(input, "1");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    const name = screen.getByLabelText("Service name (English)");
    await user.type(name, " TV");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Use at most 60 characters.")).toBeTruthy();
    expect(api.sent("PATCH", "/api/v1/admin/settings/branding.service_name_en")[0]?.body).toEqual({
      value: "Smart IPTV TV",
    });
  });

  it("is read-only without the settings.edit permission", async () => {
    mockApi(
      signedIn(
        { "GET /api/v1/admin/settings": { body: SETTINGS } },
        me({ permissions: ["settings.view"] }),
      ),
    );
    renderApp("/settings");
    expect(await screen.findByRole("switch", { name: "Approve new devices" })).toHaveProperty(
      "disabled",
      true,
    );
  });
});

describe("audit log", () => {
  it("lists entries and opens one with its before/after diff", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/audit": {
          body: page([
            auditEntry(),
            auditEntry({ id: "audit-2", actor: null, action: "x.custom" }),
          ]),
        },
      }),
    );
    const { user } = renderApp("/audit?action=customer.suspend&from=2026-10-01");
    const table = await screen.findByRole("table", { name: "Audit log" });
    expect(await within(table).findByText("Customer suspended")).toBeTruthy();
    expect(within(table).getByText("System")).toBeTruthy();
    expect(within(table).getByText("x.custom")).toBeTruthy();
    const query = api.sent("GET", "/api/v1/admin/audit").at(-1)?.query;
    expect(query?.get("action")).toBe("customer.suspend");
    // The start of 1 October in Riyadh.
    expect(query?.get("at_after")).toBe("2026-09-30T21:00:00.000Z");

    await user.click(within(table).getByText("Customer suspended"));
    const drawer = await screen.findByRole("dialog", { name: "Customer suspended" });
    expect(within(drawer).getByText("Changed: 1")).toBeTruthy();
    expect(within(drawer).getByText("suspended")).toBeTruthy();
  });
});

describe("admins and roles", () => {
  const ROLES = [
    role({ id: "r-support", name: "support", permissions: ["customers.view"] }),
    role({ id: "r-owner", name: "owner", permissions: [], admin_count: 1 }),
  ];

  it("lists admins with their roles and two-factor status", async () => {
    mockApi(
      signedIn({
        "GET /api/v1/admin/admins": {
          body: page([
            {
              id: "admin-1",
              username: "admin",
              name: "Owner",
              email: "owner@example.com",
              status: "active",
              roles: [{ id: "r-owner", name: "owner" }],
              mfa_enabled: true,
              last_login: null,
              last_login_ip: null,
              created_at: "2026-09-01T10:00:00Z",
            },
          ]),
        },
      }),
    );
    renderApp("/admins");
    const table = await screen.findByRole("table", { name: "Admins" });
    const row = (await within(table).findByText("You")).closest("tr");
    expect(row?.textContent).toContain("Owner");
    expect(row?.textContent).toContain("On");
  });

  it("edits a role's permissions in the matrix and saves only that role", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/roles": { body: ROLES },
        "GET /api/v1/admin/permissions": { body: PERMISSIONS },
        "PATCH /api/v1/admin/roles/r-support": (request: MockRequest) => ({
          body: { ...ROLES[0], ...(request.body as object) },
        }),
      }),
    );
    const { user } = renderApp("/admins?tab=roles");
    const matrix = await screen.findByRole("table", { name: "Permissions of each role" });
    // The owner column comes first and can't be changed.
    const owner = within(matrix).getByRole("checkbox", {
      name: "Owner: Create and change customers and their access profiles",
    });
    expect(owner.getAttribute("data-state")).toBe("checked");
    expect(owner.hasAttribute("disabled")).toBe(true);

    await user.click(
      within(matrix).getByRole("checkbox", {
        name: "Support: Create and change customers and their access profiles",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes to 1 role" }));
    await waitFor(() => {
      expect(api.sent("PATCH", "/api/v1/admin/roles/r-support")[0]?.body).toEqual({
        permissions: ["customers.view", "customers.edit"],
      });
    });
    expect(await screen.findByText("Roles saved")).toBeTruthy();
  });
});
