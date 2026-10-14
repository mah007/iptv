#!/usr/bin/env bash
# Zero-downtime rollout of a service behind Traefik: web, or the frontend (M15, ADR-0022;
# docs/runbooks/deploy.md).
#
#   scripts/rollout.sh [--service web] [--replicas N] [--timeout 300] -- <compose options>
#
# e.g. scripts/rollout.sh -- --project-directory /opt/iptv -f docker/compose.yml -f docker/compose.prod.yml
#
# 1. Start N new replicas next to the running ones, from the current configuration and
#    images (build first; migrations run before this, once, in the `migrate` service).
# 2. Wait until Docker reports every new one healthy: Traefik only routes to healthy
#    containers, so they take requests from then on.
# 3. Drain each old replica (web's liveness answers 503, so Traefik's health check takes
#    it out of rotation within seconds), then stop it gracefully: gunicorn (or nginx)
#    finishes the requests in flight. Traefik's retry middleware covers a connection
#    refused meanwhile.
# 4. Remove the old containers.
# If a new replica never becomes healthy, it is removed and the old ones keep serving.
# If the update changes the service's Traefik labels, old and new replicas can't serve
# side by side (Traefik drops a router its containers define differently), so the
# service is recreated instead: a short interruption, once, for that update.
set -Eeuo pipefail

SERVICE=web
REPLICAS=""
TIMEOUT=300
DRAIN_FILE=/tmp/web-draining
DRAIN_WAIT=7
STOP_TIMEOUT=35

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

while (($#)); do
    case "$1" in
        --service) SERVICE="${2:?}"; shift ;;
        --replicas) REPLICAS="${2:?}"; shift ;;
        --timeout) TIMEOUT="${2:?}"; shift ;;
        -h | --help) usage; exit 0 ;;
        --) shift; break ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
COMPOSE_ARGS=("$@")

say() { printf 'rollout: %s\n' "$*"; }
die() { printf 'rollout: error: %s\n' "$*" >&2; exit 1; }
compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }

# Running containers of the service, oldest first.
running() {
    compose ps -q "$SERVICE" 2>/dev/null | while read -r id; do
        [[ -n "$id" ]] && docker inspect -f '{{.Created}} {{.Id}}' "$id"
    done | sort | awk '{print $2}'
}

# The service's traefik.* labels, sorted: from the configuration, or from a container.
traefik_labels() {
    python3 -I -c '
import json, sys
data = json.load(sys.stdin)
labels = data["services"][sys.argv[1]].get("labels") if sys.argv[1] else data
if isinstance(labels, list):
    labels = dict(item.split("=", 1) for item in labels)
for key in sorted(labels or {}):
    if key.startswith("traefik."):
        print(f"{key}={labels[key]}")
' "${1-}"
}
config_labels() { compose config --format json | traefik_labels "$SERVICE"; }
container_labels() { docker inspect -f '{{json .Config.Labels}}' "$1" | traefik_labels; }

# Docker's health of a container; one without a health check counts once it has run 5 s.
health() {
    local state
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else if .State.Running}}running {{.State.StartedAt}}{{else}}gone{{end}}' "$1" 2>/dev/null) || state=gone
    if [[ "$state" == running* ]]; then
        if (($(date +%s) - $(date -d "${state#running }" +%s) >= 5)); then state=healthy; else state=starting; fi
    fi
    echo "$state"
}

mapfile -t OLD < <(running)
if ((${#OLD[@]} == 0)); then
    say "no $SERVICE container runs yet: starting it normally"
    compose up -d --no-deps --wait "$SERVICE"
    exit 0
fi
WANT=${REPLICAS:-${#OLD[@]}}
[[ "$WANT" =~ ^[1-9][0-9]?$ ]] || die "--replicas must be 1-99"

if ! command -v python3 >/dev/null; then
    say "python3 is missing: can't compare the routing labels; assuming they're unchanged"
elif [[ "$(config_labels)" != "$(container_labels "${OLD[0]}")" ]]; then
    say "this update changes $SERVICE's routing labels: recreating it instead (a short interruption)"
    compose up -d --no-deps --wait --scale "$SERVICE=$WANT" "$SERVICE"
    say "done: $WANT $SERVICE replica(s) serving"
    exit 0
fi

say "starting $WANT new $SERVICE replica(s) next to ${#OLD[@]} running"
compose up -d --no-deps --no-recreate --scale "$SERVICE=$((${#OLD[@]} + WANT))" "$SERVICE" >/dev/null
mapfile -t ALL < <(running)
NEW=()
for id in "${ALL[@]}"; do
    [[ " ${OLD[*]} " == *" $id "* ]] || NEW+=("$id")
done
((${#NEW[@]} > 0)) || die "no new container started"

deadline=$((SECONDS + TIMEOUT))
for id in "${NEW[@]}"; do
    until [[ "$(health "$id")" == healthy ]]; do
        state=$(health "$id")
        if [[ "$state" == unhealthy || "$state" == gone ]] || ((SECONDS > deadline)); then
            say "a new replica is $state: removing the new ones, the old ones keep serving"
            docker rm -f "${NEW[@]}" >/dev/null 2>&1 || true
            exit 1
        fi
        sleep 2
    done
done
say "new replica(s) healthy; draining the old ones"
# Traefik registers healthy containers from its Docker events within a second or two.
sleep 3
for id in "${OLD[@]}"; do
    docker exec "$id" sh -c "touch $DRAIN_FILE" >/dev/null 2>&1 || true
done
sleep "$DRAIN_WAIT"
for id in "${OLD[@]}"; do
    docker stop -t "$STOP_TIMEOUT" "$id" >/dev/null
    docker rm "$id" >/dev/null
done
compose up -d --no-deps --no-recreate --scale "$SERVICE=$WANT" "$SERVICE" >/dev/null
say "done: $WANT new $SERVICE replica(s) serving"
