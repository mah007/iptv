# syntax=docker/dockerfile:1.7
# Admin SPA + customer portal.
# Targets: `dev` (Vite dev servers, sources bind-mounted) and `runtime` (static files on nginx).

# Base images pinned by digest as well as tag (G-39, ADR-0020).
ARG NODE_IMAGE=node:24.21.0-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66
ARG NGINX_IMAGE=nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94

FROM ${NODE_IMAGE} AS base
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app

FROM base AS dev
# World-writable store: dev runs as the host UID, which may not be 1000 (node).
RUN mkdir -p /pnpm-store && chmod 1777 /pnpm-store && chown node:node /app
USER node
EXPOSE 5173 5174
CMD ["sh", "-c", "pnpm install --frozen-lockfile --store-dir /pnpm-store && exec pnpm dev"]

FROM base AS build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
# pnpm applies these while installing (patchedDependencies; ADR-0021).
COPY frontend/patches/ patches/
COPY frontend/packages/ui/package.json packages/ui/
COPY frontend/packages/api/package.json packages/api/
COPY frontend/packages/api-portal/package.json packages/api-portal/
COPY frontend/apps/admin/package.json apps/admin/
COPY frontend/apps/portal/package.json apps/portal/
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir /pnpm-store
COPY frontend/ ./
RUN pnpm build
# The pages' security headers (ADR-0021): rendered from the same policy the dev servers use.
RUN mkdir -p /app/csp && node --input-type=module -e "\
import { renderNginx } from '/app/packages/ui/csp/policy.js'; \
import { writeFileSync } from 'node:fs'; \
for (const app of ['admin', 'portal']) writeFileSync('/app/csp/csp-' + app + '.conf.template', renderNginx(app));"

FROM ${NGINX_IMAGE} AS runtime
# Apply pending Alpine security fixes ahead of the next base-image release.
RUN apk upgrade --no-cache
COPY docker/nginx/frontend.conf /etc/nginx/nginx.conf
COPY docker/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/csp/ /etc/nginx/smart-iptv/
COPY --chmod=755 docker/nginx/30-csp.sh /docker-entrypoint.d/30-csp.sh
COPY --from=build /app/apps/admin/dist /srv/admin
COPY --from=build /app/apps/portal/dist /srv/portal
RUN mkdir -p /tmp/nginx && chown -R nginx:nginx /tmp/nginx
USER nginx
EXPOSE 8080 8081
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:8080/healthz || exit 1
