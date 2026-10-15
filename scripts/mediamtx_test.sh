#!/usr/bin/env bash
# MediaMTX sign-ins end to end on the dev stack (make mediamtx-test; ADR-0023).
#
# The production MediaMTX image with docker/mediamtx/mediamtx.yml runs in a throwaway
# container on the backend network and asks a throwaway web process (the dev image and
# database, with its own token and ingest users in its environment: the .env fallback of
# live.ingest_*) about every sign-in through /internal/mediamtx-auth. Then:
#   - publishing with the publish user works, a wrong password is refused;
#   - the read user reads the API and the stream; the publish user can't read;
#   - a caller without MediaMTX's token gets 401 from the endpoint.
# Nothing on the shared stack changes (the throwaway containers are not in Traefik), and
# the generated passwords are never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml)
network=smart-iptv-backend
web=smart-iptv-mtx-test-web
mtx=smart-iptv-mtx-test
image="$(sed -n 's/^ *image: \(bluenviron\/mediamtx:[^ ]*\)$/\1/p' docker/compose.mediamtx.yml)"
tools="$(sed -n 's/^ *image: \(bluenviron\/mediamtx:[^ ]*-ffmpeg[^ ]*\)$/\1/p' docker/compose.dev.yml)"
[[ -n "$image" && -n "$tools" ]] || { echo "MediaMTX images not found in the compose files" >&2; exit 1; }

export MEDIAMTX_AUTH_TOKEN MEDIAMTX_PUBLISH_PASS MEDIAMTX_READ_PASS
MEDIAMTX_AUTH_TOKEN="$(openssl rand -hex 32)"
MEDIAMTX_PUBLISH_PASS="$(openssl rand -hex 16)"
MEDIAMTX_READ_PASS="$(openssl rand -hex 16)"
export MEDIAMTX_PUBLISH_USER=q1-publisher MEDIAMTX_READ_USER=q1-reader
failed=0

cleanup() {
  docker rm -f "$mtx" "$web" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

ok() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1" >&2; failed=1; }

started=$SECONDS
echo "MediaMTX sign-ins through Django (prod image ${image%%@*}):"
"${compose[@]}" run -d --rm --no-deps --name "$web" -l traefik.enable=false \
  -e MEDIAMTX_AUTH_TOKEN -e MEDIAMTX_PUBLISH_USER -e MEDIAMTX_PUBLISH_PASS \
  -e MEDIAMTX_READ_USER -e MEDIAMTX_READ_PASS -e INTERNAL_HOSTS="web,localhost,127.0.0.1,$web" \
  web uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --no-access-log >/dev/null
docker run -d --name "$mtx" --network "$network" --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v "$PWD/docker/mediamtx/mediamtx.yml:/mediamtx.yml:ro" \
  -e MTX_AUTHHTTPADDRESS="http://mediamtx:${MEDIAMTX_AUTH_TOKEN}@${web}:8000/internal/mediamtx-auth" \
  "$image" >/dev/null

# The web process answers once Django is up (a few seconds).
deadline=$((SECONDS + 60))
until docker exec "$web" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/internal/health/live', timeout=2)" \
  >/dev/null 2>&1; do
  ((SECONDS < deadline)) || { echo "  FAIL  the throwaway web process did not start" >&2; exit 1; }
  sleep 1
done

api() { # api USER PASSWORD-VARIABLE: the HTTP status of MediaMTX's paths list
  docker exec -e "PASS=${!2}" "$web" python -c "
import base64, os, sys, urllib.request, urllib.error
auth = base64.b64encode(f'{sys.argv[1]}:{os.environ[\"PASS\"]}'.encode()).decode()
request = urllib.request.Request(sys.argv[2], headers={'Authorization': f'Basic {auth}'})
try:
    print(urllib.request.urlopen(request, timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
" "$1" "http://$mtx:9997/v3/paths/list"
}

publish() { # publish USER PASSWORD-VARIABLE SECONDS: ffmpeg's exit status
  docker run --rm --network "$network" -e "PASS=${!2}" --entrypoint sh "$tools" -c \
    "exec ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc=size=320x240:rate=25 \
       -t $3 -c:v libx264 -preset ultrafast -tune zerolatency -g 25 -f rtsp -rtsp_transport tcp \
       \"rtsp://$1:\$PASS@$mtx:8554/q1-test\"" >/dev/null 2>&1
}

export WRONG_PASS
WRONG_PASS="$(openssl rand -hex 16)"  # read through ${!2} in api and publish
until [[ "$(api "$MEDIAMTX_READ_USER" MEDIAMTX_READ_PASS)" == 200 ]]; do
  ((SECONDS < deadline)) || break
  sleep 1
done
if [[ "$(api "$MEDIAMTX_READ_USER" MEDIAMTX_READ_PASS)" == 200 ]]; then
  ok "the read user reads the API"
else
  fail "the read user reads the API"
fi
code="$(api "$MEDIAMTX_READ_USER" WRONG_PASS)"
if [[ "$code" == 401 || "$code" == 403 ]]; then
  ok "a wrong password is refused ($code)"
else
  fail "a wrong password is refused (got $code)"
fi
code="$(api "$MEDIAMTX_PUBLISH_USER" MEDIAMTX_PUBLISH_PASS)"
if [[ "$code" == 401 || "$code" == 403 ]]; then
  ok "the publish user can't use the API ($code)"
else
  fail "the publish user can't use the API (got $code)"
fi

if publish "$MEDIAMTX_PUBLISH_USER" WRONG_PASS 1; then
  fail "publishing with a wrong password is refused"
else
  ok "publishing with a wrong password is refused"
fi
publish "$MEDIAMTX_PUBLISH_USER" MEDIAMTX_PUBLISH_PASS 8 &
publisher=$!
read_ok=0
for _ in $(seq 1 12); do
  sleep 0.5
  if docker exec -e "PASS=$MEDIAMTX_READ_PASS" "$web" sh -c \
    "ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name -of csv=p=0 \
       \"rtsp://$MEDIAMTX_READ_USER:\$PASS@$mtx:8554/q1-test\"" 2>/dev/null | grep -q h264; then
    read_ok=1
    break
  fi
done
if ((read_ok)); then
  ok "the publish user publishes and the read user reads the stream"
else
  fail "the publish user publishes and the read user reads the stream"
fi
if docker exec -e "PASS=$MEDIAMTX_PUBLISH_PASS" "$web" sh -c \
  "ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name -of csv=p=0 \
     \"rtsp://$MEDIAMTX_PUBLISH_USER:\$PASS@$mtx:8554/q1-test\"" >/dev/null 2>&1; then
  fail "the publish user can't read"
else
  ok "the publish user can't read"
fi
wait "$publisher" || fail "the publisher ran to the end"

status="$(docker exec "$web" python -c "
import urllib.request, urllib.error
request = urllib.request.Request('http://127.0.0.1:8000/internal/mediamtx-auth', data=b'{}',
                                 headers={'Content-Type': 'application/json'})
try:
    print(urllib.request.urlopen(request, timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
")"
if [[ "$status" == 401 ]]; then
  ok "callers without MediaMTX's token get 401"
else
  fail "callers without MediaMTX's token get 401 (got $status)"
fi

if ((failed)); then
  echo "MediaMTX test FAILED (stored live.ingest_* settings in the dev database override the" >&2
  echo "test's users: reset them in Settings to run it). MediaMTX's log:" >&2
  # Its own lines only, shortened: nothing a response body might carry.
  docker logs --tail 30 "$mtx" 2>&1 | grep -E '^[0-9]{4}/' | cut -c1-200 >&2 || true
  exit 1
fi
echo "MediaMTX test passed in $((SECONDS - started)) s."
