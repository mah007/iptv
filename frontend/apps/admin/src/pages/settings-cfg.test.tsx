import type { Integration, SettingEntry, SystemInfo } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { me, setting, signedIn } from "../test/fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* Dynamic configuration (ADR-0019): secrets are write-only with their source shown,
 * the Integrations page saves then tests, and the System page shows the installation. */

const PASSWORD = setting({
  key: "email.smtp_password",
  group: "email",
  kind: "str",
  sensitive: true,
  default: null,
  value: null,
  source: "environment",
  is_set: true,
  env: ["EMAIL_HOST_PASSWORD"],
});
const HOST = setting({
  key: "email.smtp_host",
  group: "email",
  kind: "str",
  default: "",
  value: "smtp.env.example.com",
  source: "environment",
  env: ["EMAIL_HOST"],
});
const PORT = setting({
  key: "email.smtp_port",
  group: "email",
  kind: "int",
  default: 587,
  value: 2525,
  source: "database",
  env: ["EMAIL_PORT"],
  min_value: 1,
  max_value: 65535,
  updated_by: "owner",
  updated_at: "2026-10-11T10:00:00Z",
});

function stored(entry: SettingEntry, overrides: Partial<SettingEntry> = {}): SettingEntry {
  return { ...entry, source: "database", is_default: false, ...overrides };
}

describe("secret settings", () => {
  it("never shows a secret, says where it comes from, and moves it in", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/settings": { body: [HOST, PORT, PASSWORD] },
        "POST /api/v1/admin/settings/email.smtp_password/adopt": { body: stored(PASSWORD) },
      }),
    );
    const { user } = renderApp("/settings");
    const row = (await screen.findByText("SMTP password")).closest("[data-setting]");
    if (!(row instanceof HTMLElement)) throw new Error("no row");
    expect(within(row).getByText("From server config")).toBeTruthy();
    expect(within(row).getByText(/EMAIL_HOST_PASSWORD/u)).toBeTruthy();
    expect(within(row).queryByDisplayValue(/./u)).toBeNull();
    await user.click(within(row).getByRole("button", { name: "Move into settings" }));
    expect(await screen.findByText("SMTP password: moved into the settings")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/settings/email.smtp_password/adopt")).toHaveLength(1);
    expect(await within(row).findByText("Set")).toBeTruthy();
  });

  it("replaces a stored secret with an empty input, and clears it", async () => {
    const secret = stored(PASSWORD);
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/settings": { body: [secret] },
        "PATCH /api/v1/admin/settings/email.smtp_password": { body: secret },
        "DELETE /api/v1/admin/settings/email.smtp_password": { body: PASSWORD },
      }),
    );
    const { user } = renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Replace" }));
    const input = screen.getByLabelText("SMTP password");
    expect((input as HTMLInputElement).value).toBe("");
    await user.type(input, "n3w-Pa55");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("SMTP password saved")).toBeTruthy();
    expect(api.sent("PATCH", "/api/v1/admin/settings/email.smtp_password")[0]?.body).toEqual({
      value: "n3w-Pa55",
    });

    await user.click(screen.getByRole("button", { name: "Clear" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/EMAIL_HOST_PASSWORD/u)).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Clear" }));
    await waitFor(() => {
      expect(api.sent("DELETE", "/api/v1/admin/settings/email.smtp_password")).toHaveLength(1);
    });
  });

  it("marks server-config values and resets stored ones back to it", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/settings": { body: [HOST, PORT] },
        "DELETE /api/v1/admin/settings/email.smtp_port": {
          body: { ...PORT, source: "environment", value: 587, is_default: true },
        },
      }),
    );
    const { user } = renderApp("/settings");
    const host = (await screen.findByText("SMTP server")).closest("[data-setting]");
    if (!(host instanceof HTMLElement)) throw new Error("no row");
    expect(within(host).getByText("Server config")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back to the server config" }));
    await waitFor(() => {
      expect(api.sent("DELETE", "/api/v1/admin/settings/email.smtp_port")).toHaveLength(1);
    });
  });
});

function card(name: Integration["name"], overrides: Partial<Integration> = {}): Integration {
  return {
    name,
    status: "not_set",
    enabled: null,
    webhook_url: null,
    settings: [],
    ...overrides,
  };
}

const TELEGRAM = card("telegram", {
  status: "partial",
  settings: [
    setting({
      key: "notifications.telegram_bot_token",
      group: "notifications",
      kind: "str",
      sensitive: true,
      default: null,
      value: null,
      is_set: false,
    }),
    setting({
      key: "notifications.telegram_chat_id",
      group: "notifications",
      kind: "str",
      default: "",
      value: "",
      is_set: false,
    }),
  ],
});
const EMAIL = card("email", { status: "server_config", settings: [HOST, PORT, PASSWORD] });
const STRIPE = card("stripe", {
  webhook_url: "https://api.tv.example.com/api/v1/webhooks/stripe",
  enabled: false,
});

describe("integrations", () => {
  it("shows each card's status and saves its settings in one request", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/integrations": { body: [EMAIL, TELEGRAM, STRIPE] },
        "PATCH /api/v1/admin/settings": { body: [] },
      }),
    );
    const { user } = renderApp("/settings/integrations");
    expect(await screen.findByRole("heading", { level: 1, name: "Integrations" })).toBeTruthy();
    const telegram = (await screen.findByText("Telegram")).closest("[data-integration]");
    if (!(telegram instanceof HTMLElement)) throw new Error("no card");
    expect(within(telegram).getByText("Incomplete")).toBeTruthy();
    expect(
      screen.getByDisplayValue("https://api.tv.example.com/api/v1/webhooks/stripe"),
    ).toBeTruthy();

    const test = within(telegram).getByRole("button", { name: "Send a test message" });
    await user.click(within(telegram).getByRole("button", { name: "Set" }));
    await user.type(within(telegram).getByLabelText("Telegram bot token"), "123456789:AAA");
    await user.type(within(telegram).getByLabelText("Admins' Telegram chat"), "-100123");
    expect(test.hasAttribute("disabled")).toBe(true);
    expect(within(telegram).getByText(/Save the changes before testing/u)).toBeTruthy();
    await user.click(within(telegram).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Telegram: saved")).toBeTruthy();
    expect(api.sent("PATCH", "/api/v1/admin/settings")[0]?.body).toEqual({
      values: {
        "notifications.telegram_bot_token": "123456789:AAA",
        "notifications.telegram_chat_id": "-100123",
      },
    });
  });

  it("runs a test and explains the result", async () => {
    mockApi(
      signedIn({
        "GET /api/v1/admin/integrations": { body: [EMAIL] },
        "POST /api/v1/admin/integrations/email/test": {
          body: {
            ok: true,
            code: "ok",
            message: "Sent to owner@example.com.",
            details: { reference: "ab12cd34", recipient: "owner@example.com" },
          },
        },
      }),
    );
    const { user } = renderApp("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Send a test email to me" }));
    expect(await screen.findByText("Test email sent")).toBeTruthy();
    expect(screen.getByText("Sent to owner@example.com")).toBeTruthy();
    expect(screen.getByText("Reference ab12cd34 (in the subject)")).toBeTruthy();
  });

  it("explains a failed test in Arabic", async () => {
    mockApi(
      signedIn({
        "GET /api/v1/admin/integrations": { body: [EMAIL] },
        "POST /api/v1/admin/integrations/email/test": {
          body: {
            ok: false,
            code: "auth_failed",
            message: "The SMTP server refused the user name or password.",
            details: {},
          },
        },
      }),
    );
    const { user } = renderApp("/settings/integrations", { language: "ar" });
    await user.click(await screen.findByRole("button", { name: "أرسل لي رسالة تجريبية" }));
    expect(await screen.findByText("رُفض المفتاح أو كلمة المرور")).toBeTruthy();
  });

  it("is read-only without settings.edit", async () => {
    mockApi(
      signedIn(
        { "GET /api/v1/admin/integrations": { body: [EMAIL, TELEGRAM] } },
        me({ permissions: ["settings.view"] }),
      ),
    );
    renderApp("/settings/integrations");
    expect(await screen.findByText("Telegram")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send a test message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Set" })).toBeNull();
  });
});

const SYSTEM: SystemInfo = {
  environment: "production",
  version: "0.1.0",
  commit: "abc1234",
  domain: "tv.example.com",
  public_scheme: "https",
  hosts: [
    { role: "tv", host: "tv.example.com", url: "https://tv.example.com" },
    { role: "admin", host: "admin.tv.example.com", url: "https://admin.tv.example.com" },
  ],
  media_root: "/srv/media",
  library_root: "/media",
  data_root: "/data",
  monitoring_enabled: true,
  live_ingest_enabled: false,
  server_time: "2026-10-11T12:00:00Z",
  time_zone: "Asia/Riyadh",
  from_server_config: [{ key: "email.smtp_host", env: ["EMAIL_HOST"] }],
  secrets: [
    { name: "FIELD_ENCRYPTION_KEY", reason: "encryption", location: "environment", required: true },
    { name: "MEDIA_TOKEN_KEYS", reason: "edge", location: "file", required: true },
    { name: "MEDIAMTX_READ_PASS", reason: "ingest", location: "missing", required: false },
  ],
  key_rotations: [],
};

describe("system", () => {
  it("shows the installation, what stays in .env and why", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/system": { body: SYSTEM } }));
    renderApp("/settings/system");
    expect(await screen.findByText("abc1234")).toBeTruthy();
    expect(screen.getByText("https://admin.tv.example.com")).toBeTruthy();
    expect(screen.getByText("/srv/media")).toBeTruthy();
    expect(screen.getByText("SMTP server")).toBeTruthy();
    expect(
      screen.getByText("Decrypts the secrets stored in the settings, so it can't be one of them."),
    ).toBeTruthy();
    expect(screen.getByText("File (secrets/)")).toBeTruthy();
    expect(screen.getByText("Not used")).toBeTruthy();
    expect(screen.getByText(/No rotation recorded yet/u)).toBeTruthy();
  });
});
