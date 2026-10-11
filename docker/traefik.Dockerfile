# syntax=docker/dockerfile:1.7
# Traefik with the CrowdSec bouncer plugin vendored as a local plugin (ADR-0020): nothing
# is downloaded when Traefik starts (a plugin that fails to download would take every
# router using it down). The entrypoint renders the static configuration and keeps the
# dynamic configuration folder in sync (docker/traefik/entrypoint.sh).

ARG TRAEFIK_IMAGE=traefik:v3.7.13@sha256:24841fe2de7304c149343d877d2923b4c8800a38ba015dea9174c23b20e344a0
# maxlerebourg/crowdsec-bouncer-traefik-plugin (Apache-2.0), with its vendored modules.
ARG CROWDSEC_PLUGIN_VERSION=1.7.1
ARG CROWDSEC_PLUGIN_SHA256=a50cdfe37d67c48be571417a26d8c5a2b28e4b91e5b1ed3b1ea2bcb0e6b4aa38

FROM ${TRAEFIK_IMAGE}
ARG CROWDSEC_PLUGIN_VERSION
ARG CROWDSEC_PLUGIN_SHA256
ADD --checksum=sha256:${CROWDSEC_PLUGIN_SHA256} \
    https://github.com/maxlerebourg/crowdsec-bouncer-traefik-plugin/archive/refs/tags/v${CROWDSEC_PLUGIN_VERSION}.tar.gz \
    /tmp/crowdsec-plugin.tar.gz
# Traefik looks for local plugins under ./plugins-local/src/<module> (its working dir: /).
RUN dir=/plugins-local/src/github.com/maxlerebourg/crowdsec-bouncer-traefik-plugin \
    && mkdir -p "$dir" \
    && tar -xzf /tmp/crowdsec-plugin.tar.gz -C "$dir" --strip-components=1 \
    && rm /tmp/crowdsec-plugin.tar.gz \
    && test -f "$dir/.traefik.yml"
COPY --chmod=0755 docker/traefik/entrypoint.sh /usr/local/bin/iptv-traefik-entrypoint
# Not under /etc/traefik: COPY --chmod would also give that folder 0644 (no traversal).
COPY --chmod=0644 docker/traefik/crowdsec.default.yml /usr/local/share/smart-iptv/crowdsec.default.yml
RUN chmod 0755 /usr/local/share/smart-iptv
ENTRYPOINT ["/usr/local/bin/iptv-traefik-entrypoint"]
