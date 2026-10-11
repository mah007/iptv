#!/usr/bin/env bash
# CrowdSec end to end on the dev stack with CROWDSEC=1 (make crowdsec-test; ADR-0020):
# a probe container on the edge network bans its own address (no one else is
# affected), gets 403 from Traefik's bouncer, is unbanned and gets 200 again; then the
# admin's "block CrowdSec bans" setting is switched off and the banned probe passes,
# which proves the setting reaches Traefik through the worker's file.
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml
  -f docker/compose.crowdsec.yml)
probe=smart-iptv-crowdsec-probe
image="smart-iptv/postgres-backup:${APP_VERSION:-dev}"
host="tv.$(sed -n 's/^DOMAIN=//p' .env)"
ip=""

cleanup() {
  [[ -n "$ip" ]] && "${compose[@]}" exec -T crowdsec cscli decisions delete --ip "$ip" >/dev/null 2>&1 || true
  printf 'true' | "${compose[@]}" exec -T web python manage.py set_setting security.crowdsec_enforce --stdin >/dev/null 2>&1 || true
  docker rm -f "$probe" >/dev/null 2>&1 || true
}
trap cleanup EXIT

status() { # the HTTP status the probe gets from Traefik for the Xtream host's /health
  docker exec "$probe" python3 -c "
import sys, urllib.request, urllib.error
request = urllib.request.Request('http://traefik/health', headers={'Host': sys.argv[1]})
try:
    print(urllib.request.urlopen(request, timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
" "$host"
}

wait_for() { # wait_for CODE WHAT: the bouncer syncs bans every 15 s
  local deadline=$((SECONDS + 60)) code
  while ((SECONDS < deadline)); do
    code="$(status)"
    if [[ "$code" == "$1" ]]; then echo "  ok    $2 ($code)"; return 0; fi
    sleep 2
  done
  echo "  FAIL  $2: still $code after 60 s" >&2
  return 1
}

docker rm -f "$probe" >/dev/null 2>&1 || true
docker run -d --rm --name "$probe" --network smart-iptv-edge --entrypoint sleep "$image" 600 >/dev/null
ip="$(docker inspect -f '{{(index .NetworkSettings.Networks "smart-iptv-edge").IPAddress}}' "$probe")"
echo "CrowdSec bouncer (probe at $ip):"
printf 'true' | "${compose[@]}" exec -T web python manage.py set_setting security.crowdsec_enforce --stdin >/dev/null
wait_for 200 "before the ban"
"${compose[@]}" exec -T crowdsec cscli decisions add --ip "$ip" --duration 10m --reason "make crowdsec-test" >/dev/null
wait_for 403 "banned"
printf 'false' | "${compose[@]}" exec -T web python manage.py set_setting security.crowdsec_enforce --stdin >/dev/null
wait_for 200 "banned, with blocking switched off in the admin"
printf 'true' | "${compose[@]}" exec -T web python manage.py set_setting security.crowdsec_enforce --stdin >/dev/null
wait_for 403 "banned, with blocking switched on again"
"${compose[@]}" exec -T crowdsec cscli decisions delete --ip "$ip" >/dev/null
wait_for 200 "unbanned (cscli decisions delete --ip)"
echo "CrowdSec test passed."
