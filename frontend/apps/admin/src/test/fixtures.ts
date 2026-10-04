import type {
  AuditLog,
  CustomerDetail,
  CustomerSummary,
  Device,
  IssuedCredential,
  Kpis,
  Me,
  Permission,
  Role,
  SettingEntry,
} from "@smart-iptv/api";

import type { Routes } from "./mock-api";

export const ALL_PERMISSIONS = [
  "dashboard.view",
  "customers.view",
  "customers.edit",
  "devices.manage",
  "settings.view",
  "settings.edit",
  "audit.view",
  "roles.manage",
  "admins.manage",
];

export function me(overrides: Partial<Me> = {}): Me {
  return {
    id: "admin-1",
    username: "admin",
    name: "Owner",
    email: "owner@example.com",
    locale: "en",
    timezone: "Asia/Riyadh",
    mfa_enabled: true,
    roles: ["owner"],
    permissions: ALL_PERMISSIONS,
    ...overrides,
  };
}

/** A DRF page of results. */
export function page<T>(results: T[], count = results.length) {
  return { count, next: null, previous: null, results };
}

const IN_20_DAYS = new Date(Date.now() + 20 * 86_400_000).toISOString();

export function customerSummary(overrides: Partial<CustomerSummary> = {}): CustomerSummary {
  return {
    id: "cust-1",
    username: "cus-abc123",
    name: "Sara Ahmed",
    email: "sara@example.com",
    phone: "+966501234567",
    status: "active",
    access_status: "active",
    expires_at: IN_20_DAYS,
    max_devices: 2,
    device_count: 1,
    last_seen: null,
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

export function device(overrides: Partial<Device> = {}): Device {
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
    xtream_username: "sar-q7k2pa",
    first_seen: null,
    last_seen: null,
    last_ip: null,
    last_country: "",
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

export function customerDetail(overrides: Partial<CustomerDetail> = {}): CustomerDetail {
  return {
    id: "cust-1",
    username: "cus-abc123",
    name: "Sara Ahmed",
    email: "sara@example.com",
    phone: "+966501234567",
    status: "active",
    locale: "ar",
    timezone: "Asia/Riyadh",
    notes: "",
    marketing_opt_in: false,
    access: {
      status: "active",
      expires_at: IN_20_DAYS,
      max_streams: 1,
      max_devices: 2,
      max_quality: 1080,
      concurrency_policy: "reject",
      allow_movies: true,
      allow_series: true,
      allow_live: true,
      categories: [],
      updated_at: "2026-09-01T10:00:00Z",
    },
    devices: [device()],
    last_login: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

export function credential(overrides: Partial<IssuedCredential> = {}): IssuedCredential {
  return {
    device: device(),
    server_url: "https://tv.example.com",
    username: "sar-q7k2pa",
    password: "Kx7mPq2vRt9wZb4n",
    ...overrides,
  };
}

export const KPIS: Kpis = {
  customers_total: 24,
  customers_active: 19,
  customers_expired: 3,
  customers_suspended: 2,
  expiring_7d: 3,
  devices_total: 32,
  devices_blocked: 1,
  streams_now: 4,
  stream_users_now: 3,
  reviews_open: 2,
  transcode_queued: 5,
  transcode_running: 1,
  transcode_failed_24h: 0,
  as_of: "2026-10-03T21:00:00Z",
};

export function setting(
  overrides: Partial<SettingEntry> & Pick<SettingEntry, "key">,
): SettingEntry {
  return {
    group: overrides.key.split(".")[0] ?? "features",
    kind: "bool",
    description: "A setting.",
    default: false,
    value: false,
    is_default: true,
    sensitive: false,
    min_value: null,
    max_value: null,
    choices: null,
    updated_at: null,
    updated_by: null,
    ...overrides,
  };
}

export function auditEntry(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    id: "audit-1",
    at: "2026-10-03T20:00:00Z",
    actor: { id: "admin-1", username: "admin" },
    actor_ip: "10.0.0.5",
    action: "customer.suspend",
    target_type: "accounts.user",
    target_id: "cust-1",
    before: { status: "active" },
    after: { status: "suspended" },
    ...overrides,
  };
}

export const PERMISSIONS: Permission[] = [
  { code: "customers.view", description: "See customers." },
  { code: "customers.edit", description: "Change customers." },
  { code: "dashboard.view", description: "See the dashboard." },
];

export function role(overrides: Partial<Role> & Pick<Role, "id" | "name">): Role {
  return {
    description: "",
    permissions: [],
    is_system: true,
    admin_count: 0,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

/** A signed-in owner with an empty-but-working dashboard. */
export function signedIn(routes: Routes = {}, admin: Me = me()): Routes {
  return {
    "GET /api/v1/auth/me": { body: admin },
    "GET /api/v1/admin/dashboard/kpis": { body: KPIS },
    "GET /api/v1/admin/customers": { body: page([customerSummary()]) },
    ...routes,
  };
}
