#!/usr/bin/env bash
# Rotate a key or a password (SPEC §16 M14, ADR-0020, docs/runbooks/rotate-keys.md).
#
#   sudo scripts/rotate-keys.sh media-token [--finish]   media URL signing keys
#   sudo scripts/rotate-keys.sh secret-key [--finish]    DJANGO_SECRET_KEY
#   sudo scripts/rotate-keys.sh field-encryption         FIELD_ENCRYPTION_KEY (re-encrypts)
#   sudo scripts/rotate-keys.sh db-app | db-reporting | db-monitor | db-owner
#   sudo scripts/rotate-keys.sh valkey-app | valkey-exporter | valkey-default
#
# Options: --dir DIR (the installation; default from /etc/smart-iptv/install.conf, else
# this checkout), --dev (the development stack's compose files), --yes (no question).
# COMPOSE_EXTRA (more `-f file` arguments) and COMPOSE_PROJECT_NAME pass through, which
# the rotation tests use for a throwaway project.
#
# Two-step rotations never refuse anyone: media-token keeps accepting the old key until
# every URL signed with it has expired (--finish refuses earlier), secret-key keeps the
# old key as a fallback for sessions and password links (--finish drops it; at once if
# the key leaked). Every step is recorded in the audit log (keys.rotate) and shows on
# the admin's System page. New values are generated here and written to .env (mode
# 600) or secrets/, never printed, never passed on a command line.
set -Eeuo pipefail

KIND="" FINISH=0 ASSUME_YES=0 DEV=0 INSTALL_DIR="${INSTALL_DIR:-}"
# The services that read the rotated values (ROTATE_APP_SERVICES overrides, for tests).
read -r -a APP_SERVICES <<<"${ROTATE_APP_SERVICES:-web worker beat watcher transcoder live}"

die() { printf 'rotate-keys: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --finish) FINISH=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    --dev) DEV=1 ;;
    --dir) INSTALL_DIR="${2:?--dir needs a value}"; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) [[ -z "$KIND" ]] || die "one kind at a time"; KIND="$1" ;;
  esac
  shift
done
[[ -n "$KIND" ]] || { usage >&2; exit 2; }

if [[ -z "$INSTALL_DIR" && -r /etc/smart-iptv/install.conf ]]; then
  INSTALL_DIR="$(sed -n 's/^INSTALL_DIR=//p' /etc/smart-iptv/install.conf)"
fi
INSTALL_DIR="${INSTALL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$INSTALL_DIR"
[[ -f .env ]] || die "no .env in $INSTALL_DIR"
umask 077

get_env() { sed -n "s/^$1=//p" .env | tail -n 1 | sed "s/^'//; s/'\$//"; }

# set_env KEY VALUE: replace or append; the value travels in the environment only.
set_env() {
  ENV_KEY="$1" ENV_VALUE="$2" awk '
    BEGIN { k = ENVIRON["ENV_KEY"]; v = ENVIRON["ENV_VALUE"]; done = 0 }
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env > .env.new
  cat .env.new > .env
  rm -f .env.new
}

on() { case "$(get_env "$1")" in 1 | yes | true) return 0 ;; esac; return 1; }

compose_args() {
  local files=(-f docker/compose.yml)
  if ((DEV)); then
    files+=(-f docker/compose.dev.yml)
    on MONITORING && files+=(-f docker/compose.monitoring.yml -f docker/compose.monitoring.dev.yml)
  else
    files+=(-f docker/compose.prod.yml)
    on MONITORING && files+=(-f docker/compose.monitoring.yml -f docker/compose.monitoring.prod.yml)
    on LIVE_INGEST && files+=(-f docker/compose.mediamtx.yml)
  fi
  on CROWDSEC && files+=(-f docker/compose.crowdsec.yml)
  # shellcheck disable=SC2206 # COMPOSE_EXTRA is a list of -f arguments by design
  files+=(${COMPOSE_EXTRA:-})
  printf '%s\n' "${files[@]}"
}
mapfile -t FILES < <(compose_args)
dc() { docker compose --project-directory . "${FILES[@]}" "$@"; }
manage() { dc exec -T web python manage.py "$@"; }
record() { manage key_rotation record "$@" >/dev/null; }

confirm() {
  ((ASSUME_YES)) && return 0
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "${answer,,}" == y || "${answer,,}" == yes ]] || die "nothing changed"
}

hex_secret() { openssl rand -hex 32; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

recreate_apps() {
  say "Restarting the app services with the new values..."
  dc up -d --no-deps --wait "${APP_SERVICES[@]}" >/dev/null
}

# --- media-token -----------------------------------------------------------------------------
# The key file is written in place (same inode): Docker secrets are bind mounts of the
# file, and Django re-reads it when it changes; every edge reloads.
media_step() { # stage | promote | retire
  local keys=secrets/media_token_keys.json new
  new="$(mktemp secrets/.media_token_keys.XXXXXX)"
  manage key_rotation media-keys "$1" < "$keys" > "$new" || { rm -f "$new"; die "media-keys $1 failed"; }
  cat "$new" > "$keys"
  rm -f "$new"
  dc exec -T nginx-stream nginx -s reload >/dev/null 2>&1
}

rotate_media_token() {
  if ((FINISH)); then
    manage key_rotation media-token-finish || die "too early: run it again after the time above"
    media_step retire
    record media_token retired
    say "Done: the previous media key is gone."
    return
  fi
  confirm "Add a new media token key and sign with it (the old one keeps working)?"
  media_step stage
  record media_token staged
  sleep 5 # every edge has reloaded and accepts the new key before Django signs with it
  media_step promote
  record media_token signing
  manage key_rotation media-token-finish || true
  say "Next: scripts/rotate-keys.sh media-token --finish once the time above has passed."
}

# --- secret-key ----------------------------------------------------------------------------------
rotate_secret_key() {
  if ((FINISH)); then
    set_env DJANGO_SECRET_KEY_FALLBACKS ""
    recreate_apps
    record secret_key finished
    say "Done: the previous Django secret key no longer verifies anything."
    return
  fi
  confirm "Replace DJANGO_SECRET_KEY (the old one stays as a fallback until --finish)?"
  local old
  old="$(get_env DJANGO_SECRET_KEY)"
  set_env DJANGO_SECRET_KEY_FALLBACKS "$old"
  set_env DJANGO_SECRET_KEY "$(hex_secret)"
  recreate_apps
  record secret_key started
  say "Next: scripts/rotate-keys.sh secret-key --finish in a month (portal sessions last up"
  say "to 30 days), or at once if the old key leaked (everyone signs in again)."
}

# --- field-encryption ------------------------------------------------------------------------
rotate_field_encryption() {
  confirm "Replace FIELD_ENCRYPTION_KEY and re-encrypt every stored secret?"
  local old new
  old="$(get_env FIELD_ENCRYPTION_KEY)"
  new="$(fernet_key)"
  set_env FIELD_ENCRYPTION_KEY "$new,$old"
  recreate_apps
  manage key_rotation reencrypt
  manage key_rotation check-first-key ||
    die "some values still need the old key; FIELD_ENCRYPTION_KEY keeps both. See the runbook."
  set_env FIELD_ENCRYPTION_KEY "$new"
  recreate_apps
  record field_encryption completed
  say "Done: every stored secret is encrypted with the new key, and the old key is gone."
}

# --- database passwords ----------------------------------------------------------------------
rotate_db() { # role
  local role="$1" variable new
  case "$role" in
    app) variable=POSTGRES_APP_PASSWORD ;;
    reporting) variable=POSTGRES_REPORTING_PASSWORD ;;
    monitor) variable=POSTGRES_MONITOR_PASSWORD ;;
    owner) variable=POSTGRES_PASSWORD ;;
  esac
  confirm "Change the database password of the $role role?"
  new="$(hex_secret)"
  case "$role" in
    app | reporting)
      set_env "$variable" "$new"
      dc run --rm --no-deps db-roles >/dev/null # ALTER ROLE ... PASSWORD from .env
      ;;
    monitor)
      set_env "$variable" "$new"
      scripts/secrets.sh >/dev/null
      if on MONITORING; then dc up -d --no-deps --force-recreate postgres-monitor-init postgres-exporter >/dev/null; fi
      ;;
    owner)
      # shellcheck disable=SC2016 # expanded inside the container
      NEW_PASSWORD="$new" dc exec -T -e NEW_PASSWORD postgres sh -c \
        'psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL' >/dev/null
\getenv new_password NEW_PASSWORD
SELECT format('ALTER ROLE %I PASSWORD %L', current_user, :'new_password') \gexec
SQL
      set_env "$variable" "$new"
      scripts/secrets.sh >/dev/null
      ;;
  esac
  recreate_apps
  record db completed --detail "role=$role"
  say "Done: the $role role has a new password (in .env)."
}

# --- Valkey passwords ------------------------------------------------------------------------
# The new password is added to the running user first, so clients that reconnect with
# either keep working; then the consumers restart with it, and the Valkeys restart once
# (a second-long reconnect) so their ACL file holds only the new one.
valkey_cli() { # store: commands on stdin, as the default user (VALKEYCLI_AUTH)
  dc exec -T "$1" sh -c 'valkey-cli --no-auth-warning >/dev/null'
}

rotate_valkey() { # user
  local user="$1" store prefix variable new
  confirm "Change the Valkey password of the $user user (both instances)?"
  for store in redis-state redis-cache; do
    prefix="$([[ "$store" == redis-state ]] && echo REDIS_STATE || echo REDIS_CACHE)"
    case "$user" in
      app) variable="${prefix}_APP_PASSWORD" ;;
      exporter) variable="${prefix}_EXPORTER_PASSWORD" ;;
      default) variable="${prefix}_PASSWORD" ;;
    esac
    new="$(hex_secret)"
    printf 'ACL SETUSER %s >%s\n' "$user" "$new" | valkey_cli "$store"
    set_env "$variable" "$new"
  done
  scripts/secrets.sh >/dev/null
  case "$user" in
    app) recreate_apps ;;
    exporter) if on MONITORING; then dc up -d --no-deps --force-recreate redis-exporter >/dev/null; fi ;;
    default) ;;
  esac
  dc up -d --no-deps --wait redis-state redis-cache >/dev/null
  [[ "$user" != default ]] || recreate_apps
  record valkey completed --detail "user=$user"
  say "Done: the $user user has a new password on both Valkeys (in .env)."
}

case "$KIND" in
  media-token) rotate_media_token ;;
  secret-key) rotate_secret_key ;;
  field-encryption) rotate_field_encryption ;;
  db-app) rotate_db app ;;
  db-reporting) rotate_db reporting ;;
  db-monitor) rotate_db monitor ;;
  db-owner) rotate_db owner ;;
  valkey-app) rotate_valkey app ;;
  valkey-exporter) rotate_valkey exporter ;;
  valkey-default) rotate_valkey default ;;
  *) die "unknown kind $KIND (see --help)" ;;
esac
