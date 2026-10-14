import { describe, expect, it } from "vitest";

import {
  buildCsp,
  NONCE_PLACEHOLDER,
  pageHeaders,
  parseOrigins,
  renderNginx,
} from "../csp/policy.js";

/* The admin's and the portal's Content-Security-Policy (G-24, ADR-0021). */

const MEDIA = ["https://media.example.com", "https://*.example.com"];

function directives(policy: string): Map<string, string[]> {
  return new Map(
    policy.split("; ").map((part) => {
      const [name = "", ...values] = part.split(" ");
      return [name, values];
    }),
  );
}

describe("csp", () => {
  it("allows no inline script or style, and scripts only from the app with its nonce", () => {
    for (const app of ["admin", "portal"] as const) {
      const policy = buildCsp(app, { mediaOrigins: MEDIA, nonce: "abc123" });
      expect(policy).not.toContain("unsafe-inline");
      expect(policy).not.toContain("unsafe-eval");
      const parsed = directives(policy);
      expect(parsed.get("default-src")).toEqual(["'none'"]);
      expect(parsed.get("script-src")).toEqual(["'self'", "'nonce-abc123'"]);
      expect(parsed.get("style-src")).toEqual(["'self'", "'nonce-abc123'"]);
      expect(parsed.get("frame-ancestors")).toEqual(["'none'"]);
      expect(parsed.get("object-src")).toEqual(["'none'"]);
      expect(parsed.get("base-uri")).toEqual(["'none'"]);
      expect(parsed.get("media-src")).toEqual(["'self'", "blob:", ...MEDIA]);
    }
  });

  it("lets only the portal frame trailers, and only the admin load TMDB images", () => {
    const admin = directives(buildCsp("admin", { mediaOrigins: [], nonce: "n" }));
    const portal = directives(buildCsp("portal", { mediaOrigins: [], nonce: "n" }));
    expect(admin.get("frame-src")).toEqual(["'self'"]);
    expect(portal.get("frame-src")).toContain("https://www.youtube-nocookie.com");
    expect(admin.get("img-src")).toContain("https://image.tmdb.org");
    expect(portal.get("img-src")).not.toContain("https://image.tmdb.org");
  });

  it("sends the companion headers, keys allowed only in the admin", () => {
    const admin = pageHeaders("admin", { mediaOrigins: [], nonce: "n" });
    const portal = pageHeaders("portal", { mediaOrigins: [], nonce: "n" });
    expect(admin["Permissions-Policy"]).toContain("publickey-credentials-get=(self)");
    expect(portal["Permissions-Policy"]).not.toContain("publickey-credentials");
    expect(admin["Referrer-Policy"]).toBe("same-origin");
    expect(admin["Cross-Origin-Opener-Policy"]).toBe("same-origin");
  });

  it("accepts only origins, so nothing else reaches the header", () => {
    expect(
      parseOrigins("https://cdn.example.net, https://*.example.com  http://media.localhost:8080"),
    ).toEqual(["https://cdn.example.net", "https://*.example.com", "http://media.localhost:8080"]);
    expect(parseOrigins("https://a.example; script-src * 'unsafe-inline' javascript:x")).toEqual(
      [],
    );
  });

  it("renders nginx headers with the request id as the nonce", () => {
    const conf = renderNginx("portal");
    expect(conf).toContain("'nonce-$request_id'");
    expect(conf).toContain(`sub_filter '${NONCE_PLACEHOLDER}' '$request_id';`);
    expect(conf).toContain("__MEDIA_ORIGINS__");
    expect(conf).toContain("upgrade-insecure-requests");
    expect(conf).toMatch(
      /add_header Reporting-Endpoints "csp=\\"\/api\/v1\/csp-report\\"" always;/,
    );
  });
});
