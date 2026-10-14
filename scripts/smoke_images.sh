#!/usr/bin/env bash
# Smoke-test the production images exactly as built: generate a throwaway env
# from .env.example (proving the template and secrets script), run Django's
# deploy checks, then start both images and wait for their healthchecks.
set -euo pipefail
cd "$(dirname "$0")/.."

version="${APP_VERSION:-dev}"
app_image="smart-iptv/app:$version"
media_image="smart-iptv/media:$version"
frontend_image="smart-iptv/frontend:$version"
suffix="$$"
app_name="smart-iptv-smoke-app-$suffix"
frontend_name="smart-iptv-smoke-frontend-$suffix"
env_dir="$(mktemp -d)"
env_file="$env_dir/smoke.env"

cleanup() {
  docker rm -f "$app_name" "$frontend_name" >/dev/null 2>&1 || true
  rm -rf "$env_dir"
}
trap cleanup EXIT

scripts/secrets.sh "$env_file" >/dev/null

# Wait until a container's healthcheck reports healthy; fail fast if it dies.
wait_healthy() {
  local name="$1" deadline_s="$2" waited=0 status
  while :; do
    status="$(docker inspect -f '{{if .State.Running}}{{.State.Health.Status}}{{else}}exited{{end}}' "$name")"
    case "$status" in
      healthy) echo "  ok    $name is healthy"; return 0 ;;
      exited | unhealthy) break ;;
    esac
    ((waited >= deadline_s)) && break
    sleep 2
    waited=$((waited + 2))
  done
  echo "  FAIL  $name is $status after ${waited}s; last logs:" >&2
  docker logs --tail 40 "$name" >&2 || true
  return 1
}

echo "Django deploy checks (production settings, env generated from .env.example):"
docker run --rm --env-file "$env_file" -e DJANGO_SETTINGS_MODULE=config.settings.prod \
  "$app_image" python manage.py check --deploy --fail-level WARNING

echo "Starting the production images:"
docker run -d --name "$app_name" --env-file "$env_file" \
  -e DJANGO_SETTINGS_MODULE=config.settings.prod "$app_image" >/dev/null
# No web here: the CSP's media origins come from the fallback (docker/nginx/30-csp.sh).
docker run -d --name "$frontend_name" -e CSP_FETCH_ATTEMPTS=1 \
  -e CSP_MEDIA_ORIGINS="https://media.example.com https://*.example.com" "$frontend_image" >/dev/null
wait_healthy "$app_name" 90
wait_healthy "$frontend_name" 60

# Gunicorn's workers write metrics to a multiprocess directory that /metrics
# aggregates (config/gunicorn_conf.py); the healthchecks above produced samples.
echo "Prometheus metrics from the Gunicorn workers:"
if docker exec "$app_name" python -c "
import sys, urllib.request
request = urllib.request.Request('http://127.0.0.1:8000/metrics', headers={'Host': 'localhost'})
with urllib.request.urlopen(request, timeout=10) as resp:
    body = resp.read().decode()
ok = '# TYPE iptv_subscriptions gauge' in body and 'django_http_requests_total_by_method_total' in body
sys.exit(0 if ok else 1)
"; then
  echo "  ok    /metrics aggregates the workers' samples"
else
  echo "  FAIL  /metrics; last logs:" >&2
  docker logs --tail 40 "$app_name" >&2 || true
  exit 1
fi
# The SPAs' security headers (ADR-0021): a CSP whose nonce is fresh per request and the
# same as the page's, no inline script without it, and the companion headers.
echo "Security headers of the SPAs (production image):"
if docker exec "$frontend_name" sh -c '
  set -e
  for port in 8080 8081; do
    one="$(wget -S -q -O /tmp/nginx/page1 "http://127.0.0.1:$port/" 2>&1)"
    two="$(wget -S -q -O /tmp/nginx/page2 "http://127.0.0.1:$port/customers" 2>&1)"
    csp1="$(printf "%s" "$one" | sed -n "s/^ *Content-Security-Policy: //p")"
    csp2="$(printf "%s" "$two" | sed -n "s/^ *Content-Security-Policy: //p")"
    nonce1="$(printf "%s" "$csp1" | sed -n "s/.*nonce-\([0-9a-f]\{32\}\).*/\1/p")"
    nonce2="$(printf "%s" "$csp2" | sed -n "s/.*nonce-\([0-9a-f]\{32\}\).*/\1/p")"
    [ -n "$nonce1" ] && [ -n "$nonce2" ] && [ "$nonce1" != "$nonce2" ]
    grep -q "nonce=\"$nonce1\"" /tmp/nginx/page1
    ! grep -q CSPNONCE /tmp/nginx/page1
    printf "%s" "$csp1" | grep -q "frame-ancestors '"'"'none'"'"'"
    printf "%s" "$csp1" | grep -q "https://media.example.com"
    ! printf "%s" "$csp1" | grep -q "unsafe-inline"
    printf "%s" "$one" | grep -q "Permissions-Policy:"
    printf "%s" "$one" | grep -q "Referrer-Policy:"
    printf "%s" "$one" | grep -q "X-Content-Type-Options: nosniff"
    # Never a 304 for a page: the cached page would keep an old nonce.
    printf "%s" "$one" | grep -q "Cache-Control: no-store"
    ! printf "%s" "$one" | grep -qi "^ *ETag:"
    ! printf "%s" "$one" | grep -qi "^ *Last-Modified:"
  done
'; then
  echo "  ok    a fresh nonce per page, in the CSP and the page, never revalidated; no unsafe-inline; headers set"
else
  echo "  FAIL  the SPAs' security headers; last logs:" >&2
  docker logs --tail 40 "$frontend_name" >&2 || true
  exit 1
fi
# The media image (worker, watcher, transcoder) runs FFmpeg as a separate process.
echo "FFmpeg in the media image:"
if docker run --rm --entrypoint sh "$media_image" -c \
  'ffprobe -hide_banner -version | head -1 && ffmpeg -hide_banner -encoders | grep -q libx264 && python -c "import watchdog"'; then
  echo "  ok    ffprobe and ffmpeg (libx264) run as the app user"
else
  echo "  FAIL  FFmpeg in $media_image" >&2
  exit 1
fi
echo "Image smoke test passed."
