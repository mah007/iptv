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
# restic (BSD-2-Clause), the release's static binary.
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c

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
RUN apk add --no-cache lz4-libs zstd-libs libbz2 libxml2
COPY --from=pgbackrest-build /build/src/pgbackrest /usr/local/bin/pgbackrest
COPY --chmod=0755 docker/postgres/pgbackrest-iptv-archive /usr/local/bin/pgbackrest-iptv-archive
RUN pgbackrest version

FROM postgres AS backup
ARG RESTIC_VERSION
ARG RESTIC_SHA256
RUN apk add --no-cache python3
ADD --checksum=sha256:${RESTIC_SHA256} \
    https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2 \
    /tmp/restic.bz2
RUN bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic && chmod 0755 /usr/local/bin/restic \
    && rm /tmp/restic.bz2 && restic version
COPY --chmod=0644 backend/apps/ops/agent.py /usr/local/lib/smart-iptv/agent.py
RUN chmod 0755 /usr/local/lib/smart-iptv
COPY --chmod=0755 docker/postgres/db-roles.sh /usr/local/bin/db-roles
# Mount points: a new named volume starts with this ownership.
RUN mkdir -p /backups /backup-config /drill /sources \
    && chown postgres:postgres /drill
ENTRYPOINT []
CMD ["python3", "/usr/local/lib/smart-iptv/agent.py", "run"]
