import { AudienceEnum, StorageEnum, type Edge, type EdgeWriteRequest } from "@smart-iptv/api";
import { z } from "zod";

/* The edge form (ADR-0022). Messages are translation keys. */

export const STORAGES = Object.values(StorageEnum);
export const AUDIENCES = Object.values(AudienceEnum);
export const NEW_KINDS = ["standalone", "cdn"] as const;
export type NewKind = (typeof NEW_KINDS)[number];

const whole = (min: number, max: number, message: string) =>
  z
    .string()
    .trim()
    .regex(/^\d+$/u, message)
    .transform(Number)
    .pipe(z.number().min(min, message).max(max, message));

export const edgeFormSchema = z
  .object({
    kind: z.enum(["local", "standalone", "cdn"]),
    edge_id: z
      .string()
      .trim()
      .regex(/^[A-Za-z0-9_.-]{1,64}$/u, "edges.validation.edgeId"),
    name: z
      .string()
      .trim()
      .min(1, "edges.validation.nameRequired")
      .max(100, "edges.validation.nameTooLong"),
    base_url: z.string().trim(),
    storage: z.enum(StorageEnum),
    origin_edge_id: z.string(),
    weight: whole(0, 1000, "edges.validation.weight"),
    capacity_mbps: whole(1, 1_000_000, "edges.validation.capacity"),
    audience: z.enum(AudienceEnum),
    canary_user_ids: z.array(z.string()),
    draining: z.boolean(),
    enabled: z.boolean(),
  })
  .superRefine((values, ctx) => {
    if (
      values.kind !== "local" &&
      !/^https?:\/\/[A-Za-z0-9.-]+(:\d{1,5})?$/u.test(values.base_url)
    ) {
      ctx.addIssue({ code: "custom", path: ["base_url"], message: "edges.validation.baseUrl" });
    }
    if (values.kind === "cdn" && values.origin_edge_id === "") {
      ctx.addIssue({
        code: "custom",
        path: ["origin_edge_id"],
        message: "edges.validation.origin",
      });
    }
  });

export type EdgeFormInput = z.input<typeof edgeFormSchema>;
export type EdgeFormValues = z.output<typeof edgeFormSchema>;

export function newEdgeValues(kind: NewKind): EdgeFormInput {
  return {
    kind,
    edge_id: "",
    name: "",
    base_url: "https://",
    storage: kind === "cdn" ? "disk" : "origin",
    origin_edge_id: "",
    weight: "100",
    capacity_mbps: "1000",
    audience: "all",
    canary_user_ids: [],
    draining: false,
    enabled: true,
  };
}

export function edgeFormValues(edge: Edge): EdgeFormInput {
  return {
    kind: edge.kind,
    edge_id: edge.edge_id,
    name: edge.name,
    base_url: edge.base_url,
    storage: edge.storage,
    origin_edge_id: edge.origin_edge_id ?? "",
    weight: String(edge.weight),
    capacity_mbps: String(edge.capacity_mbps),
    audience: edge.audience,
    canary_user_ids: [...edge.canary_user_ids],
    draining: edge.draining,
    enabled: edge.enabled,
  };
}

/** What the API takes: creation sends every field, a change only what an edge has. */
export function edgeRequest(values: EdgeFormValues, creating: boolean): EdgeWriteRequest {
  const common: EdgeWriteRequest = {
    name: values.name,
    weight: values.weight,
    capacity_mbps: values.capacity_mbps,
    audience: values.audience,
    canary_user_ids: values.audience === "canary" ? values.canary_user_ids : [],
    draining: values.draining,
    enabled: values.enabled,
  };
  if (values.kind === "local") return common;
  const remote: EdgeWriteRequest = {
    ...common,
    base_url: values.base_url,
    ...(values.kind === "cdn"
      ? { origin_edge_id: values.origin_edge_id }
      : { storage: values.storage }),
  };
  return creating ? { ...remote, kind: values.kind, edge_id: values.edge_id } : remote;
}

export const EDGE_FIELD_PATHS = {
  edge_id: "edge_id",
  name: "name",
  base_url: "base_url",
  storage: "storage",
  weight: "weight",
  capacity_mbps: "capacity_mbps",
  origin_edge_id: "origin_edge_id",
  canary_user_ids: "canary_user_ids",
} as const;

/** The installer command for a new standalone edge: it asks for the secret (hidden
 * input), so the secret never sits in the shell's history (EDGE_TOKEN works too). */
export function installCommand(
  controlUrl: string,
  edge: Pick<Edge, "edge_id" | "base_url">,
): string {
  let host = edge.base_url;
  try {
    host = new URL(edge.base_url).hostname;
  } catch {
    /* The form validated it; keep what was typed. */
  }
  return [
    "sudo bash install.sh --edge",
    `--control-url ${controlUrl}`,
    `--edge-id ${edge.edge_id}`,
    `--edge-domain ${host}`,
  ].join(" ");
}
