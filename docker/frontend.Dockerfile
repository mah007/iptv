# syntax=docker/dockerfile:1.7
# Admin SPA + customer portal.
# Targets: `dev` (Vite dev servers, sources bind-mounted) and `runtime` (static files on nginx).

ARG NODE_IMAGE=node:24.21.0-trixie-slim
ARG NGINX_IMAGE=nginx:1.30.5-alpine

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
COPY frontend/packages/ui/package.json packages/ui/
COPY frontend/packages/api/package.json packages/api/
COPY frontend/packages/api-portal/package.json packages/api-portal/
COPY frontend/apps/admin/package.json apps/admin/
COPY frontend/apps/portal/package.json apps/portal/
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir /pnpm-store
COPY frontend/ ./
RUN pnpm build

FROM ${NGINX_IMAGE} AS runtime
# Apply pending Alpine security fixes ahead of the next base-image release.
RUN apk upgrade --no-cache
COPY docker/nginx/frontend.conf /etc/nginx/nginx.conf
COPY docker/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/apps/admin/dist /srv/admin
COPY --from=build /app/apps/portal/dist /srv/portal
RUN mkdir -p /tmp/nginx && chown -R nginx:nginx /tmp/nginx
USER nginx
EXPOSE 8080 8081
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:8080/healthz || exit 1
