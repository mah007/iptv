# ADR-0004: Same-origin admin API with session cookie and CSRF

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M2

## Context
The admin SPA is served from `admin.<domain>` and needs an authenticated API. SPEC §10 puts the REST API on `api.<domain>`, and SPEC §11 asks for HttpOnly + Secure + SameSite=Lax cookies, CSRF on unsafe methods, CORS restricted to known origins, an optional IP allowlist for the admin surface, and an idle timeout.

Calling `api.<domain>` from the SPA would make every request cross-origin:
- `admin.localhost` and `api.localhost` are different *sites* to browsers, so a session cookie set by one is not sent with requests from the other (SameSite), and dev would behave differently from production;
- it needs CORS with credentials, preflight requests for every unsafe call, and a cookie scoped to the parent domain, which would then also reach `tv.` and `media.`;
- one IP allowlist could no longer cover the whole admin surface (SPA and API).

Tokens in the SPA (JWT in memory or storage) would avoid cookies, but any XSS could read them, and refresh-token rotation adds a second auth system for a single first-party client.

## Decision
1. **Same origin.** Traefik routes `admin.<DOMAIN>` with `PathPrefix(/api)` to `web` (router `admin-api`), and `app.<DOMAIN>` with `PathPrefix(/api)` likewise (router `portal-api`, used from M11b). Both have an explicit priority (1000) above the SPA routers, whose default priority is their rule length. Everything else on those hosts stays on `frontend`.
2. **One URLconf per host.** `ADMIN_HOST` (default `admin.<DOMAIN>`) serves `config.urls_admin`; `APP_HOST` (default `app.<DOMAIN>`) serves `config.urls_portal` (only `api/v1/health` for now). Both are in `ALLOWED_HOSTS`. `api.<DOMAIN>` keeps `config.urls_api` for external and token clients. Each app keeps its admin routes in `apps/<app>/urls_admin.py`, mounted under `/api/v1/admin/`. Unknown `/api` paths answer a problem+json 404 in every mode, never an HTML page or a redirect.
3. **Session cookie + CSRF, no JWT for the SPA.**
   - `apps.core.authentication.SessionAuthentication` (DRF session auth) is the default authentication. Unauthenticated requests get **401** with `WWW-Authenticate: Session realm="api"` and code `NOT_AUTHENTICATED`; authenticated users without permission get 403 `PERMISSION_DENIED`. It also binds `user_id` into the request's log context.
   - Cookies are host-only (no `SESSION_COOKIE_DOMAIN`), so admin and portal sessions never mix and `tv.`/`media.` never see them. `SESSION_COOKIE_HTTPONLY`, `SameSite=Lax`, `Secure` in production.
   - `CSRF_COOKIE_HTTPONLY = False`: the SPA reads `csrftoken` and sends it as `X-CSRFToken` on unsafe methods. CSRF failures answer problem+json (`CSRF_FAILURE_VIEW`).
   - `CSRF_TRUSTED_ORIGINS` lists the admin and portal origins, built from `PUBLIC_SCHEME` and `PUBLIC_PORT` (`config/origins.py`). Dev defaults to `http` on `HTTP_PORT` (so `http://admin.localhost:8080` on a machine where port 80 is busy); production defaults to `https` on 443. Same-origin requests already pass Django's Origin check; the list covers proxies that rewrite `Host`.
   - Idle timeout: `SESSION_COOKIE_AGE = 1800` with `SESSION_SAVE_EVERY_REQUEST = True`, so each request renews the 30 minutes (SPEC §8.2). Login (M3) may set a per-session expiry from the `security.admin_idle_timeout_min` setting.
4. **Login must enforce CSRF itself.** DRF checks CSRF only for requests authenticated by session. Endpoints that accept anonymous unsafe requests, such as `POST /api/v1/auth/login`, must apply `csrf_protect` (or `CSRFCheck`) explicitly, after `GET /api/v1/auth/csrf` has set the cookie.
5. **Typed client.** `make api-client` generates the admin URLconf's OpenAPI schema (`manage.py spectacular --urlconf config.urls_admin --validate --fail-on-warn`, logs on stderr only) into `frontend/packages/api/openapi/admin.yaml`, formats it with Prettier, and runs Orval into `frontend/packages/api/src/generated/` (TanStack Query hooks, fetch client). The hand-written mutator `src/fetcher.ts` is same-origin with `credentials: "same-origin"`, adds `X-CSRFToken` to unsafe methods (fetching `GET /api/v1/auth/csrf` once if the cookie is missing), and turns failures into `ApiError` (status, stable `code`, title, detail, field errors, request id, the problem document). Generated files are committed and never edited; `make api-client-check`, in `make ci` right after `typecheck`, fails when regeneration changes anything under `frontend/packages/api`.

## Alternatives considered
- **SPA calls `api.<domain>` with CORS and a parent-domain cookie:** preflights, a cookie visible to every subdomain, and different dev and production behaviour. Rejected.
- **JWT access + refresh tokens in the SPA:** XSS can exfiltrate tokens, and rotation with reuse detection is a second auth system to secure. SPEC §11 keeps JWT for app clients; the browser gets cookies.
- **Vite dev-server proxy:** works in dev only; production would still need one of the options above.
- **A separate backend-for-frontend service:** another deployable for what Traefik routing already provides.

## Consequences
- The admin API exists only on the admin host: an IP allowlist middleware on `admin.<domain>` (SPEC §11, optional) covers the SPA and its API together.
- Every SPA request carries the session cookie; every unsafe one needs the CSRF header. The fetcher does both, so feature code never handles them.
- New admin endpoints go into an app's `urls_admin.py` and show up in the generated client after `make api-client`; `make ci` fails until the regenerated client is committed.
- Revisit if a second browser app needs the admin API from another origin (it shouldn't), or when M11b adds the portal API on `app.<domain>`, which reuses this setup.
