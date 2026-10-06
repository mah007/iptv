#!/bin/sh
# Production Traefik start (docker/compose.prod.yml). Renders the static configuration
# with the optional Let's Encrypt contact address (ACME_EMAIL in .env) and runs Traefik
# with the copy: Traefik's static file can't read environment variables. Without
# ACME_EMAIL, or with an invalid one, Traefik starts exactly as configured, without a
# contact address (Let's Encrypt doesn't require one).
set -eu
src=/etc/traefik/traefik.yml
dst=/tmp/traefik.yml
email="${ACME_EMAIL:-}"
if [ -n "$email" ] && ! printf '%s' "$email" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'; then
  echo "ACME_EMAIL is not an email address; starting without a Let's Encrypt contact." >&2
  email=""
fi
awk -v email="$email" '
  { print }
  email != "" && /^ *storage: \/letsencrypt\/acme\.json *$/ {
    match($0, /^ */)
    printf "%" RLENGTH "s" "email: %s\n", "", email
  }
' "$src" > "$dst"
exec traefik --configFile="$dst" "$@"
