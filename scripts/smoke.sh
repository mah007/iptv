#!/usr/bin/env bash
# Smoke-test a running stack through Traefik: public routes answer, the SPA hosts
# send /api to Django (ADR-0004), API errors are problem+json, internal routes and
# metrics stay internal, unknown hosts are refused, and every store is ready.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v curl >/dev/null || { echo "smoke.sh needs curl on the host" >&2; exit 1; }

value() { grep -E "^$1=" .env | cut -d= -f2-; }
port="$(value HTTP_PORT)"
domain="$(value DOMAIN)"
base_port=$([[ "$port" == "80" ]] && echo "" || echo ":$port")
failures=0

# Traefik picks up recreated containers asynchronously, so each check retries
# for up to SMOKE_TIMEOUT seconds before it counts as a failure.
deadline_s="${SMOKE_TIMEOUT:-30}"

# expect STATUS URL [CONTENT_TYPE]: the status must match, and the Content-Type
# must contain CONTENT_TYPE when one is given.
expect() {
  local want="$1" url="$2" want_type="${3:-}" result="" got="" type="" waited=0
  while :; do
    result="$(curl -s -o /dev/null -w '%{http_code} %{content_type}' --max-time 10 "$url" || true)"
    got="${result%% *}"
    type="${result#* }"
    if [[ "$got" == "$want" && ( -z "$want_type" || "$type" == *"$want_type"* ) ]]; then
      printf '  ok    %s %s%s\n' "$got" "$url" "${want_type:+ ($want_type)}"
      return 0
    fi
    [[ "$waited" -ge "$deadline_s" ]] && break
    sleep 1
    waited=$((waited + 1))
  done
  printf '  FAIL  %s %s (expected %s%s) %s\n' "$got" "$type" "$want" "${want_type:+ $want_type}" "$url"
  failures=$((failures + 1))
}

echo "Public routes:"
expect 200 "http://api.$domain$base_port/"
expect 200 "http://api.$domain$base_port/api/v1/health"
expect 200 "http://tv.$domain$base_port/health"
expect 200 "http://admin.$domain$base_port/" "text/html"
expect 200 "http://app.$domain$base_port/" "text/html"
echo "Same-origin APIs on the SPA hosts (ADR-0004):"
expect 200 "http://admin.$domain$base_port/api/v1/health" "application/json"
expect 200 "http://app.$domain$base_port/api/v1/health" "application/json"
expect 401 "http://admin.$domain$base_port/api/v1/admin/settings" "application/problem+json"
expect 404 "http://admin.$domain$base_port/api/v1/no-such-endpoint" "application/problem+json"
expect 200 "http://admin.$domain$base_port/settings" "text/html"
echo "Admin sign-in API (ADR-0006):"
expect 204 "http://admin.$domain$base_port/api/v1/auth/csrf"
expect 401 "http://admin.$domain$base_port/api/v1/auth/me" "application/problem+json"
expect 401 "http://admin.$domain$base_port/api/v1/admin/customers" "application/problem+json"
expect 404 "http://api.$domain$base_port/api/v1/auth/csrf" "application/problem+json"
echo "Internal routes stay internal:"
expect 404 "http://api.$domain$base_port/internal/health/ready"
expect 404 "http://tv.$domain$base_port/internal/health/live"
expect 404 "http://api.$domain$base_port/metrics"
expect 404 "http://tv.$domain$base_port/metrics"
expect 404 "http://admin.$domain$base_port/api/metrics" "application/problem+json"
echo "Unknown hosts are refused:"
expect 404 "http://unknown.$domain$base_port/"

echo "Readiness and metrics (inside the Docker network):"
compose=(docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml)
if "${compose[@]}" exec -T web python -c "
import json, sys, urllib.request
with urllib.request.urlopen('http://web:8000/internal/health/ready', timeout=10) as resp:
    body = json.load(resp)
print('  ' + ', '.join(f\"{name}={'ok' if c['ok'] else 'DOWN'}\" for name, c in body['checks'].items()))
sys.exit(0 if body['status'] == 'ok' else 1)
"; then
  echo "  ok    all stores ready"
else
  echo "  FAIL  readiness"
  failures=$((failures + 1))
fi
if "${compose[@]}" exec -T web python -c "
import sys, urllib.request
with urllib.request.urlopen('http://web:8000/metrics', timeout=10) as resp:
    body = resp.read().decode()
sys.exit(0 if '# TYPE iptv_subscriptions gauge' in body else 1)
"; then
  echo "  ok    /metrics on the internal network"
else
  echo "  FAIL  /metrics on the internal network"
  failures=$((failures + 1))
fi

if ((failures > 0)); then
  echo "Smoke test failed: $failures check(s)."
  exit 1
fi
echo "Smoke test passed."
