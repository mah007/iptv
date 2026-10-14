import type { Edge, EdgeList } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ALL_PERMISSIONS, me, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The Edges page (ADR-0022): this server's edge, standalone edges and CDN zones, their
 * health and load; adding one shows its secret once with the installer command. */

const NOW = new Date().toISOString();

function edge(overrides: Partial<Edge> = {}): Edge {
  return {
    id: "edge-local",
    edge_id: "local",
    name: "This server",
    kind: "local",
    storage: "disk",
    base_url: "",
    effective_url: "https://media.example.com",
    enabled: true,
    draining: false,
    weight: 100,
    capacity_mbps: 1000,
    audience: "all",
    canary_user_ids: [],
    origin_edge_id: null,
    status: "healthy",
    last_seen_at: NOW,
    connections: 12,
    egress_mbps: 250.5,
    kids: [],
    version: "",
    has_secret: false,
    secret_set_at: null,
    created_at: NOW,
    csp_changed: false,
    ...overrides,
  };
}

const REMOTE = edge({
  id: "edge-2",
  edge_id: "media2",
  name: "Frankfurt",
  kind: "standalone",
  storage: "origin",
  base_url: "https://media2.example.com",
  effective_url: "https://media2.example.com",
  status: "keys_outdated",
  kids: ["k1"],
  has_secret: true,
});

const LIST: EdgeList = {
  edges: [edge(), REMOTE],
  signing_kid: "k2",
  s3_enabled: true,
  cdn_ready: false,
  edges_host: "edges.example.com",
  control_url: "https://edges.example.com",
};

const OWNER = me({ permissions: [...ALL_PERMISSIONS, "edges.view", "edges.manage"] });
const VIEWER = me({ roles: ["viewer"], permissions: ["edges.view"] });
const SECRET = "s".repeat(43);

function edgeRow(edgeId: string): HTMLElement {
  const row = document.querySelector<HTMLElement>(`tr[data-edge="${edgeId}"]`);
  if (!row) throw new Error(`no row for the edge ${edgeId}`);
  return row;
}

describe("edges", () => {
  it("lists the edges with their health and load", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/edges": { body: LIST } }, OWNER));
    renderApp("/edges");
    expect(await screen.findByRole("heading", { level: 1, name: "Edges" })).toBeTruthy();
    const local = edgeRow("local");
    expect(within(local).getByText("Healthy")).toBeTruthy();
    expect(within(local).getByText("250.5 of 1,000 Mbit/s")).toBeTruthy();
    const remote = edgeRow("media2");
    expect(within(remote).getByText("Old keys")).toBeTruthy();
    expect(within(remote).getByText("The S3 origin")).toBeTruthy();
    expect(screen.getByText("https://edges.example.com")).toBeTruthy();
  });

  it("adds a standalone edge and shows its secret and the installer command once", async () => {
    const api = mockApi(
      signedIn(
        {
          "GET /api/v1/admin/edges": { body: LIST },
          "POST /api/v1/admin/edges": {
            status: 201,
            body: {
              ...edge({
                id: "edge-3",
                edge_id: "media3",
                name: "Riyadh",
                kind: "standalone",
                storage: "origin",
                base_url: "https://media3.example.com",
                status: "never_seen",
              }),
              secret: SECRET,
            },
          },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/edges");
    await user.click(await screen.findByRole("button", { name: "Add" }));
    await user.click(await screen.findByRole("menuitem", { name: "Standalone edge" }));
    await user.type(await screen.findByLabelText("Edge id"), "media3");
    await user.type(screen.getByLabelText("Name"), "Riyadh");
    const address = screen.getByLabelText("Address");
    await user.clear(address);
    await user.type(address, "https://media3.example.com");
    await user.click(screen.getByRole("button", { name: "Add edge" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/edges")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/edges")[0]?.body).toMatchObject({
      kind: "standalone",
      edge_id: "media3",
      name: "Riyadh",
      base_url: "https://media3.example.com",
      storage: "origin",
      weight: 100,
      capacity_mbps: 1000,
      audience: "all",
    });
    expect(await screen.findByText("Secret of Riyadh")).toBeTruthy();
    expect(
      screen.getByDisplayValue(
        "sudo bash install.sh --edge --control-url https://edges.example.com --edge-id media3 --edge-domain media3.example.com",
      ),
    ).toBeTruthy();
  });

  it("refuses an address with a path before calling the API", async () => {
    const api = mockApi(signedIn({ "GET /api/v1/admin/edges": { body: LIST } }, OWNER));
    const { user } = renderApp("/edges");
    await user.click(await screen.findByRole("button", { name: "Add" }));
    await user.click(await screen.findByRole("menuitem", { name: "Standalone edge" }));
    await user.type(await screen.findByLabelText("Edge id"), "x");
    await user.type(screen.getByLabelText("Name"), "X");
    await user.type(screen.getByLabelText("Address"), "media.example.com/path");
    await user.click(screen.getByRole("button", { name: "Add edge" }));
    expect(
      await screen.findByText("Enter an address such as https://media2.example.com (no path)."),
    ).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/edges")).toHaveLength(0);
  });

  it("tests an edge and explains each step", async () => {
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/edges": { body: LIST },
          "POST /api/v1/admin/edges/edge-2/test": {
            body: {
              ok: false,
              steps: [
                { name: "health", ok: true, code: "ok", detail: "" },
                { name: "media", ok: false, code: "http_403", detail: "edge_unauthorized" },
              ],
            },
          },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/edges");
    await user.click(await screen.findByRole("button", { name: "Actions for Frankfurt" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test" }));
    expect(await screen.findByText("Test of Frankfurt")).toBeTruthy();
    expect(screen.getByText("Refused (403)")).toBeTruthy();
    expect(screen.getByText("(edge_unauthorized)")).toBeTruthy();
  });

  it("lets viewers look but not change", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/edges": { body: LIST } }, VIEWER));
    const { user } = renderApp("/edges");
    expect(await screen.findByText("Frankfurt")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Actions for Frankfurt" }));
    expect(screen.queryByRole("menuitem", { name: "New secret" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Remove" })).toBeNull();
  });

  it("is refused without the permission", async () => {
    mockApi(
      signedIn(
        { "GET /api/v1/admin/edges": problem(403, "PERMISSION_DENIED") },
        me({ roles: ["support"], permissions: ["customers.view"] }),
      ),
    );
    renderApp("/edges");
    expect(await screen.findByText("You don't have access to this")).toBeTruthy();
  });
});
