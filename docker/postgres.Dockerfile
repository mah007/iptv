# syntax=docker/dockerfile:1.7
# PostgreSQL with pgBackRest (ADR-0020). One image, three uses:
#   target `postgres`: the database itself; WAL is archived by pgbackrest-iptv-archive;
#   target `backup`:   the backup service (apps/ops/agent.py) with pgBackRest, restic and
#                      the same PostgreSQL binaries (for pg_dump and the restore drill);
#                      also the `db-roles` one-shot (docker/postgres/db-roles.sh).
# Same base as before (postgres:18.6-alpine): the data volume is unchanged.

ARG POSTGRES_IMAGE=postgres:18.6-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
# pgBackRest (MIT), built from the release tarball: Alpine 3.24 ships 2.58.0, before the
# 2.59.3 fix for weak encryption keys after a random-source error.
ARG PGBACKREST_VERSION=2.59.3
ARG PGBACKREST_SHA256=b2a95535a68e326a0f316c2d8d3243bc30092cf1ce9de9be5d142e204d2d4263
# restic (BSD-2-Clause), built from the release's source tarball (checksum as in its
# SHA256SUMS) with the current Go, and with the dependencies that have fixed CVEs raised
# to their fixed versions: the 0.19.1 release binary (Go 1.26.4) fails `make scan`.
# Modules come from the Go proxy, checked against the checksum database. Drop the
# updates when a restic release includes them.
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SRC_SHA256=bb9b1a19040744d26d8a79be029d4e6b189c45ccc9d8831d7fe367d3c33df725
ARG RESTIC_DEP_UPDATES="golang.org/x/crypto@v0.57.0 golang.org/x/net@v0.60.0 google.golang.org/grpc@v1.83.2"
ARG GO_IMAGE=golang:1.26.9-alpine3.24@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade

FROM ${GO_IMAGE} AS restic-build
ARG RESTIC_VERSION
ARG RESTIC_SRC_SHA256
ARG RESTIC_DEP_UPDATES
ENV GOTOOLCHAIN=local CGO_ENABLED=0
ADD --checksum=sha256:${RESTIC_SRC_SHA256} \
    https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic-${RESTIC_VERSION}.tar.gz \
    /tmp/restic.tar.gz
# hadolint ignore=SC2086 (RESTIC_DEP_UPDATES is a list)
RUN mkdir /src && tar -xzf /tmp/restic.tar.gz -C /src --strip-components=1 \
    && cd /src && go get ${RESTIC_DEP_UPDATES} && go mod tidy \
    && go build -mod=readonly -trimpath \
        -ldflags "-s -w -X main.version=${RESTIC_VERSION}" -o /restic ./cmd/restic \
    && go test -count=1 ./internal/crypto/... ./internal/repository/... ./internal/backend/s3/... \
    && /restic version

FROM ${POSTGRES_IMAGE} AS pgbackrest-build
ARG PGBACKREST_VERSION
ARG PGBACKREST_SHA256
# curl-dev: PostgreSQL 18's libpq.pc names libcurl (OAuth), so pkg-config needs it.
RUN apk add --no-cache meson ninja gcc musl-dev pkgconf openssl-dev lz4-dev zstd-dev \
        bzip2-dev libxml2-dev yaml-dev zlib-dev curl-dev
ADD --checksum=sha256:${PGBACKREST_SHA256} \
    https://github.com/pgbackrest/pgbackrest/archive/release/${PGBACKREST_VERSION}.tar.gz \
    /tmp/pgbackrest.tar.gz
RUN mkdir /src && tar -xzf /tmp/pgbackrest.tar.gz -C /src --strip-components=1 \
    && PKG_CONFIG_PATH=/usr/local/lib/pkgconfig meson setup /build /src \
        --buildtype=release -Dlibssh2=disabled -Dlibsystemd=disabled \
    && ninja -C /build \
    && strip /build/src/pgbackrest

FROM ${POSTGRES_IMAGE} AS postgres
# The official image's gosu is a Go binary built with an old toolchain (Trivy: fixable
# Go standard-library CVEs). su-exec (C, MIT) takes the same `user command...` arguments
# and is what the Alpine images used before; it replaces gosu at the same path, so the
# upstream entrypoint and the backup agent are unchanged. The removal is its own layer
# (a whiteout), so image scanners see the old binary gone.
RUN rm /usr/local/bin/gosu
RUN apk add --no-cache lz4-libs zstd-libs libbz2 libxml2 su-exec \
    && ln -s /sbin/su-exec /usr/local/bin/gosu \
    && gosu postgres true
COPY --from=pgbackrest-build /build/src/pgbackrest /usr/local/bin/pgbackrest
COPY --chmod=0755 docker/postgres/pgbackrest-iptv-archive /usr/local/bin/pgbackrest-iptv-archive
RUN pgbackrest version

FROM postgres AS backup
RUN apk add --no-cache python3
COPY --from=restic-build /restic /usr/local/bin/restic
RUN restic version
COPY --chmod=0644 backend/apps/ops/agent.py /usr/local/lib/smart-iptv/agent.py
RUN chmod 0755 /usr/local/lib/smart-iptv
COPY --chmod=0755 docker/postgres/db-roles.sh /usr/local/bin/db-roles
# Mount points: a new named volume starts with this ownership.
RUN mkdir -p /backups /backup-config /drill /sources \
    && chown postgres:postgres /drill
ENTRYPOINT []
CMD ["python3", "/usr/local/lib/smart-iptv/agent.py", "run"]
