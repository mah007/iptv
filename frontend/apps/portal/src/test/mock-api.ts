import { vi } from "vitest";

/** A request the app sent, as the mock saw it. */
export interface MockRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

export interface MockReply {
  status?: number;
  body?: unknown;
}

type Handler = MockReply | ((request: MockRequest) => MockReply | Promise<MockReply>);

/** Routes keyed "METHOD /path", e.g. "GET /api/v1/me". */
export type Routes = Record<string, Handler>;

/** An RFC 9457 problem reply, as the API sends it. */
export function problem(
  status: number,
  code: string,
  fieldErrors?: Record<string, string[]>,
): MockReply {
  return {
    status,
    body: {
      type: `urn:smart-iptv:problem:${code.toLowerCase()}`,
      title: code,
      status,
      code,
      detail: `${code} from the test API.`,
      ...(fieldErrors ? { field_errors: fieldErrors } : {}),
    },
  };
}

function urlOf(input: RequestInfo | URL): URL {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return new URL(raw, "http://app.localhost");
}

/**
 * Replace `fetch` with an in-memory API. Unknown routes answer 404, so a test
 * fails loudly when the app calls something it didn't expect.
 */
export function mockApi(routes: Routes) {
  const requests: MockRequest[] = [];
  document.cookie = "csrftoken=test-csrf";
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = urlOf(input);
    const method = (init.method ?? "GET").toUpperCase();
    const body: unknown = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    const request: MockRequest = { method, path: url.pathname, query: url.searchParams, body };
    requests.push(request);
    const handler = routes[`${method} ${url.pathname}`];
    const reply =
      handler === undefined
        ? problem(404, "NOT_FOUND")
        : typeof handler === "function"
          ? await handler(request)
          : handler;
    const status = reply.status ?? 200;
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(reply.body ?? null), {
      status,
      headers: {
        "Content-Type": status >= 400 ? "application/problem+json" : "application/json",
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    requests,
    /** Requests to one route, oldest first. */
    sent: (method: string, path: string) =>
      requests.filter((request) => request.method === method && request.path === path),
  };
}
