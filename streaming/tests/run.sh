#!/usr/bin/env bash
# End-to-end test of the media edge (ADR-0007): the official nginx image with
# streaming/nginx mounted, a stub for Django's stream-auth (and an S3-style origin),
# and test media. Standalone containers only; it never touches the compose stack.
#
#   bash streaming/tests/run.sh
#
# Containers (removed on exit, also after a failure or Ctrl-C):
#   prework-edge      local disk mode     127.0.0.1:18080
#   prework-auth      stream-auth/origin  127.0.0.1:18081 (test control API)
#   prework-edge-s3   S3 origin mode      127.0.0.1:18082
# Needs docker, curl and python3 >= 3.10 on the host. Images: EDGE_IMAGE, STUB_IMAGE.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 # leave no __pycache__ in the tree

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STREAMING=$(dirname "$HERE")
EDGE_IMAGE=${EDGE_IMAGE:-nginx:1.30.5-alpine}
STUB_IMAGE=${STUB_IMAGE:-python:3.13.16-slim}
NET=prework-edge-net
EDGE=prework-edge
EDGE_S3=prework-edge-s3
AUTH=prework-auth
EDGE_URL=http://127.0.0.1:18080
CONTROL_URL=http://127.0.0.1:18081
EDGE_S3_URL=http://127.0.0.1:18082
AUTH_TTL=3 # seconds; production keeps the 60 s default (checked on the S3 edge)

remove_all() {
    docker rm -f "$EDGE" "$EDGE_S3" "$AUTH" prework-edge-badkeys >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}

for tool in docker curl python3; do
    command -v "$tool" >/dev/null || { echo "run.sh: $tool is required" >&2; exit 2; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    { echo "run.sh: python3 >= 3.10 is required" >&2; exit 2; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/edge-test.XXXXXX")
cleanup() {
    remove_all
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
remove_all

RESULTS=()
FAILED=0
step() {
    local name=$1
    shift
    echo
    if "$@"; then
        RESULTS+=("PASS  $name")
    else
        RESULTS+=("FAIL  $name")
        FAILED=1
    fi
}

# The edge as production would run it: read-only root file system, writable tmpfs
# only where nginx and the entrypoint write, and no capabilities beyond switching
# workers to the nginx user and chowning their cache directories.
run_edge() {
    local name=$1 port=$2
    shift 2
    docker run -d --name "$name" --network "$NET" -p "127.0.0.1:$port:8080" \
        --read-only \
        --tmpfs /var/cache/nginx --tmpfs /run --tmpfs /tmp --tmpfs /etc/nginx/conf.d \
        --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
        --security-opt no-new-privileges \
        -e EDGE_AUTH_UPSTREAM=auth:8000 \
        -e EDGE_CORS_ORIGINS="https://app.example.com, https://portal.example.com" \
        -e EDGE_REAL_IP_FROM="$SUBNET" \
        -v "$WORK/keys.json:/run/secrets/media_token_keys.json:ro" \
        -v "$STREAMING/nginx:/etc/nginx/edge:ro" \
        -v "$STREAMING/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
        -v "$STREAMING/nginx/docker-entrypoint.d/40-edge-config.sh:/docker-entrypoint.d/40-edge-config.sh:ro" \
        "$@" "$EDGE_IMAGE" >/dev/null
}

wait_ready() {
    local name=$1 url=$2
    for _ in $(seq 1 50); do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)" = 200 ]; then
            return 0
        fi
        if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != true ]; then
            break
        fi
        sleep 0.2
    done
    echo "run.sh: $name did not become ready; its output:" >&2
    docker logs "$name" 2>&1 | tail -20 >&2
    return 1
}

njs_unit_tests() {
    echo "== njs unit tests (token.js against vectors.json and fuzz.json)"
    docker run --rm --name prework-edge-njs --network none \
        -v "$STREAMING:/streaming:ro" -v "$WORK:/work:ro" "$EDGE_IMAGE" \
        njs -q -m -p /streaming/nginx/njs /streaming/tests/njs/token_test.js \
        /streaming/tests/vectors.json /work/fuzz.json
}

# A bad key file stops the container before nginx starts, with a message that names
# the problem and not the key.
bad_key_file_refused() {
    echo "== an invalid key file stops the edge"
    local secret out
    secret=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode())')
    printf '{"current": {"kid": "k1", "secret": "%s"}}\n' "$secret" >"$WORK/bad-keys.json"
    chmod 644 "$WORK/bad-keys.json"
    if out=$(docker run --rm --name prework-edge-badkeys --network none \
        -e EDGE_AUTH_UPSTREAM=auth:8000 -e EDGE_RESOLVER=127.0.0.11 --tmpfs /srv/media \
        -v "$WORK/bad-keys.json:/run/secrets/media_token_keys.json:ro" \
        -v "$STREAMING/nginx:/etc/nginx/edge:ro" \
        -v "$STREAMING/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
        -v "$STREAMING/nginx/docker-entrypoint.d/40-edge-config.sh:/docker-entrypoint.d/40-edge-config.sh:ro" \
        "$EDGE_IMAGE" /docker-entrypoint.sh nginx -t 2>&1); then
        echo "  FAIL  the edge accepted a 16-byte secret"
        return 1
    fi
    local ok=0
    if grep -q 'current.secret must be canonical unpadded base64url of 32-64 bytes' <<<"$out"; then
        echo "  ok    refused with a message naming the field"
    else
        echo "  FAIL  unexpected message: $(tail -1 <<<"$out")"
        ok=1
    fi
    if grep -qF "$secret" <<<"$out"; then
        echo "  FAIL  the secret appears in the output"
        ok=1
    else
        echo "  ok    the secret is not in the output"
    fi
    return $ok
}

echo "media edge test: $EDGE_IMAGE, stub $STUB_IMAGE, work dir $WORK"
step "vectors.json is current (make_vectors.py --check)" \
    python3 "$HERE/make_vectors.py" --check
step "reference signer passes vectors.json" python3 "$HERE/edge_test.py" reference
python3 "$HERE/edge_test.py" prepare "$WORK"
step "njs token.js passes vectors.json and the fuzz cases" njs_unit_tests
step "an invalid key file stops the edge" bad_key_file_refused

docker network create "$NET" >/dev/null
SUBNET=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$NET" | awk '{print $1}')
docker run -d --name "$AUTH" --network "$NET" --network-alias auth --network-alias origin \
    -p 127.0.0.1:18081:8000 \
    -v "$HERE:/stub:ro" -v "$WORK/media:/media:ro" \
    "$STUB_IMAGE" python -u /stub/auth_stub.py --port 8000 --media /media >/dev/null
run_edge "$EDGE" 18080 \
    -e EDGE_ID=edge-test-local -e EDGE_AUTH_CACHE_TTL="${AUTH_TTL}s" \
    -e EDGE_LIVE_ROOT=/srv/live -e EDGE_LIVE_RELAY=auth:8000 \
    -v "$WORK/media:/srv/media:ro" -v "$WORK/live:/srv/live:ro"
run_edge "$EDGE_S3" 18082 \
    -e EDGE_ID=edge-test-s3 -e EDGE_MODE=s3 -e EDGE_ORIGIN_URL=http://origin:8000/origin
wait_ready "$AUTH" "$CONTROL_URL/__control/health"
wait_ready "$EDGE" "$EDGE_URL/healthz"
wait_ready "$EDGE_S3" "$EDGE_S3_URL/healthz"

step "local disk mode" python3 "$HERE/edge_test.py" local \
    --edge "$EDGE_URL" --control "$CONTROL_URL" --work "$WORK" --auth-ttl "$AUTH_TTL"
step "live TV and catch-up" python3 "$HERE/edge_test.py" live \
    --edge "$EDGE_URL" --control "$CONTROL_URL" --work "$WORK"
step "S3 origin mode" python3 "$HERE/edge_test.py" s3 \
    --edge "$EDGE_S3_URL" --control "$CONTROL_URL" --work "$WORK" --container "$EDGE_S3"
docker stop -t 1 "$AUTH" >/dev/null
step "fail closed when stream-auth is down" python3 "$HERE/edge_test.py" failclosed \
    --edge "$EDGE_URL" --work "$WORK"

docker logs "$EDGE" >"$WORK/edge-local.log" 2>&1
docker logs "$EDGE_S3" >"$WORK/edge-s3.log" 2>&1
step "logs carry no token, secret or credential" python3 "$HERE/edge_test.py" logs \
    --work "$WORK" "$WORK/edge-local.log" "$WORK/edge-s3.log"

echo
echo "== summary"
printf '  %s\n' "${RESULTS[@]}"
if [ "$FAILED" -ne 0 ]; then
    echo "media edge test: FAILED"
    exit 1
fi
echo "media edge test: all steps passed"
