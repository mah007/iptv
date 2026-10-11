#!/bin/sh
# Traefik's start (ADR-0018, ADR-0020), dev and production:
#  1. renders the static configuration into /tmp: the optional Let's Encrypt contact
#     (ACME_EMAIL) and, with CROWDSEC=1, the CrowdSec bouncer plugin in front of every
#     router of the public entry point (CROWDSEC_ENTRYPOINT: websecure, dev: web);
#  2. keeps /tmp/dynamic, the folder Traefik's file provider watches, in sync with the
#     repository's dynamic files and, with CrowdSec, the middleware file Django renders
#     from the admin's settings (read-only volume; until it exists, the image's default).
#     Traefik watches only the top of one folder, so the files are copied there, each
#     replaced atomically. If Django stops writing, the last good file stays;
#  3. runs Traefik.
# Traefik's static file can't read environment variables, hence the rendering. Without
# ACME_EMAIL, or with an invalid one, Traefik starts without a contact address (Let's
# Encrypt doesn't require one).
set -eu

src="${TRAEFIK_STATIC:-/etc/traefik/traefik.yml}"
dst=/tmp/traefik.yml
repo_dynamic=/etc/traefik/dynamic
generated=/etc/traefik/generated/crowdsec.yml
default_crowdsec=/usr/local/share/smart-iptv/crowdsec.default.yml
live=/tmp/dynamic
crowdsec="${CROWDSEC:-0}"
entrypoint="${CROWDSEC_ENTRYPOINT:-websecure}"

email="${ACME_EMAIL:-}"
if [ -n "$email" ] && ! printf '%s' "$email" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'; then
  echo "ACME_EMAIL is not an email address; starting without a Let's Encrypt contact." >&2
  email=""
fi
case "$crowdsec" in 1 | yes | true) crowdsec=1 ;; *) crowdsec=0 ;; esac
case "${HSTS_PRELOAD:-0}" in 1 | yes | true) hsts_preload=1 ;; *) hsts_preload=0 ;; esac

awk -v email="$email" -v crowdsec="$crowdsec" -v ep="$entrypoint" -v live="$live" '
  /^ *directory: \/etc\/traefik\/dynamic *$/ { match($0, /^ */); printf "%" RLENGTH "s" "directory: %s\n", "", live; next }
  { print }
  email != "" && /^ *storage: \/letsencrypt\/acme\.json *$/ {
    match($0, /^ */)
    printf "%" RLENGTH "s" "email: %s\n", "", email
  }
  crowdsec == 1 && $0 == "  " ep ":" {
    print "    http:"
    print "      middlewares:"
    print "        - crowdsec@file"
  }
  END {
    if (crowdsec == 1) {
      print "experimental:"
      print "  localPlugins:"
      print "    crowdsec:"
      print "      moduleName: github.com/maxlerebourg/crowdsec-bouncer-traefik-plugin"
    }
  }
' "$src" > "$dst"

# put SOURCE NAME: copy into the watched folder when it differs, atomically. With
# HSTS_PRELOAD=1, a line `# hsts-preload: <setting>` becomes `<setting>` (G-43).
put() {
  if [ "$hsts_preload" = 1 ]; then
    sed -E 's/^( *)# hsts-preload: (.*)$/\1\2/' "$1" > "$live/.$2.new"
  else
    cp "$1" "$live/.$2.new"
  fi
  if cmp -s "$live/.$2.new" "$live/$2" 2>/dev/null; then
    rm -f "$live/.$2.new"
  else
    mv "$live/.$2.new" "$live/$2"
  fi
}

sync_dynamic() {
  mkdir -p "$live"
  for file in "$repo_dynamic"/*.yml "$repo_dynamic"/*.yaml "$repo_dynamic"/*.toml; do
    [ -f "$file" ] && put "$file" "${file##*/}"
  done
  for file in "$live"/*; do
    [ -f "$file" ] || continue
    name="${file##*/}"
    [ "$name" = crowdsec.yml ] && continue
    [ -f "$repo_dynamic/$name" ] || rm -f "$file"
  done
  if [ "$crowdsec" = 1 ]; then
    if [ -s "$generated" ]; then put "$generated" crowdsec.yml; else put "$default_crowdsec" crowdsec.yml; fi
  else
    rm -f "$live/crowdsec.yml"
  fi
}

sync_dynamic
(
  while sleep 2; do
    sync_dynamic || echo "dynamic configuration sync failed; retrying" >&2
  done
) &

exec traefik --configFile="$dst" "$@"
