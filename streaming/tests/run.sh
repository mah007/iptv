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
#   prework-edge-ctl  standalone edge     127.0.0.1:18083 (https), 18084 (http)
#                     (ADR-0022: its own TLS, the control channel and stream-auth over
#                     TLS to the stub's edges host on :8443, a private S3 origin)
#   prework-pebble    Let's Encrypt's test ACME server (Pebble, MPL-2.0)  127.0.0.1:18086
#   prework-edge-acme standalone edge, EDGE_TLS=acme   127.0.0.1:18085 (https)
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
EDGE_CTL=prework-edge-ctl
EDGE_ACME=prework-edge-acme
PEBBLE=prework-pebble
PEBBLE_IMAGE=${PEBBLE_IMAGE:-ghcr.io/letsencrypt/pebble:2.10.1@sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199}
AUTH=prework-auth
EDGE_URL=http://127.0.0.1:18080
CONTROL_URL=http://127.0.0.1:18081
EDGE_S3_URL=http://127.0.0.1:18082
EDGE_CTL_TLS_URL=https://127.0.0.1:18083
EDGE_CTL_URL=http://127.0.0.1:18084
AUTH_TTL=3 # seconds; production keeps the 60 s default (checked on the S3 edge)

remove_all() {
    docker rm -f -v "$EDGE" "$EDGE_S3" "$EDGE_CTL" "$EDGE_ACME" "$PEBBLE" "$AUTH" prework-edge-badkeys >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}

for tool in docker curl python3 openssl; do
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

# ADR-0022: the S3 presign (against AWS's published example) and the bucket mapping.
njs_s3_tests() {
    echo "== njs unit tests (s3.js and control.js against sigv4_vectors.json)"
    python3 "$STREAMING/tools/s3_presign.py" vectors | cmp -s - "$HERE/sigv4_vectors.json" ||
        { echo "  FAIL  sigv4_vectors.json is stale: run streaming/tools/s3_presign.py vectors"; return 1; }
    docker run --rm --name prework-edge-njs-s3 --network none \
        -v "$STREAMING:/streaming:ro" "$EDGE_IMAGE" \
        njs -q -m -p /streaming/nginx/njs /streaming/tests/njs/s3_test.js \
        /streaming/tests/sigv4_vectors.json
}

# A throwaway CA, and certificates for the stub's edges host and the standalone edge.
make_certs() {
    local dir=$WORK/tls name
    mkdir -p "$dir"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 \
        -subj "/CN=edge-test-ca" -keyout "$dir/ca.key" -out "$dir/ca.pem" \
        -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign 2>/dev/null
    for name in control edge.test; do
        openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$name" \
            -keyout "$dir/$name.key" -out "$dir/$name.csr" 2>/dev/null
        printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$name" > "$dir/$name.ext"
        openssl x509 -req -in "$dir/$name.csr" -CA "$dir/ca.pem" -CAkey "$dir/ca.key" \
            -CAcreateserial -days 2 -out "$dir/$name.pem" -extfile "$dir/$name.ext" 2>/dev/null
    done
    chmod 755 "$dir"
    chmod 644 "$dir"/*
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
step "njs s3.js passes sigv4_vectors.json (AWS's example included)" njs_s3_tests
make_certs
step "an invalid key file stops the edge" bad_key_file_refused

docker network create "$NET" >/dev/null
SUBNET=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$NET" | awk '{print $1}')
docker run -d --name "$AUTH" --network "$NET" --network-alias auth --network-alias origin \
    --network-alias control -p 127.0.0.1:18081:8000 \
    -v "$HERE:/stub:ro" -v "$STREAMING/tools:/tools:ro" -v "$WORK:/work:ro" \
    -v "$WORK/media:/media:ro" \
    "$STUB_IMAGE" python -u /stub/auth_stub.py --port 8000 --media /media \
    --s3-creds /work/origin-creds.json --keys /work/keys.json --edge-secret /work/edge_token \
    --cdn-secret /work/cdn_secret \
    --tls-port 8443 --tls-cert /work/tls/control.pem --tls-key /work/tls/control.key >/dev/null
run_edge "$EDGE" 18080 \
    -e EDGE_ID=edge-test-local -e EDGE_AUTH_CACHE_TTL="${AUTH_TTL}s" \
    -e EDGE_LIVE_ROOT=/srv/live -e EDGE_LIVE_RELAY=auth:8000 \
    -e EDGE_CDN=on -e EDGE_CDN_SECRET_FILE=/run/secrets/cdn_secret \
    -v "$WORK/cdn_secret:/run/secrets/cdn_secret:ro" \
    -v "$WORK/edge_token:/run/secrets/edge_auth_token:ro" \
    -v "$WORK/media:/srv/media:ro" -v "$WORK/live:/srv/live:ro"
run_edge "$EDGE_S3" 18082 \
    -e EDGE_ID=edge-test-s3 -e EDGE_MODE=s3 -e EDGE_ORIGIN_URL=http://origin:8000/origin \
    -e EDGE_ORIGIN_CREDENTIALS_FILE=/run/secrets/origin.json \
    -v "$WORK/origin-creds.json:/run/secrets/origin.json:ro"
# A standalone edge (ADR-0022): no key file, no internal stream-auth; everything from the
# control plane (the stub's TLS edges host), its own TLS, a small G-17 rate limit.
docker run -d --name "$EDGE_CTL" --network "$NET" \
    -p 127.0.0.1:18083:8443 -p 127.0.0.1:18084:8080 \
    --read-only \
    --tmpfs /var/cache/nginx --tmpfs /run --tmpfs /tmp --tmpfs /etc/nginx/conf.d \
    --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
    --security-opt no-new-privileges \
    -e EDGE_ID=edge-test-control -e EDGE_MODE=s3 -e EDGE_ORIGIN_URL=http://origin:8000/origin \
    -e EDGE_CONTROL_URL=https://control:8443 -e EDGE_CONTROL_CA_FILE=/certs/ca.pem \
    -e EDGE_CONTROL_TOKEN_FILE=/run/secrets/edge_token -e EDGE_AUTH_CACHE_TTL="${AUTH_TTL}s" \
    -e EDGE_TLS=file -e EDGE_DOMAIN=edge.test \
    -e EDGE_TLS_CERT=/certs/edge.test.pem -e EDGE_TLS_KEY=/certs/edge.test.key \
    -e EDGE_RATE_AFTER=2m -e EDGE_RATE_LIMIT=4m -e EDGE_CDN=on \
    -v "$WORK/edge_token:/run/secrets/edge_token:ro" -v "$WORK/tls:/certs:ro" \
    -v "$STREAMING/nginx:/etc/nginx/edge:ro" \
    -v "$STREAMING/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "$STREAMING/nginx/docker-entrypoint.d/40-edge-config.sh:/docker-entrypoint.d/40-edge-config.sh:ro" \
    "$EDGE_IMAGE" >/dev/null
wait_ready "$AUTH" "$CONTROL_URL/__control/health"
wait_ready "$EDGE" "$EDGE_URL/healthz"
wait_ready "$EDGE_S3" "$EDGE_S3_URL/healthz"

step "local disk mode" python3 "$HERE/edge_test.py" local \
    --edge "$EDGE_URL" --control "$CONTROL_URL" --work "$WORK" --auth-ttl "$AUTH_TTL"
step "live TV and catch-up" python3 "$HERE/edge_test.py" live \
    --edge "$EDGE_URL" --control "$CONTROL_URL" --work "$WORK"
step "S3 origin mode" python3 "$HERE/edge_test.py" s3 \
    --edge "$EDGE_S3_URL" --control "$CONTROL_URL" --work "$WORK" --container "$EDGE_S3"
step "standalone edge" python3 "$HERE/edge_test.py" control \
    --edge "$EDGE_CTL_TLS_URL" --plain "$EDGE_CTL_URL" --control "$CONTROL_URL" \
    --work "$WORK" --ca "$WORK/tls/ca.pem"

# The same standalone edge with Let's Encrypt (EDGE_TLS=acme) against Pebble, whose
# validation is switched off here (no public name). The state folder is a fresh volume,
# root-owned like a Docker volume in production.
docker run -d --name "$PEBBLE" --network "$NET" --network-alias pebble -p 127.0.0.1:18086:15000 \
    -e PEBBLE_VA_ALWAYS_VALID=1 -e PEBBLE_VA_NOSLEEP=1 "$PEBBLE_IMAGE" >/dev/null
docker cp "$PEBBLE:/test/certs/pebble.minica.pem" "$WORK/tls/pebble-ca.pem" >/dev/null
chmod 644 "$WORK/tls/pebble-ca.pem"
docker run -d --name "$EDGE_ACME" --network "$NET" -p 127.0.0.1:18085:8443 \
    --read-only \
    --tmpfs /var/cache/nginx --tmpfs /run --tmpfs /tmp --tmpfs /etc/nginx/conf.d \
    -v /var/lib/nginx-acme \
    --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
    --security-opt no-new-privileges \
    -e EDGE_ID=edge-test-control -e EDGE_MODE=s3 -e EDGE_ORIGIN_URL=http://origin:8000/origin \
    -e EDGE_CONTROL_URL=https://control:8443 -e EDGE_CONTROL_CA_FILE=/certs/ca.pem \
    -e EDGE_CONTROL_TOKEN_FILE=/run/secrets/edge_token \
    -e EDGE_TLS=acme -e EDGE_DOMAIN=edge.test -e EDGE_ACME_DIRECTORY=https://pebble:14000/dir \
    -e EDGE_ACME_CA_FILE=/certs/pebble-ca.pem -e EDGE_ACME_EMAIL=ops@example.com \
    -v "$WORK/edge_token:/run/secrets/edge_token:ro" -v "$WORK/tls:/certs:ro" \
    -v "$STREAMING/nginx:/etc/nginx/edge:ro" \
    -v "$STREAMING/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "$STREAMING/nginx/docker-entrypoint.d/40-edge-config.sh:/docker-entrypoint.d/40-edge-config.sh:ro" \
    "$EDGE_IMAGE" >/dev/null
step "standalone edge with Let's Encrypt (Pebble)" python3 "$HERE/edge_test.py" acme \
    --edge https://127.0.0.1:18085 --mgmt https://127.0.0.1:18086 \
    --pebble-ca "$WORK/tls/pebble-ca.pem" --work "$WORK"
docker logs "$EDGE_ACME" >"$WORK/edge-acme.log" 2>&1

docker stop -t 1 "$AUTH" >/dev/null
step "fail closed when stream-auth is down" python3 "$HERE/edge_test.py" failclosed \
    --edge "$EDGE_URL" --work "$WORK"

docker logs "$EDGE" >"$WORK/edge-local.log" 2>&1
docker logs "$EDGE_S3" >"$WORK/edge-s3.log" 2>&1
docker logs "$EDGE_CTL" >"$WORK/edge-ctl.log" 2>&1
step "logs carry no token, secret or credential" python3 "$HERE/edge_test.py" logs \
    --work "$WORK" "$WORK/edge-local.log" "$WORK/edge-s3.log" "$WORK/edge-ctl.log"

echo
echo "== summary"
printf '  %s\n' "${RESULTS[@]}"
if [ "$FAILED" -ne 0 ]; then
    echo "media edge test: FAILED"
    exit 1
fi
echo "media edge test: all steps passed"
