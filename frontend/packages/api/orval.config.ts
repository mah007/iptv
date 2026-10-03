// Typed client for the admin API, generated from openapi/admin.yaml by `make api-client`.
// Generated files live in src/generated/ and are never edited by hand (ADR-0004).
import { defineConfig } from "orval";

export default defineConfig({
  admin: {
    input: { target: "./openapi/admin.yaml" },
    output: {
      // One file of hooks and one of models: adding endpoints never touches src/index.ts.
      mode: "split",
      target: "./src/generated/admin.ts",
      client: "react-query",
      httpClient: "fetch",
      clean: true,
      formatter: "prettier",
      // Path parameters are encoded, so a value can never change the route.
      urlEncodeParameters: true,
      override: {
        // Same-origin fetch with session cookie, CSRF header and problem+json errors.
        mutator: { path: "./src/fetcher.ts", name: "apiFetch" },
        // Hooks resolve to the response body; failures throw ApiError.
        fetch: { includeHttpResponseReturnType: false },
        query: { version: 5, signal: true },
      },
    },
  },
});
