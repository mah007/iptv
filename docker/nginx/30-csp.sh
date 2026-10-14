#!/bin/sh
# The SPAs' security headers (ADR-0021), filled in at container start: the media origins
# the Content-Security-Policy allows come from Django (/internal/frontend-csp: the media
# edge, *.<DOMAIN> and the admin's security.csp_extra_media_origins), else from
# CSP_MEDIA_ORIGINS. Changing the extra origins in the admin applies at the next start
# (install.sh --update recreates this container). Origins are checked again here, since
# they end up inside a header.
set -eu

src=/etc/nginx/smart-iptv
out=/tmp/nginx/conf.d
url="${CSP_ORIGINS_URL:-http://web:8000/internal/frontend-csp}"
mkdir -p "$out"

origins=""
attempt=0
while [ "$attempt" -lt "${CSP_FETCH_ATTEMPTS:-30}" ]; do
  if fetched="$(wget -q -T 3 -O - "$url" 2>/dev/null)"; then
    origins="$fetched"
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
if [ -z "$origins" ]; then
  echo "30-csp.sh: Django's media origins unavailable; using CSP_MEDIA_ORIGINS" >&2
  origins="${CSP_MEDIA_ORIGINS:-}"
fi

clean=""
for origin in $origins; do
  if printf '%s' "$origin" | grep -Eq '^https?://(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?$'; then
    clean="$clean $origin"
  else
    echo "30-csp.sh: ignored a media origin that is not an origin" >&2
  fi
done
clean="${clean# }"

for app in admin portal; do
  sed "s|__MEDIA_ORIGINS__|$clean|g" "$src/csp-$app.conf.template" > "$out/.csp-$app.conf"
  mv "$out/.csp-$app.conf" "$out/csp-$app.conf"
done
echo "30-csp.sh: media origins: ${clean:-none}"
