#!/bin/sh
# Prometheus entrypoint (ADR-0018). Renders what depends on the deployment into
# /run/prometheus (a tmpfs), then replaces itself with Prometheus. Prometheus has no
# environment expansion in its configuration, hence this step.
#
#   EDGE_CAPACITY_MBPS          the edge's uplink in Mbit/s, for EdgeEgressHigh (1000)
#   PUBLIC_PROBE_URLS           https URLs probed from outside-in (prod), space-separated
#   MONITORING_PUBLIC_URL       where browsers reach the monitoring host (alert links)
#   PROMETHEUS_RETENTION_TIME   how long samples are kept (30d)
#   PROMETHEUS_RETENTION_SIZE   and at most how much disk they use (10GB)
set -eu

die() {
    echo "prometheus-entrypoint: error: $*" >&2
    exit 1
}

matches() {
    printf '%s\n' "$1" | grep -Eq "^($2)\$"
}

capacity=${EDGE_CAPACITY_MBPS:-1000}
retention_time=${PROMETHEUS_RETENTION_TIME:-30d}
retention_size=${PROMETHEUS_RETENTION_SIZE:-10GB}
public_url=${MONITORING_PUBLIC_URL:-http://localhost:9090}
probes=${PUBLIC_PROBE_URLS:-}

matches "$capacity" '[1-9][0-9]{0,6}' || die "EDGE_CAPACITY_MBPS must be a whole number of Mbit/s (got '$capacity')"
matches "$retention_time" '[1-9][0-9]{0,4}(h|d|w|y)' || die "PROMETHEUS_RETENTION_TIME must look like 30d (got '$retention_time')"
matches "$retention_size" '[1-9][0-9]{0,5}(MB|GB|TB)' || die "PROMETHEUS_RETENTION_SIZE must look like 10GB (got '$retention_size')"
matches "$public_url" 'https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?' || die "MONITORING_PUBLIC_URL must be http(s)://host[:port] (got '$public_url')"

mkdir -p /run/prometheus/rules /run/prometheus/targets

cat > /run/prometheus/rules/capacity.yml <<EOF
# Rendered by entrypoint.sh from EDGE_CAPACITY_MBPS=${capacity}.
groups:
  - name: iptv-capacity
    rules:
      - record: iptv:edge_capacity_bits
        expr: vector(${capacity} * 1000000)
EOF

targets=""
for url in $probes; do
    matches "$url" 'https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?' ||
        die "PUBLIC_PROBE_URLS holds '$url'; only https://host[:port][/path] URLs are probed"
    targets="${targets:+$targets, }\"$url\""
done
if [ -n "$targets" ]; then
    printf '[{"targets": [%s]}]\n' "$targets" > /run/prometheus/targets/public.json
else
    printf '[]\n' > /run/prometheus/targets/public.json
fi

# make monitoring-test: validate the rendered configuration instead of starting.
if [ "${PROMETHEUS_CHECK_ONLY:-}" = 1 ]; then
    exec /bin/promtool check config /etc/prometheus/prometheus.yml
fi

exec /bin/prometheus \
    --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/prometheus \
    --storage.tsdb.retention.time="$retention_time" \
    --storage.tsdb.retention.size="$retention_size" \
    --web.external-url="${public_url%/}/prometheus/" \
    --web.route-prefix=/prometheus \
    "$@"
