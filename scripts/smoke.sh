#!/usr/bin/env bash
# Smoke-test a running stack through Traefik: public routes answer, internal
# routes stay internal, unknown hosts are refused, and every store is ready.
set -euo pipefail
cd "$(dirname "$0")/.."

value() { grep -E "^$1=" .env | cut -d= -f2-; }
port="$(value HTTP_PORT)"
domain="$(value DOMAIN)"
base_port=$([[ "$port" == "80" ]] && echo "" || echo ":$port")
failures=0

# Traefik picks up recreated containers asynchronously, so each check retries
# for up to SMOKE_TIMEOUT seconds before it counts as a failure.
deadline_s="${SMOKE_TIMEOUT:-30}"

expect() {
  local want="$1" url="$2" got="" waited=0
  while :; do
    got="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" || true)"
    [[ "$got" == "$want" || "$waited" -ge "$deadline_s" ]] && break
    sleep 1
    waited=$((waited + 1))
  done
  if [[ "$got" == "$want" ]]; then
    printf '  ok    %s %s\n' "$got" "$url"
  else
    printf '  FAIL  %s (expected %s) %s\n' "$got" "$want" "$url"
    failures=$((failures + 1))
  fi
}

echo "Public routes:"
expect 200 "http://api.$domain$base_port/"
expect 200 "http://api.$domain$base_port/api/v1/health"
expect 200 "http://tv.$domain$base_port/health"
expect 200 "http://admin.$domain$base_port/"
expect 200 "http://app.$domain$base_port/"
echo "Internal routes stay internal:"
expect 404 "http://api.$domain$base_port/internal/health/ready"
expect 404 "http://tv.$domain$base_port/internal/health/live"
echo "Unknown hosts are refused:"
expect 404 "http://unknown.$domain$base_port/"

echo "Readiness (inside the Docker network):"
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

if ((failures > 0)); then
  echo "Smoke test failed: $failures check(s)."
  exit 1
fi
echo "Smoke test passed."
