#!/usr/bin/env bash
# Smart IPTV installer and updater for one server (the small tier, docs/runbooks/deploy.md).
#
#   Fresh server (Ubuntu 22.04/24.04 or Debian 12/13), as root or with sudo:
#     curl -fsSL https://raw.githubusercontent.com/mah007/iptv/main/scripts/install.sh -o install.sh
#     sudo bash install.sh
#   From a checkout:          sudo scripts/install.sh
#   Update an installation:   sudo /opt/iptv/scripts/install.sh --update
#   A media edge on another server (admin → Edges → Add shows the command):
#     sudo bash install.sh --edge --control-url https://edges.tv.example.com \
#       --edge-id edge-2 --edge-domain media2.example.com
#   All options:              scripts/install.sh --help
#
# It asks for the domain, the admin's email and sign-in name, the media folder, the
# optional TMDB and SMTP settings and the monitoring overlay, and checks the server (OS, CPU, memory, disk, ports
# 80/443, DNS). Then it installs Docker if needed, gets the code, writes .env with fresh
# secrets (bootstrap only: domain, folders, passwords of the data stores), builds and
# starts the stack, saves the optional answers in the admin's settings (where they stay
# editable: Settings → Integrations), creates the owner admin and the default libraries,
# and checks HTTPS on every host. Run again on an installed server, it offers to update
# or to change the domain, email, media folder or monitoring. An update replaces the web
# service replica by replica behind Traefik's health checks, so the site keeps answering.
#
# With --edge it installs a standalone media edge instead (ADR-0022,
# docs/runbooks/add-edge.md): nginx with its own Let's Encrypt certificate, serving the
# renditions published to the S3 origin, signed in to the control plane with its secret.
#
# Secrets are never taken from the command line (other users can read it): pass them in
# environment variables (ADMIN_PASSWORD, TMDB_TOKEN, SMTP_PASSWORD, TELEGRAM_BOT_TOKEN,
# EDGE_TOKEN) or type them when asked. Nothing secret is written to the log.
set -Eeuo pipefail

readonly DEFAULT_REPO="https://github.com/mah007/iptv.git"
readonly DEFAULT_DIR="/opt/iptv"
readonly LOG_FILE="/var/log/smart-iptv-install.log"
readonly CONF_DIR="/etc/smart-iptv"
readonly CREDENTIALS_FILE="/root/smart-iptv-admin.txt"
# The platform's needs; check_resources uses the edge's (EDGE_MIN_*) with --edge.
MIN_CPUS=2 MIN_RAM_MB=3500 MIN_DISK_GB=25
GOOD_CPUS=4 GOOD_RAM_MB=7500 GOOD_DISK_GB=60
readonly EDGE_MIN="1 900 20" EDGE_GOOD="2 1900 80"

# --- Answers: options and environment variables fill these; questions fill the rest. ----
MODE=""                       # install | update | reconfigure
ROLE=""                       # "" (the platform) | edge: a standalone media edge (--edge)
ASSUME_YES=0
DRY_RUN=0
SKIP_DNS_CHECK=0
INSTALL_DIR="${INSTALL_DIR:-}"
REPO_URL="${REPO_URL:-$DEFAULT_REPO}"
BRANCH="${BRANCH:-main}"
DOMAIN="${DOMAIN:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
MEDIA_ROOT="${MEDIA_ROOT:-}"
SAMPLE_MEDIA="${SAMPLE_MEDIA:-}"          # yes | no
TMDB_TOKEN="${TMDB_TOKEN:-}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
SMTP_SECURITY="${SMTP_SECURITY:-}"        # starttls | ssl | none
SMTP_FROM="${SMTP_FROM:-}"
FIREWALL="${FIREWALL:-}"                  # yes | no
INSTALL_DOCKER="${INSTALL_DOCKER:-}"      # yes | no
MONITORING="${MONITORING:-}"              # yes | no: Grafana, Prometheus, Loki, alerts (ADR-0018)
ALERT_EMAIL="${ALERT_EMAIL:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
# Backups (ADR-0020): where, and an optional S3-compatible bucket (keys from the environment).
BACKUP_DIR="${BACKUP_DIR:-}"
BACKUP_MEDIA="${BACKUP_MEDIA:-}"          # yes | no: also the media libraries
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_REGION="${S3_REGION:-}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-}"
S3_PATH_STYLE="${S3_PATH_STYLE:-}"        # yes | no
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
CROWDSEC="${CROWDSEC:-}"                  # yes | no: CrowdSec and its Traefik bouncer
CROWDSEC_ONLINE="${CROWDSEC_ONLINE:-}"    # yes | no: the community blocklist
SSH_FROM="${SSH_FROM:-}"                  # with --firewall: SSH only from these networks
REPLICA="${REPLICA:-}"                    # yes | no: the PostgreSQL read replica (ADR-0022)
# A standalone media edge (--edge, ADR-0022): the id and secret come from admin → Edges.
CONTROL_URL="${CONTROL_URL:-}"            # the control plane's edges host, https://edges.<domain>
EDGE_ID="${EDGE_ID:-}"
EDGE_DOMAIN="${EDGE_DOMAIN:-}"            # this edge's own name, e.g. media2.example.com
EDGE_TOKEN="${EDGE_TOKEN:-}"              # its secret: environment or hidden input only
EDGE_CACHE_SIZE="${EDGE_CACHE_SIZE:-}"    # the slice cache, e.g. 50g
ORIG_ARGS=()
BACKUP_PASSPHRASE_FILE="/root/smart-iptv-backup-passphrase.txt"
readonly DEFAULT_BACKUP_DIR="/var/backups/smart-iptv"

# --- Output -------------------------------------------------------------------------------
if [[ -t 1 ]]; then
  B=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GREEN=$'\e[32m' YELLOW=$'\e[33m' CYAN=$'\e[36m' R=$'\e[0m'
else
  B="" DIM="" RED="" GREEN="" YELLOW="" CYAN="" R=""
fi
STEP=0
log() { [[ -w "${LOG_FILE%/*}" || -w "$LOG_FILE" ]] && printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG_FILE" 2>/dev/null || true; }
say() { printf '%s\n' "$*"; log "$*"; }
info() { printf '  %s\n' "$*"; log "$*"; }
ok() { printf '  %s✓%s %s\n' "$GREEN" "$R" "$*"; log "ok: $*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$R" "$*" >&2; log "warning: $*"; }
die() { printf '\n%s✗ %s%s\n' "$RED" "$*" "$R" >&2; log "error: $*"; exit 1; }
step() { STEP=$((STEP + 1)); printf '\n%s%s%d. %s%s\n' "$B" "$CYAN" "$STEP" "$*" "$R"; log "== $*"; }
trap 'die "Stopped at line $LINENO. The log is $LOG_FILE."' ERR

usage() {
  cat <<EOF
Smart IPTV installer: install, update or reconfigure on one server.

Usage: sudo scripts/install.sh [options]

Modes (asked when there's already an installation):
  --update               pull the latest code and update without downtime (web replicas
                         are replaced one set at a time); keeps every setting
  --reconfigure          change the domain, admin email, media folder or monitoring
                         (TMDB, email, payments and alerts: admin → Settings → Integrations)
  --edge                 install (or, with --update/--reconfigure, maintain) a standalone
                         media edge on this server instead of the platform (see below)

Answers (otherwise asked):
  --dir DIR              where the code lives (default $DEFAULT_DIR, or this checkout)
  --repo URL             git repository (default $DEFAULT_REPO)
  --branch NAME          git branch (default main)
  --domain NAME          base domain, e.g. tv.example.com: IPTV apps use it, and
                         admin., app., api. and media. are subdomains of it
  --email ADDRESS        the owner admin's email, also the Let's Encrypt contact
  --admin-user NAME      the owner admin's sign-in name (default admin)
  --media-root DIR       host folder with the media libraries (default /srv/media)
  --sample-media         add legal synthetic sample titles (about 20 MB) to try it out
  --no-sample-media
  --smtp-host HOST       SMTP server for emails (optional); also --smtp-port PORT,
                         --smtp-user USER, --smtp-security starttls|ssl|none, --smtp-from ADDR
  --firewall | --no-firewall      allow SSH, 80 and 443 in ufw and enable it
  --install-docker | --no-install-docker
  --monitoring | --no-monitoring  Grafana, Prometheus, Loki and alerts on grafana.<domain>
                         (about 1.5-2 GB of memory; default on with 8 GB or more). With
                         --update, --monitoring turns it on for an existing installation
  --alert-email ADDRESS  where alerts are emailed (default: the admin email)
  --backup-dir DIR       where backups are kept on this server (default $DEFAULT_BACKUP_DIR;
                         another disk is better). Schedule and retention: admin → Settings → Backups
  --backup-media | --no-backup-media   also back up the media libraries (default no)
  --s3-endpoint URL      also copy backups to an S3-compatible bucket: its address, with
                         --s3-bucket NAME, optionally --s3-region R, --s3-prefix P, --s3-path-style
                         (keys: S3_ACCESS_KEY and S3_SECRET_KEY in the environment)
  --crowdsec | --no-crowdsec      CrowdSec on Traefik's logs and its bouncer (default on)
  --crowdsec-online | --no-crowdsec-online   CrowdSec's community blocklist (default off;
                         it shares the attacking addresses this server sees with CrowdSec)
  --ssh-from CIDRS       with --firewall: allow SSH only from these networks (comma-separated)
  --replica | --no-replica   a PostgreSQL read replica for reports and as a failover copy
                         (about 1 GB of memory and a second copy of the database on disk;
                         default off; docs/runbooks/database-replica.md)

A standalone media edge (--edge; add it first in admin → Edges, which shows its secret once):
  --control-url URL      the control plane's edges host, e.g. https://edges.tv.example.com
  --edge-id ID           the edge's id from admin → Edges
  --edge-domain NAME     this server's name for players, e.g. media2.example.com (its DNS
                         A record points here; Let's Encrypt issues its certificate)
  --email ADDRESS        the Let's Encrypt contact (optional)
  --edge-cache-size SIZE the media cache on this disk, e.g. 50g (default 50g)
  --media-root DIR       for an edge with "disk" storage: the full copy of the renditions

Other:
  -y, --yes              unattended: no questions; defaults for whatever isn't given
  --skip-dns-check       don't stop when the DNS records don't point at this server
  --dry-run              ask and check everything, then show the plan; change nothing
  -h, --help             this help

Secrets come from environment variables, never options:
  ADMIN_PASSWORD   the owner admin's password (12+ characters); generated if empty
  TMDB_TOKEN       TMDB API read access token (v4) for metadata; optional
  SMTP_PASSWORD    the SMTP password; optional
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   a Telegram bot and chat for alerts; optional
  S3_ACCESS_KEY, S3_SECRET_KEY           the backup bucket's keys; optional
  EDGE_TOKEN       a standalone edge's secret (admin → Edges); asked if empty

Example (unattended):
  sudo ADMIN_PASSWORD='...' TMDB_TOKEN='...' scripts/install.sh --yes \\
    --domain tv.example.com --email ops@example.com --sample-media
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --update) MODE=update ;;
      --reconfigure) MODE=reconfigure ;;
      --dir) INSTALL_DIR="${2:?--dir needs a value}"; shift ;;
      --repo) REPO_URL="${2:?--repo needs a value}"; shift ;;
      --branch) BRANCH="${2:?--branch needs a value}"; shift ;;
      --domain) DOMAIN="${2:?--domain needs a value}"; shift ;;
      --email) ADMIN_EMAIL="${2:?--email needs a value}"; shift ;;
      --admin-user) ADMIN_USER="${2:?--admin-user needs a value}"; shift ;;
      --media-root) MEDIA_ROOT="${2:?--media-root needs a value}"; shift ;;
      --sample-media) SAMPLE_MEDIA=yes ;;
      --no-sample-media) SAMPLE_MEDIA=no ;;
      --smtp-host) SMTP_HOST="${2:?--smtp-host needs a value}"; shift ;;
      --smtp-port) SMTP_PORT="${2:?--smtp-port needs a value}"; shift ;;
      --smtp-user) SMTP_USER="${2:?--smtp-user needs a value}"; shift ;;
      --smtp-security) SMTP_SECURITY="${2:?--smtp-security needs a value}"; shift ;;
      --smtp-from) SMTP_FROM="${2:?--smtp-from needs a value}"; shift ;;
      --firewall) FIREWALL=yes ;;
      --no-firewall) FIREWALL=no ;;
      --install-docker) INSTALL_DOCKER=yes ;;
      --no-install-docker) INSTALL_DOCKER=no ;;
      --monitoring) MONITORING=yes ;;
      --no-monitoring) MONITORING=no ;;
      --alert-email) ALERT_EMAIL="${2:?--alert-email needs a value}"; shift ;;
      --backup-dir) BACKUP_DIR="${2:?--backup-dir needs a value}"; shift ;;
      --backup-media) BACKUP_MEDIA=yes ;;
      --no-backup-media) BACKUP_MEDIA=no ;;
      --s3-endpoint) S3_ENDPOINT="${2:?--s3-endpoint needs a value}"; shift ;;
      --s3-region) S3_REGION="${2:?--s3-region needs a value}"; shift ;;
      --s3-bucket) S3_BUCKET="${2:?--s3-bucket needs a value}"; shift ;;
      --s3-prefix) S3_PREFIX="${2:?--s3-prefix needs a value}"; shift ;;
      --s3-path-style) S3_PATH_STYLE=yes ;;
      --crowdsec) CROWDSEC=yes ;;
      --no-crowdsec) CROWDSEC=no ;;
      --crowdsec-online) CROWDSEC_ONLINE=yes ;;
      --no-crowdsec-online) CROWDSEC_ONLINE=no ;;
      --ssh-from) SSH_FROM="${2:?--ssh-from needs a value}"; shift ;;
      --replica) REPLICA=yes ;;
      --no-replica) REPLICA=no ;;
      --edge) ROLE=edge ;;
      --control-url) CONTROL_URL="${2:?--control-url needs a value}"; shift ;;
      --edge-id) EDGE_ID="${2:?--edge-id needs a value}"; shift ;;
      --edge-domain) EDGE_DOMAIN="${2:?--edge-domain needs a value}"; shift ;;
      --edge-cache-size) EDGE_CACHE_SIZE="${2:?--edge-cache-size needs a value}"; shift ;;
      -y | --yes) ASSUME_YES=1 ;;
      --skip-dns-check) SKIP_DNS_CHECK=1 ;;
      --dry-run) DRY_RUN=1 ;;
      -h | --help) usage; exit 0 ;;
      *) usage >&2; die "Unknown option: $1" ;;
    esac
    shift
  done
}

# --- Questions ----------------------------------------------------------------------------
# Answers are read from the terminal even when the script itself arrives on stdin.
open_terminal() {
  if ((ASSUME_YES)); then return; fi
  if [[ -t 0 ]]; then
    exec 3<&0
  elif { exec 3</dev/tty; } 2>/dev/null; then
    :
  elif [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    exec 3<&0 # answers piped into a script file (not `curl | bash`, where stdin is the script)
  else
    die "No terminal to ask questions on. Run it from a terminal, or pass --yes with the answers."
  fi
}

# ask VAR "question" "default" [validator]: keeps a preset answer if it's valid.
ask() {
  local var="$1" question="$2" default="${3-}" validator="${4-}" answer
  if [[ -n "${!var-}" ]]; then
    if [[ -z "$validator" ]] || "$validator" "${!var}"; then return 0; fi
    ((ASSUME_YES)) && die "Invalid value for $question: ${!var}"
  fi
  if ((ASSUME_YES)); then
    [[ -n "$default" ]] || die "No answer for \"$question\": pass it as an option (see --help)."
    if [[ -n "$validator" ]] && ! "$validator" "$default"; then die "Invalid default for $question."; fi
    printf -v "$var" '%s' "$default"
    return 0
  fi
  while true; do
    if [[ -n "$default" ]]; then
      printf '  %s%s%s %s[%s]%s: ' "$B" "$question" "$R" "$DIM" "$default" "$R"
    else
      printf '  %s%s%s: ' "$B" "$question" "$R"
    fi
    IFS= read -r answer <&3 || die "No answer (end of input)."
    answer="${answer#"${answer%%[![:space:]]*}"}"
    answer="${answer%"${answer##*[![:space:]]}"}"
    answer="${answer:-$default}"
    if [[ -z "$answer" ]]; then warn "An answer is needed."; continue; fi
    if [[ -n "$validator" ]] && ! "$validator" "$answer"; then continue; fi
    printf -v "$var" '%s' "$answer"
    return 0
  done
}

# ask_optional VAR "question": empty is allowed and means "skip".
ask_optional() {
  local var="$1" question="$2" answer
  [[ -n "${!var-}" ]] && return 0
  ((ASSUME_YES)) && return 0
  printf '  %s%s%s %s[skip]%s: ' "$B" "$question" "$R" "$DIM" "$R"
  IFS= read -r answer <&3 || die "No answer (end of input)."
  printf -v "$var" '%s' "${answer//[[:space:]]/}"
}

# ask_secret VAR "question" [confirm]: hidden input; empty means skip unless confirm.
ask_secret() {
  local var="$1" question="$2" confirm="${3-}" first second
  [[ -n "${!var-}" ]] && return 0
  ((ASSUME_YES)) && return 0
  while true; do
    printf '  %s%s%s: ' "$B" "$question" "$R"
    IFS= read -rs first <&3 || die "No answer (end of input)."
    printf '\n'
    if [[ -z "$confirm" ]]; then printf -v "$var" '%s' "$first"; return 0; fi
    [[ -z "$first" ]] && return 0
    valid_password "$first" || continue
    printf '  %sAgain, to confirm%s: ' "$B" "$R"
    IFS= read -rs second <&3 || die "No answer (end of input)."
    printf '\n'
    if [[ "$first" != "$second" ]]; then warn "They don't match; try again."; continue; fi
    printf -v "$var" '%s' "$first"
    return 0
  done
}

# yes_no VAR "question" default(yes|no)
yes_no() {
  local var="$1" question="$2" default="$3" answer hint
  case "${!var-}" in yes | no) return 0 ;; esac
  if ((ASSUME_YES)); then printf -v "$var" '%s' "$default"; return 0; fi
  [[ "$default" == yes ]] && hint="Y/n" || hint="y/N"
  while true; do
    printf '  %s%s%s %s[%s]%s: ' "$B" "$question" "$R" "$DIM" "$hint" "$R"
    IFS= read -r answer <&3 || die "No answer (end of input)."
    case "${answer,,}" in
      "") printf -v "$var" '%s' "$default"; return 0 ;;
      y | yes) printf -v "$var" yes; return 0 ;;
      n | no) printf -v "$var" no; return 0 ;;
      *) warn "Please answer y or n." ;;
    esac
  done
}

# --- Validators (print why, return 1) ----------------------------------------------------------
valid_domain() {
  local d="${1,,}"
  if [[ ! "$d" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then
    warn "Enter a domain name such as tv.example.com (no http://, no slash)."; return 1
  fi
  if [[ "$d" =~ ^(admin|app|api|media|www)\. ]]; then
    warn "Enter the base name (e.g. tv.example.com); admin., app., api. and media. are added to it."; return 1
  fi
  return 0
}
valid_email() {
  [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] && return 0
  warn "Enter an email address such as ops@example.com."; return 1
}
valid_username() {
  [[ "$1" =~ ^[A-Za-z0-9._@+-]{3,150}$ ]] && return 0
  warn "3 to 150 letters, digits or . _ @ + -"; return 1
}
valid_password() {
  if ((${#1} < 12)); then warn "Use at least 12 characters."; return 1; fi
  if [[ "$1" =~ ^[0-9]+$ ]]; then warn "Not only digits."; return 1; fi
  if [[ "$1" == *"'"* ]]; then warn "Single quotes aren't allowed."; return 1; fi
  return 0
}
valid_dir() {
  if [[ "$1" != /* || "$1" == "/" || "$1" =~ [[:space:]\'\"] ]]; then
    warn "Enter an absolute folder path without spaces, e.g. /srv/media."; return 1
  fi
  return 0
}
valid_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] && (($1 >= 1 && $1 <= 65535)) && return 0
  warn "Enter a port number."; return 1
}
valid_security() {
  case "$1" in starttls | ssl | none) return 0 ;; esac
  warn "Answer starttls, ssl or none."; return 1
}
valid_telegram_token() {
  [[ -z "$1" || "$1" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{30,}$ ]] && return 0
  warn "A bot token looks like 123456789:AA... (from @BotFather)."; return 1
}
valid_chat_id() {
  [[ "$1" =~ ^-?[0-9]{1,20}$ ]] && return 0
  warn "A chat id is a number, e.g. 123456789 or -1001234567890."; return 1
}
valid_s3_endpoint() {
  [[ "$1" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?/?$ ]] && return 0
  warn "Enter the service's address, e.g. https://s3.eu-central-1.amazonaws.com."; return 1
}
valid_bucket() {
  [[ "$1" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] && return 0
  warn "A bucket name has 3 to 63 lowercase letters, digits, dots or dashes."; return 1
}
valid_networks() {
  local part
  for part in ${1//,/ }; do
    [[ "$part" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] || { warn "$part is not an address or network (e.g. 203.0.113.0/24)."; return 1; }
  done
  return 0
}
valid_control_url() {
  [[ "$1" =~ ^https://[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:[0-9]{1,5})?/?$ ]] && return 0
  warn "Enter the control plane's edges address, e.g. https://edges.tv.example.com."; return 1
}
valid_edge_id() {
  [[ "$1" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] && return 0
  warn "The edge id from admin → Edges: letters, digits, . _ or -."; return 1
}
valid_edge_domain() {
  local d="${1,,}"
  [[ "$d" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] && return 0
  warn "Enter this server's name such as media2.example.com (no https://, no slash)."; return 1
}
valid_edge_token() {
  [[ "$1" =~ ^[A-Za-z0-9_-]{32,128}$ ]] && return 0
  warn "That isn't an edge secret: copy it from admin → Edges (shown once when added or rotated)."; return 1
}
valid_cache_size() {
  [[ "$1" =~ ^[1-9][0-9]{0,5}[kKmMgG]$ ]] && return 0
  warn "Enter a size such as 50g."; return 1
}
valid_env_value() {
  [[ "$1" != *"'"* && "$1" != *$'\n'* ]] && return 0
  warn "Single quotes and line breaks aren't allowed."; return 1
}

# --- Helpers ----------------------------------------------------------------------------------
# run "what" command...: runs with output to the log; prints how long it took.
run() {
  local what="$1" start rc
  shift
  if ((DRY_RUN)); then info "${DIM}would run:${R} $*"; return 0; fi
  start=$(date +%s)
  printf '  %s… ' "$what"
  log "run: $*"
  set +e
  "$@" >> "$LOG_FILE" 2>&1
  rc=$?
  set -e
  if ((rc != 0)); then
    printf '%sfailed%s\n' "$RED" "$R"
    printf '%s--- last lines of %s ---%s\n' "$DIM" "$LOG_FILE" "$R" >&2
    tail -n 25 "$LOG_FILE" >&2 || true
    die "$what failed (exit $rc)."
  fi
  printf '%sdone%s %s(%ss)%s\n' "$GREEN" "$R" "$DIM" "$(($(date +%s) - start))" "$R"
}

# The monitoring overlay (ADR-0018) is on when this run says so, else when .env says so.
monitoring_enabled() {
  case "$MONITORING" in yes) return 0 ;; no) return 1 ;; esac
  case "$(get_env MONITORING)" in 1 | yes | true) return 0 ;; esac
  return 1
}

# CrowdSec (ADR-0020) is on when this run says so, else when .env says so.
crowdsec_enabled() {
  case "$CROWDSEC" in yes) return 0 ;; no) return 1 ;; esac
  case "$(get_env CROWDSEC)" in 1 | yes | true) return 0 ;; esac
  return 1
}

# The read replica (ADR-0022) is on when this run says so, else when .env says so.
replica_enabled() {
  case "$REPLICA" in yes) return 0 ;; no) return 1 ;; esac
  case "$(get_env REPLICA)" in 1 | yes | true) return 0 ;; esac
  return 1
}

# The compose files, relative to the installation (for messages) or absolute.
compose_files() {
  local prefix="${1-}"
  if [[ "$ROLE" == edge ]]; then printf -- '-f %sdocker/compose.edge.yml' "$prefix"; return 0; fi
  printf -- '-f %sdocker/compose.yml -f %sdocker/compose.prod.yml' "$prefix" "$prefix"
  if monitoring_enabled; then
    printf -- ' -f %sdocker/compose.monitoring.yml -f %sdocker/compose.monitoring.prod.yml' "$prefix" "$prefix"
  fi
  if crowdsec_enabled; then printf -- ' -f %sdocker/compose.crowdsec.yml' "$prefix"; fi
  if live_ingest_enabled; then printf -- ' -f %sdocker/compose.mediamtx.yml' "$prefix"; fi
  if replica_enabled; then
    printf -- ' -f %sdocker/compose.replica.yml' "$prefix"
    if monitoring_enabled; then printf -- ' -f %sdocker/compose.replica.monitoring.yml' "$prefix"; fi
  fi
}

# Web replicas in production (docker/compose.prod.yml's default, or WEB_REPLICAS in .env).
web_replicas() {
  local n
  n="$(get_env WEB_REPLICAS)"
  [[ "$n" =~ ^[1-9][0-9]?$ ]] || n=2
  printf '%s' "$n"
}

# The optional MediaMTX ingest for the operator's own encoders (ADR-0017,
# docs/runbooks/live-tv.md): LIVE_INGEST=1 in .env. scripts/secrets.sh generates its users.
live_ingest_enabled() {
  case "$(get_env LIVE_INGEST)" in 1 | yes | true) return 0 ;; esac
  return 1
}

compose() {
  local files
  read -r -a files <<<"$(compose_files "$INSTALL_DIR/")"
  docker compose --project-directory "$INSTALL_DIR" "${files[@]}" "$@"
}

env_file() { printf '%s/.env' "$INSTALL_DIR"; }

# get_env KEY: the value in .env, quotes removed (the installer's own server config).
get_env() {
  local value
  [[ -f "$(env_file)" ]] || return 0
  value="$(sed -n "s/^$1=//p" "$(env_file)" | tail -n 1)"
  value="${value#\'}"; value="${value%\'}"
  printf '%s' "$value"
}

# set_env KEY VALUE: replace or append the key; the value travels in the environment,
# never on a command line, and is never printed. Quoted when it holds special characters.
set_env() {
  local key="$1" value="$2" file
  file="$(env_file)"
  if ((DRY_RUN)); then info "${DIM}would set${R} $key in .env"; return 0; fi
  valid_env_value "$value" || die "Can't store $key."
  [[ "$value" =~ ^[A-Za-z0-9._:/@+=,%-]*$ ]] || value="'$value'"
  ENV_KEY="$key" ENV_VALUE="$value" awk '
    BEGIN { k = ENVIRON["ENV_KEY"]; v = ENVIRON["ENV_VALUE"]; done = 0 }
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$file.new"
  cat "$file.new" > "$file"
  rm -f "$file.new"
}

hosts() {
  if [[ "$ROLE" == edge ]]; then printf '%s\n' "$EDGE_DOMAIN"; return 0; fi
  printf '%s\n' "$DOMAIN" "admin.$DOMAIN" "app.$DOMAIN" "api.$DOMAIN" "media.$DOMAIN" "edges.$DOMAIN"
  if monitoring_enabled; then printf '%s\n' "grafana.$DOMAIN"; fi
}

ram_mb() { awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo; }

public_ip() {
  curl -4 -fsS --max-time 6 https://api.ipify.org 2>/dev/null ||
    curl -4 -fsS --max-time 6 https://ifconfig.co 2>/dev/null || true
}

# --- Checks -------------------------------------------------------------------------------------
require_root() {
  ((EUID == 0)) && return 0
  ((DRY_RUN)) && { warn "Not root: fine for --dry-run."; return 0; }
  die "Run it as root, e.g.: sudo bash $0 $*"
}

OS_ID="" OS_VERSION="" OS_CODENAME=""
check_os() {
  [[ -r /etc/os-release ]] || die "Can't tell the operating system (/etc/os-release is missing)."
  # shellcheck disable=SC1091
  read -r OS_ID OS_VERSION OS_CODENAME < <(
    . /etc/os-release && printf '%s %s %s\n' "${ID:-none}" "${VERSION_ID:-none}" "${VERSION_CODENAME:-none}"
  )
  case "$OS_ID:$OS_VERSION" in
    ubuntu:22.04 | ubuntu:24.04 | ubuntu:26.04 | debian:12 | debian:13)
      ok "Operating system: $OS_ID $OS_VERSION" ;;
    ubuntu:* | debian:*)
      warn "$OS_ID $OS_VERSION isn't tested; Ubuntu 24.04 is recommended." ;;
    *)
      command -v docker >/dev/null ||
        die "$OS_ID $OS_VERSION isn't supported: use Ubuntu 22.04/24.04 or Debian 12/13 (or install Docker yourself first)."
      warn "$OS_ID $OS_VERSION isn't tested; using the Docker that's already installed." ;;
  esac
  case "$(uname -m)" in
    x86_64 | amd64) ok "CPU architecture: x86_64" ;;
    aarch64 | arm64) warn "ARM64 isn't tested yet; x86_64 is recommended." ;;
    *) die "Unsupported CPU architecture: $(uname -m)." ;;
  esac
}

check_resources() {
  local cpus ram_mb disk_gb target
  if [[ "$ROLE" == edge ]]; then
    read -r MIN_CPUS MIN_RAM_MB MIN_DISK_GB <<<"$EDGE_MIN"
    read -r GOOD_CPUS GOOD_RAM_MB GOOD_DISK_GB <<<"$EDGE_GOOD"
  fi
  cpus="$(nproc)"
  ram_mb="$(ram_mb)"
  target="$INSTALL_DIR"
  while [[ ! -d "$target" ]]; do target="$(dirname "$target")"; done
  disk_gb="$(df -Pk "$target" | awk 'NR == 2 {print int($4 / 1048576)}')"
  report() { # name value unit min good
    if (($2 < $4)); then
      warn "$1: $2 $3; at least $4 $3 is needed."
      return 1
    elif (($2 < $5)); then
      warn "$1: $2 $3 works for a small library; $5 $3 or more is recommended."
    else
      ok "$1: $2 $3"
    fi
  }
  local short=0
  report "CPU" "$cpus" "cores" "$MIN_CPUS" "$GOOD_CPUS" || short=1
  report "Memory" "$ram_mb" "MB" "$MIN_RAM_MB" "$GOOD_RAM_MB" || short=1
  report "Free disk at $target" "$disk_gb" "GB" "$MIN_DISK_GB" "$GOOD_DISK_GB" || short=1
  if ((short)); then
    local cont=""
    yes_no cont "This server is below the minimum. Continue anyway?" no
    [[ "$cont" == yes ]] || die "Stopped: the server is too small."
  fi
}

check_ports() {
  local busy
  busy="$(ss -Hltnp '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v -E 'docker-proxy|traefik' || true)"
  if [[ -n "$busy" ]]; then
    warn "Something else already listens on port 80 or 443:"
    printf '%s\n' "$busy" | sed 's/^/      /' >&2
    die "Stop that web server (e.g. systemctl disable --now apache2 nginx) and run the installer again."
  fi
  ok "Ports 80 and 443 are free"
}

# A second installation on one server would share the first one's containers and
# volumes (the Compose project is always smart-iptv), and fresh secrets can't open old
# database volumes. So a new installation needs a server without either.
check_no_other_install() {
  docker_ready || return 0
  local dir volumes
  if [[ "$ROLE" == edge ]]; then
    # An edge needs ports 80 and 443 to itself, and the platform holds them.
    dir="$(docker ps -a --filter label=com.docker.compose.project=smart-iptv \
      --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u | sed -n 1p)"
    [[ -z "$dir" ]] || die "The Smart IPTV platform runs on this server ($dir): a standalone edge goes on another server."
    dir="$(docker ps -a --filter label=com.docker.compose.project=smart-iptv-edge \
      --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u | sed -n 1p)"
    [[ -z "$dir" ]] || die "An edge already runs on this server from $dir. Update it with: sudo $dir/scripts/install.sh --update"
    ok "No other Smart IPTV installation on this server"
    return 0
  fi
  dir="$(docker ps -a --filter label=com.docker.compose.project=smart-iptv \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u | sed -n 1p)"
  if [[ -n "$dir" ]]; then
    die "Smart IPTV already runs on this server from $dir. Update it with: sudo $dir/scripts/install.sh --update"
  fi
  volumes="$(docker volume ls -q --filter label=com.docker.compose.project=smart-iptv 2>/dev/null | paste -sd ' ' -)"
  if [[ -n "$volumes" ]]; then
    warn "Data volumes from an earlier installation remain: $volumes"
    die "Reuse that installation's .env (copy it to $INSTALL_DIR first), or delete the volumes yourself if their data can go (docker volume rm ...)."
  fi
  ok "No other Smart IPTV installation on this server"
}

check_internet() {
  curl -fsS --max-time 10 -o /dev/null https://github.com ||
    die "No HTTPS access to github.com: the server needs internet access to install."
  ok "Internet access"
}

check_dns() {
  local ip host resolved bad=0
  ((SKIP_DNS_CHECK)) && { info "DNS check skipped (--skip-dns-check)."; return 0; }
  ip="$(public_ip)"
  if [[ -z "$ip" ]]; then
    warn "Couldn't find this server's public IP; skipping the DNS check."
    return 0
  fi
  info "This server's public IP: $ip"
  while read -r host; do
    resolved="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd ' ' - || true)"
    if [[ -z "$resolved" ]]; then
      warn "$host doesn't resolve yet."; bad=1
    elif [[ " $resolved " != *" $ip "* ]]; then
      warn "$host points at $resolved, not at this server ($ip)."; bad=1
    else
      ok "$host → $ip"
    fi
  done < <(hosts)
  if ((bad)); then
    if [[ "$ROLE" == edge ]]; then
      info "Create a DNS A record pointing at $ip, then run the installer again:"
      info "  $EDGE_DOMAIN   A  $ip"
    else
      info "Create two DNS A records pointing at $ip, then run the installer again:"
      info "  $DOMAIN        A  $ip"
      info "  *.$DOMAIN      A  $ip"
    fi
    info "Without them Let's Encrypt can't issue certificates (the site still starts, and"
    info "certificates follow automatically once DNS is right)."
    local cont=""
    yes_no cont "Continue without correct DNS?" no
    [[ "$cont" == yes ]] || die "Stopped: fix DNS first (or pass --skip-dns-check)."
  fi
}

# --- Questions for a new installation or a reconfiguration ------------------------------------
gather_answers() {
  local current
  step "Questions"
  say "  Press Enter to keep the value in brackets."
  current="$(get_env DOMAIN)"
  [[ "$current" == localhost ]] && current=""
  info ""
  info "${B}Domain.${R} IPTV apps sign in at the domain itself, and the web apps live on"
  info "its subdomains: admin.<domain> (admin panel), app.<domain> (customer portal),"
  info "api.<domain> and media.<domain>. Point the domain and *.<domain> at this server."
  ask DOMAIN "Domain" "$current" valid_domain
  DOMAIN="${DOMAIN,,}"

  info ""
  info "${B}Owner admin.${R} The first admin account; it enrols an authenticator app"
  info "(TOTP) at first sign-in. The email is also the Let's Encrypt contact."
  ask ADMIN_EMAIL "Admin email" "$(get_env ACME_EMAIL)" valid_email
  if [[ "$MODE" == install ]]; then
    ask ADMIN_USER "Admin sign-in name" "admin" valid_username
    if [[ -z "$ADMIN_PASSWORD" ]] && ((!ASSUME_YES)); then
      info "Type a password (12+ characters), or press Enter to generate a strong one."
      ask_secret ADMIN_PASSWORD "Admin password" confirm
    elif [[ -n "$ADMIN_PASSWORD" ]]; then
      valid_password "$ADMIN_PASSWORD" || die "ADMIN_PASSWORD is too weak."
    fi
  fi

  info ""
  info "${B}Media folder.${R} Your own or licensed films and series go in its movies/"
  info "and series/ folders; it's mounted read-only into the app."
  current="$(get_env MEDIA_ROOT)"
  ask MEDIA_ROOT "Media folder" "${current:-/srv/media}" valid_dir
  if [[ "$MODE" == install ]]; then
    yes_no SAMPLE_MEDIA "Add legal synthetic sample titles to try it out (about 20 MB)?" no
  fi

  # TMDB and SMTP are admin settings (ADR-0019): asked once here for convenience, then
  # edited in the admin (Settings → Integrations), not by --reconfigure.
  if [[ "$MODE" == install ]]; then
    info ""
    info "${B}TMDB (optional).${R} Posters, plots and cast in English and Arabic come from"
    info "TMDB: create a free account, then an API key at themoviedb.org/settings/api and"
    info "copy the \"API Read Access Token\". Without one, titles keep their file names."
    info "You can also set it later in the admin (Settings → Integrations)."
    ask_secret TMDB_TOKEN "TMDB API read access token"

    info ""
    info "${B}Email (optional).${R} Invitations, password resets and invoices are emailed"
    info "through your SMTP server. Skip it now and they wait in the outbox until it's set"
    info "(admin → Settings → Integrations → Email)."
    ask_optional SMTP_HOST "SMTP server (e.g. smtp.example.com)"
    if [[ -n "$SMTP_HOST" ]]; then
      ask SMTP_PORT "SMTP port" "587" valid_port
      ask SMTP_SECURITY "Encryption: starttls, ssl or none" "starttls" valid_security
      ask_optional SMTP_USER "SMTP user name"
      if [[ -n "$SMTP_USER" ]]; then ask_secret SMTP_PASSWORD "SMTP password"; fi
      ask SMTP_FROM "Send emails from" "Smart IPTV <no-reply@$DOMAIN>" valid_env_value
    fi
  fi

  ask_monitoring

  if ! docker_ready; then
    info ""
    info "${B}Docker.${R} The app runs in Docker containers; Docker isn't installed yet."
    yes_no INSTALL_DOCKER "Install Docker from Docker's official repository?" yes
    [[ "$INSTALL_DOCKER" == yes ]] || die "Docker Engine with the Compose plugin is needed."
  fi

  ask_backups
  ask_crowdsec

  if command -v ufw >/dev/null 2>&1; then
    info ""
    info "${B}Firewall.${R} ufw can allow only SSH, HTTP and HTTPS. Say no if another"
    info "firewall (e.g. your cloud provider's) already does this."
    yes_no FIREWALL "Configure and enable ufw?" no
    if [[ "$FIREWALL" == yes ]] && ((!ASSUME_YES)); then
      info "SSH can be limited to your own networks (e.g. 203.0.113.0/24, comma-separated);"
      info "press Enter to allow it from anywhere (rate-limited). Don't lock yourself out."
      ask_optional SSH_FROM "Allow SSH only from"
      [[ -z "$SSH_FROM" ]] || valid_networks "$SSH_FROM" || die "Invalid --ssh-from value."
    fi
  else
    FIREWALL=no
  fi
}

# Backups (ADR-0020): every night, encrypted, here and optionally in an S3 bucket.
ask_backups() {
  local current
  info ""
  info "${B}Backups.${R} Every night the database (with point-in-time recovery), .env, secrets/"
  info "and the artwork are backed up, encrypted, and a restore drill runs every week."
  info "Keep them on another disk if you can, and add an S3-compatible bucket for an offsite copy."
  current="$(get_env BACKUP_DIR)"
  ask BACKUP_DIR "Keep backups in" "${current:-$DEFAULT_BACKUP_DIR}" valid_dir
  yes_no BACKUP_MEDIA "Also back up the media libraries (can need as much space as they take)?" no
  [[ "$MODE" == install ]] || return 0
  if [[ -z "$S3_ENDPOINT" ]] && ((!ASSUME_YES)); then
    info "Offsite copy (optional): the address of an S3-compatible service (AWS S3, Backblaze B2,"
    info "Wasabi, Cloudflare R2, MinIO, ...). Press Enter to skip; it can be set later in the admin."
    ask_optional S3_ENDPOINT "S3 service address"
  fi
  [[ -n "$S3_ENDPOINT" ]] || return 0
  valid_s3_endpoint "$S3_ENDPOINT" || die "Invalid --s3-endpoint value."
  ask S3_BUCKET "Bucket name" "" valid_bucket
  ask_optional S3_REGION "Region (if the service needs one)"
  [[ -n "$S3_PREFIX" ]] || S3_PREFIX=smart-iptv
  ask_secret S3_ACCESS_KEY "Access key id"
  ask_secret S3_SECRET_KEY "Secret access key"
  [[ -n "$S3_ACCESS_KEY" && -n "$S3_SECRET_KEY" ]] || warn "Without both keys the offsite copy stays off until they are set (admin → Settings → Backups)."
}

# CrowdSec (ADR-0020): bans abusive addresses at Traefik; on or off later in the admin.
ask_crowdsec() {
  local current default=yes
  current="$(get_env CROWDSEC)"
  case "$current" in 0 | no | false) [[ "$MODE" == install ]] || default=no ;; esac
  info ""
  info "${B}CrowdSec.${R} Watches Traefik's log and blocks addresses that probe or hammer the"
  info "server (about 150 MB of memory). Blocking can be switched off later in the admin."
  yes_no CROWDSEC "Enable CrowdSec?" "$default"
  [[ "$CROWDSEC" == yes ]] || return 0
  info "The community blocklist adds addresses other CrowdSec users reported; it shares the"
  info "attacking addresses this server sees with CrowdSec in return."
  current="$(get_env CROWDSEC_ONLINE)"
  case "$current" in 1 | yes | true) default=yes ;; *) default=no ;; esac
  yes_no CROWDSEC_ONLINE "Use the community blocklist?" "$default"
}

# Monitoring (ADR-0018): Grafana, Prometheus, Loki and Alertmanager on grafana.<domain>,
# opened from the admin (MFA); alerts by email (the SMTP settings above) and Telegram.
ask_monitoring() {
  local current default=no
  current="$(get_env MONITORING)"
  case "$current" in
    1 | yes | true) default=yes ;;
    "") (($(ram_mb) >= GOOD_RAM_MB)) && default=yes ;;
  esac
  info ""
  info "${B}Monitoring.${R} Dashboards, logs and alerts at grafana.<domain>, opened from the"
  info "admin. Alerts go by email (through the SMTP server above) and, optionally, Telegram."
  yes_no MONITORING "Enable monitoring (Grafana, Prometheus, Loki, alerts; about 1.5-2 GB RAM)?" "$default"
  [[ "$MONITORING" == yes ]] || return 0
  # Recipients and Telegram are admin settings (Settings → Monitoring, → Integrations).
  [[ "$MODE" == install ]] || return 0
  ask ALERT_EMAIL "Email alerts to" "$ADMIN_EMAIL" valid_email
  [[ -n "$SMTP_HOST" ]] || warn "No SMTP server is set, so email alerts stay off until one is (admin → Settings → Integrations)."
  info "Telegram (optional): create a bot with @BotFather, add it to a chat, and get the"
  info "chat's id (e.g. from @userinfobot). Press Enter to skip."
  while true; do
    ask_secret TELEGRAM_BOT_TOKEN "Telegram bot token"
    valid_telegram_token "$TELEGRAM_BOT_TOKEN" && break
    ((ASSUME_YES)) && die "TELEGRAM_BOT_TOKEN doesn't look like a bot token."
    TELEGRAM_BOT_TOKEN=""
  done
  if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
    while true; do
      ask_secret TELEGRAM_CHAT_ID "Telegram chat id"
      valid_chat_id "$TELEGRAM_CHAT_ID" && break
      ((ASSUME_YES)) && die "TELEGRAM_CHAT_ID must be a number."
      TELEGRAM_CHAT_ID=""
    done
  fi
}

show_plan() {
  step "Summary"
  info "Code:          $INSTALL_DIR ($REPO_URL, $BRANCH)"
  info "Domain:        $DOMAIN (+ admin., app., api., media., edges.)"
  info "Owner admin:   ${ADMIN_USER:-unchanged} <$ADMIN_EMAIL>, password $([[ -n "$ADMIN_PASSWORD" ]] && echo "as typed" || echo "generated")"
  info "Media folder:  $MEDIA_ROOT$([[ "$SAMPLE_MEDIA" == yes ]] && echo ", with sample titles")"
  if [[ "$MODE" == install ]]; then
    info "TMDB:          $([[ -n "$TMDB_TOKEN" ]] && echo "token set (saved in the admin's settings)" || echo "not set (file names only; set it in the admin)")"
    info "Email:         $([[ -n "$SMTP_HOST" ]] && echo "$SMTP_HOST:$SMTP_PORT ($SMTP_SECURITY), saved in the admin's settings" || echo "not set (outbox only; set it in the admin)")"
  fi
  info "Firewall:      $([[ "$FIREWALL" == yes ]] && echo "ufw: SSH${SSH_FROM:+ from $SSH_FROM}, 80, 443" || echo "unchanged")"
  info "Backups:       $BACKUP_DIR$([[ "$BACKUP_MEDIA" == yes ]] && echo ", media included")$([[ -n "$S3_ENDPOINT" ]] && echo "; offsite: $S3_BUCKET at $S3_ENDPOINT")"
  info "CrowdSec:      $(crowdsec_enabled && echo "on$([[ "$CROWDSEC_ONLINE" == yes ]] && echo ", with the community blocklist")" || echo "off")"
  info "Read replica:  $(replica_enabled && echo "on (reports read from it; a failover copy)" || echo "off")"
  if monitoring_enabled; then
    info "Monitoring:    grafana.$DOMAIN$([[ -n "$ALERT_EMAIL" ]] && echo "; alerts to $ALERT_EMAIL")$([[ -n "$TELEGRAM_BOT_TOKEN" ]] && echo " and Telegram")"
  else
    info "Monitoring:    off"
  fi
  docker_ready || info "Docker:        will be installed from download.docker.com"
  local go=""
  yes_no go "Go ahead?" yes
  [[ "$go" == yes ]] || die "Nothing was changed."
}

# --- Installation steps -------------------------------------------------------------------------
install_packages() {
  local missing=() pkg
  for pkg in git curl openssl python3 ca-certificates; do
    case "$pkg" in
      ca-certificates) [[ -d /etc/ssl/certs ]] || missing+=("$pkg") ;;
      *) command -v "$pkg" >/dev/null || missing+=("$pkg") ;;
    esac
  done
  ((${#missing[@]})) || { ok "git, curl, openssl and python3 are installed"; return 0; }
  command -v apt-get >/dev/null || die "Install ${missing[*]} first."
  run "Installing ${missing[*]}" env DEBIAN_FRONTEND=noninteractive \
    sh -c "apt-get update -q && apt-get install -yq ${missing[*]}"
}

docker_ready() { command -v docker >/dev/null && docker compose version >/dev/null 2>&1; }

install_docker() {
  if docker_ready; then
    ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?') with Compose $(docker compose version --short 2>/dev/null)"
    return 0
  fi
  yes_no INSTALL_DOCKER "Docker isn't installed. Install it from Docker's official repository?" yes
  [[ "$INSTALL_DOCKER" == yes ]] || die "Docker Engine with the Compose plugin is needed."
  [[ "$OS_ID" == ubuntu || "$OS_ID" == debian ]] || die "Install Docker yourself on $OS_ID."
  run "Adding Docker's apt repository" sh -c "
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q && apt-get install -yq ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS_ID/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$OS_ID $OS_CODENAME stable\" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -q"
  run "Installing Docker Engine and Compose" env DEBIAN_FRONTEND=noninteractive \
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  run "Starting Docker" systemctl enable --now docker
}

# SSH's ports, wherever they come from: sshd's own configuration, sshd or systemd's
# ssh.socket listening (Ubuntu 24.04 starts sshd on demand, so `ss` names systemd), and
# the port this session came in on. Missing one would lock the operator out (G-42).
ssh_ports() {
  # Each probe may fail (no sshd host keys yet, no ss, no socket unit): never fatal here.
  (
    set +eE +o pipefail
    trap - ERR
    {
      sshd -T 2>/dev/null | awk '$1 == "port" {print $2}'
      ss -Hltnp 2>/dev/null | awk '/sshd|"systemd"/ {n = split($4, a, ":"); print a[n]}' |
        while read -r port; do
          if systemctl show ssh.socket -p Listen 2>/dev/null | grep -q ":$port "; then echo "$port"; fi
          ss -Hltnp "( sport = :$port )" 2>/dev/null | grep -q sshd && echo "$port"
        done
      [[ -n "${SSH_CONNECTION:-}" ]] && awk '{print $4}' <<<"$SSH_CONNECTION"
    } | grep -E '^[0-9]+$' | sort -un
    exit 0
  )
}

configure_firewall() {
  [[ "$FIREWALL" == yes ]] || return 0
  local ports port network
  ports="$(ssh_ports)"
  [[ -n "$ports" ]] || ports=22
  run "Denying other incoming connections by default" ufw default deny incoming
  for port in $ports; do
    if [[ -n "$SSH_FROM" ]]; then
      for network in ${SSH_FROM//,/ }; do
        run "Allowing SSH on port $port from $network" ufw allow from "$network" to any port "$port" proto tcp
      done
    else
      # limit: more than 6 connections in 30 s from one address are refused.
      run "Allowing SSH on port $port (rate-limited)" ufw limit "$port/tcp"
    fi
  done
  run "Allowing HTTP and HTTPS" sh -c "ufw allow 80/tcp && ufw allow 443/tcp"
  run "Enabling ufw" ufw --force enable
  info "Docker publishes its ports past ufw: only 80 and 443 (and the live ingest, if on) are published."
}

get_code() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]]; then
      die "$INSTALL_DIR has local changes to tracked files; commit or discard them first."
    fi
    # umask 022: git writes files with the caller's umask, and the images copy them as
    # they are; a stricter umask (077) would leave files the app user can't read.
    # shellcheck disable=SC2016 # $1 expands in the inner shell
    run "Updating the code ($BRANCH)" sh -c 'umask 022 && git -C "$1" pull --ff-only -q' _ "$INSTALL_DIR"
  elif [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    die "$INSTALL_DIR exists and isn't a Smart IPTV checkout; choose another --dir."
  else
    # shellcheck disable=SC2016 # $1..$3 expand in the inner shell
    run "Downloading the code to $INSTALL_DIR" sh -c 'umask 022 && git clone -q --branch "$1" "$2" "$3"' \
      _ "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
  ((DRY_RUN)) || { fix_code_permissions; ok "Code at $(git -C "$INSTALL_DIR" log -1 --format='%h %s' 2>/dev/null)"; }
}

# Tracked files must be world-readable (and their folders traversable): the images copy
# them as they are and run as an unprivileged user. A checkout made or pulled under a
# strict umask breaks that, so repair it here. Only git-tracked paths are touched; .env
# and secrets/ are untracked and keep their modes.
fix_code_permissions() {
  local unreadable
  unreadable="$(git -C "$INSTALL_DIR" ls-files -z | (cd "$INSTALL_DIR" && xargs -0 -r find 2>/dev/null) |
    (cd "$INSTALL_DIR" && xargs -r -d '\n' stat -c '%a %n' 2>/dev/null) | awk '$1 !~ /[4-7]$/' | wc -l)"
  ((unreadable > 0)) || return 0
  warn "$unreadable tracked files weren't readable by the app's user (a strict umask?); fixing their modes."
  (
    cd "$INSTALL_DIR"
    git ls-files -z | xargs -0 -r chmod go+r
    git ls-files -z | xargs -0 -r stat -c '%a %n' | awk '$1 ~ /^7/ {sub(/^[0-7]+ /, ""); print}' |
      xargs -r -d '\n' chmod go+x
    git ls-files | xargs -r -d '\n' -n1 dirname | sort -u | xargs -r -d '\n' chmod go+rx
  )
}

write_config() {
  if ((DRY_RUN)); then
    info "${DIM}would create .env with fresh secrets (scripts/secrets.sh) and set the answers${R}"
    return 0
  fi
  (cd "$INSTALL_DIR" && umask 077 && scripts/secrets.sh >> "$LOG_FILE" 2>&1) || die "scripts/secrets.sh failed."
  chmod 600 "$INSTALL_DIR/.env"
  set_env HTTP_PORT 80
  set_env DOMAIN "$DOMAIN"
  set_env TV_HOST "$DOMAIN"
  set_env ACME_EMAIL "$ADMIN_EMAIL"
  set_env MEDIA_ROOT "$MEDIA_ROOT"
  set_commit
  write_backup_config
  write_crowdsec_config
  write_monitoring_config
  write_replica_config
  ok "Settings written to $INSTALL_DIR/.env (mode 600; secrets generated on this server)"
  mkdir -p "$CONF_DIR"
  printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR" > "$CONF_DIR/install.conf"
}

# Backups (ADR-0020): the folder in .env (a host folder the containers mount), created
# here; everything else is an admin setting. The backup passphrase file is created by
# scripts/secrets.sh; show_passphrase shows it once.
write_backup_config() {
  [[ -n "$BACKUP_DIR" ]] || BACKUP_DIR="$(get_env BACKUP_DIR)"
  [[ -n "$BACKUP_DIR" ]] || BACKUP_DIR="$DEFAULT_BACKUP_DIR"
  set_env BACKUP_DIR "$BACKUP_DIR"
  if ((DRY_RUN)); then info "${DIM}would create $BACKUP_DIR${R}"; return 0; fi
  mkdir -p "$BACKUP_DIR"
  chmod 711 "$BACKUP_DIR"
}

# CrowdSec on or off in .env (the overlay), with the community blocklist choice.
write_crowdsec_config() {
  case "$CROWDSEC" in yes) set_env CROWDSEC 1 ;; no) set_env CROWDSEC 0 ;; esac
  case "$CROWDSEC_ONLINE" in yes) set_env CROWDSEC_ONLINE 1 ;; no) set_env CROWDSEC_ONLINE 0 ;; esac
}

# The backup passphrase, shown once when scripts/secrets.sh has just created it, and kept
# in a root-only file: without it the backups cannot be read.
show_passphrase() {
  local file="$INSTALL_DIR/secrets/backup_passphrase"
  ((DRY_RUN)) && return 0
  [[ -s "$file" && ! -e "$BACKUP_PASSPHRASE_FILE" ]] || return 0
  umask 077
  {
    printf 'Smart IPTV backup passphrase (created %s)\n\n' "$(date -u +%FT%TZ)"
    cat "$file"
    printf '\nEvery backup is encrypted with it. Keep a copy away from this server (a password\n'
    printf 'manager, paper in a safe): if the server is lost, the backups cannot be read without it.\n'
  } > "$BACKUP_PASSPHRASE_FILE"
  BACKUP_PASSPHRASE_SHOWN="$(cat "$file")"
}

# Backups: the media choice and the optional bucket into the admin's settings.
write_backup_settings() {
  local extra=("$@")
  case "$BACKUP_MEDIA" in
    yes) store_setting backups.media_enabled true "${extra[@]}" ;;
    no) store_setting backups.media_enabled false "${extra[@]}" ;;
  esac
  [[ -n "$S3_ENDPOINT" && -n "$S3_BUCKET" ]] || return 0
  store_setting backups.s3_endpoint "$S3_ENDPOINT"
  store_setting backups.s3_bucket "$S3_BUCKET"
  [[ -z "$S3_REGION" ]] || store_setting backups.s3_region "$S3_REGION"
  [[ -z "$S3_PREFIX" ]] || store_setting backups.s3_prefix "$S3_PREFIX"
  [[ "$S3_PATH_STYLE" != yes ]] || store_setting backups.s3_path_style true
  [[ -z "$S3_ACCESS_KEY" ]] || store_setting backups.s3_access_key "$S3_ACCESS_KEY"
  [[ -z "$S3_SECRET_KEY" ]] || store_setting backups.s3_secret_key "$S3_SECRET_KEY"
  if [[ -n "$S3_ACCESS_KEY" && -n "$S3_SECRET_KEY" ]]; then store_setting backups.s3_enabled true; fi
  ((DRY_RUN)) || ok "The backup bucket saved in the admin's settings (Settings → Backups)"
}

# Before an update: a named restore point in the WAL, so a bad migration can be undone
# by restoring to just before it (docs/runbooks/rollback.md). Needs a running database.
restore_point() {
  local name user db
  name="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
  if ((DRY_RUN)); then info "${DIM}would create the restore point $name${R}"; return 0; fi
  user="$(get_env POSTGRES_USER)"; db="$(get_env POSTGRES_DB)"
  if compose exec -T postgres psql -U "${user:-iptv}" -d "${db:-iptv}" -Atc \
    "SELECT pg_create_restore_point('$name')" >> "$LOG_FILE" 2>&1; then
    ok "Restore point $name (to undo this update: docs/runbooks/rollback.md)"
  else
    warn "No restore point: the database isn't running."
  fi
}

# The git commit this installation runs, shown in the admin (Settings → System).
set_commit() {
  local commit
  commit="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || true)"
  [[ -z "$commit" ]] || set_env APP_COMMIT "$commit"
}

# The monitoring overlay on or off in .env, then its Docker secrets (secrets/monitoring/).
# Its alert recipients and channels are admin settings, which Alertmanager fetches
# from Django (ADR-0019), so nothing else goes into .env.
write_monitoring_config() {
  if [[ "$MONITORING" == yes ]]; then
    set_env MONITORING 1
  elif [[ "$MONITORING" == no ]]; then
    set_env MONITORING 0
  fi
  ((DRY_RUN)) && return 0
  (cd "$INSTALL_DIR" && umask 077 && scripts/secrets.sh >> "$LOG_FILE" 2>&1) || die "scripts/secrets.sh failed."
}

# store_setting KEY VALUE [--if-unset]: into the admin's settings (they stay editable
# there); the value travels on stdin, never on a command line, and is never printed.
store_setting() {
  local key="$1" value="$2" extra=("${@:3}")
  if ((DRY_RUN)); then info "${DIM}would set${R} $key in the admin's settings"; return 0; fi
  printf '%s' "$value" | compose exec -T web python manage.py set_setting "$key" --stdin "${extra[@]}" \
    >> "$LOG_FILE" 2>&1 || die "Storing the $key setting failed (see $LOG_FILE)."
}

# Alert recipients and Telegram go into the settings (Settings → Monitoring and →
# Integrations in the admin); Alertmanager's fallback fetches them from Django.
write_monitoring_settings() {
  local extra=("$@")
  monitoring_enabled || return 0
  [[ -z "$ALERT_EMAIL" ]] || store_setting monitoring.alert_emails "$ALERT_EMAIL" "${extra[@]}"
  [[ -z "$TELEGRAM_BOT_TOKEN" ]] || store_setting notifications.telegram_bot_token "$TELEGRAM_BOT_TOKEN"
  [[ -z "$TELEGRAM_CHAT_ID" ]] || store_setting notifications.telegram_chat_id "$TELEGRAM_CHAT_ID"
  ((DRY_RUN)) || ok "Alert recipients saved in the admin's settings (Settings, Monitoring)"
}

# The optional TMDB and SMTP answers go into the admin's settings (ADR-0019): encrypted
# when secret, and edited there from now on (Settings → Integrations).
write_integration_settings() {
  [[ -z "$TMDB_TOKEN" ]] || store_setting metadata.tmdb_read_access_token "$TMDB_TOKEN"
  if [[ -n "$SMTP_HOST" ]]; then
    store_setting email.smtp_host "$SMTP_HOST"
    store_setting email.smtp_port "$SMTP_PORT"
    store_setting email.smtp_security "$SMTP_SECURITY"
    [[ -z "$SMTP_USER" ]] || store_setting email.smtp_username "$SMTP_USER"
    [[ -z "$SMTP_PASSWORD" ]] || store_setting email.smtp_password "$SMTP_PASSWORD"
    [[ -z "$SMTP_FROM" ]] || store_setting email.from_address "$SMTP_FROM"
  fi
  if [[ -n "$TMDB_TOKEN$SMTP_HOST" ]] && ((!DRY_RUN)); then
    ok "TMDB and email saved in the admin's settings (Settings → Integrations)"
  fi
}

# Settings that still come from .env (servers installed before ADR-0019): names only.
report_server_config() {
  local count
  ((DRY_RUN)) && return 0
  count="$(compose exec -T web python manage.py settings_report --count 2>>"$LOG_FILE" | tail -n 1 || true)"
  [[ "$count" =~ ^[0-9]+$ ]] || return 0
  if ((count > 0)); then
    info "$count setting(s) still come from .env; move them into the admin (Settings → Integrations,"
    info "\"Move into settings\"), then they can be removed from .env."
  fi
}

prepare_media() {
  if ((DRY_RUN)); then info "${DIM}would create $MEDIA_ROOT/movies and $MEDIA_ROOT/series${R}"; else
    mkdir -p "$MEDIA_ROOT/movies" "$MEDIA_ROOT/series"
    chmod 755 "$MEDIA_ROOT" "$MEDIA_ROOT/movies" "$MEDIA_ROOT/series"
    ok "Media folders: $MEDIA_ROOT/movies and $MEDIA_ROOT/series"
  fi
  if [[ "$SAMPLE_MEDIA" == yes ]]; then
    run "Generating the sample titles" "$INSTALL_DIR/scripts/sample_media.sh" "$MEDIA_ROOT"
  fi
}

# The importer (ADR-0025) runs as the app user (uid 10001) and moves imported files into
# the libraries, so it needs write access to the media folder. An ACL gives it that for
# the folder and everything in it, and (as a default ACL) for whatever arrives later,
# without changing who owns the files; other services keep mounting it read-only.
# Idempotent; a file system without ACLs gets a warning, and imports then fail with a
# permission error until the folder is writable for uid 10001.
APP_UID=10001
grant_media_writes() {
  if ((DRY_RUN)); then
    info "${DIM}would let the importer (uid $APP_UID) write $MEDIA_ROOT (setfacl, default ACL)${R}"
    return 0
  fi
  [[ -d "$MEDIA_ROOT" ]] || return 0
  if ! command -v setfacl >/dev/null; then
    if command -v apt-get >/dev/null; then
      run "Installing acl (setfacl)" env DEBIAN_FRONTEND=noninteractive \
        sh -c "apt-get update -q && apt-get install -yq acl"
    else
      warn "setfacl is missing: give uid $APP_UID write access to $MEDIA_ROOT for imports."
      return 0
    fi
  fi
  if getfacl -cp "$MEDIA_ROOT" 2>/dev/null | grep -q "^default:user:$APP_UID:rwx"; then
    ok "The importer may write $MEDIA_ROOT"
    return 0
  fi
  printf '  Letting the importer write %s… ' "$MEDIA_ROOT"
  if setfacl -R -m "u:$APP_UID:rwX,d:u:$APP_UID:rwX" "$MEDIA_ROOT" >> "$LOG_FILE" 2>&1; then
    printf '%sdone%s\n' "$GREEN" "$R"
  else
    printf '%sfailed%s\n' "$YELLOW" "$R"
    warn "No ACLs on $MEDIA_ROOT's file system: give uid $APP_UID write access for imports."
  fi
}

start_stack() {
  if [[ "$MODE" == install ]]; then
    info "The first build compiles the images and takes about 10–20 minutes."
  else
    info "Rebuilding the changed images takes a few minutes."
  fi
  # --remove-orphans: services that left the compose files (e.g. monitoring turned off) stop.
  run "Building and starting the services" compose up -d --build --wait --wait-timeout 1800 --remove-orphans
  # The migrate service ran before the app services; once more, as the database owner
  # (the app services use a restricted role that can't change the schema, ADR-0020).
  run "Applying database migrations" compose run --rm --no-deps migrate
  # The apps' Content-Security-Policy takes its media origins from Django when the
  # frontend starts (docker/nginx/30-csp.sh; ADR-0021): start it again now that web is up,
  # so origins added in Settings → Security apply with this update.
  run "Refreshing the apps' security policy" compose up -d --no-deps --force-recreate --wait frontend
}

# An update without downtime (ADR-0022, docs/runbooks/deploy.md): build while the old
# version serves; migrate (migrations stay backward compatible: add now, remove a release
# later); replace the web replicas behind Traefik's health checks (scripts/rollout.sh);
# then the other services, each recreated only if it changed.
update_stack() {
  if ((!DRY_RUN)) && [[ -z "$(compose ps -q --status running web 2>/dev/null)" ]]; then
    info "The web service isn't running, so there's nothing to keep serving: starting everything."
    start_stack
    return 0
  fi
  info "Rebuilding the changed images takes a few minutes; the site keeps serving meanwhile."
  run "Building the images" compose build
  run "Updating the database roles" compose run --rm --no-deps db-roles
  run "Applying database migrations" compose run --rm --no-deps migrate
  if replica_enabled; then
    run "Starting the read replica" compose up -d --wait --wait-timeout 1800 postgres-replica
  fi
  local files
  read -r -a files <<<"$(compose_files "$INSTALL_DIR/")"
  run "Replacing the web replicas behind the health checks" "$INSTALL_DIR/scripts/rollout.sh" \
    --replicas "$(web_replicas)" -- --project-directory "$INSTALL_DIR" "${files[@]}"
  # The apps (admin and portal) the same way; a new one also takes the media origins of
  # its Content-Security-Policy from the updated web (docker/nginx/30-csp.sh, ADR-0021).
  run "Replacing the apps' server" "$INSTALL_DIR/scripts/rollout.sh" --service frontend \
    --replicas 1 -- --project-directory "$INSTALL_DIR" "${files[@]}"
  run "Updating the other services" compose up -d --wait --wait-timeout 1800 --remove-orphans
}

# The read replica on or off in .env (compose_files adds its overlay).
write_replica_config() {
  case "$REPLICA" in yes) set_env REPLICA 1 ;; no) set_env REPLICA 0 ;; esac
}

# After --no-replica: the replica's replication slot would keep WAL on the primary (up
# to max_slot_wal_keep_size), so it goes. The replica's volume stays until deleted by hand.
drop_replica_slot() {
  [[ "$REPLICA" == no ]] || return 0
  local user db
  if ((DRY_RUN)); then info "${DIM}would drop the read replica's replication slot${R}"; return 0; fi
  user="$(get_env POSTGRES_USER)"; db="$(get_env POSTGRES_DB)"
  if compose exec -T postgres psql -U "${user:-iptv}" -d "${db:-iptv}" -Atc \
    "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = 'replica1' AND NOT active" \
    >> "$LOG_FILE" 2>&1; then
    ok "Read replica off (its volume postgres-replica-data remains; docs/runbooks/database-replica.md)"
  else
    warn "Couldn't drop the replica's replication slot: see docs/runbooks/database-replica.md."
  fi
}

# After pulling: when the installer itself changed, the update goes on with the new one,
# which may know steps this one doesn't. CODE_BEFORE tells it which commit it came from.
continue_with_new_installer() {
  local self="${BASH_SOURCE[0]:-}" new="$INSTALL_DIR/scripts/install.sh"
  ((DRY_RUN)) && return 0
  [[ -f "$new" ]] || return 0
  if [[ -f "$self" ]] && cmp -s "$self" "$new"; then return 0; fi
  info "The installer itself changed: the update goes on with the new one."
  log "continuing with $new"
  exec env SMART_IPTV_CONTINUED="${CODE_BEFORE:-unknown}" bash "$new" --update "${ORIG_ARGS[@]}"
}

# Update the code (unless the previous installer already did, then handed over).
update_code() {
  if [[ -n "${SMART_IPTV_CONTINUED:-}" ]]; then
    CODE_BEFORE="$SMART_IPTV_CONTINUED"
    [[ "$CODE_BEFORE" =~ ^[0-9a-f]{7,64}$ ]] || CODE_BEFORE=""
    ok "Code at $(git -C "$INSTALL_DIR" log -1 --format='%h %s' 2>/dev/null)"
    return 0
  fi
  CODE_BEFORE="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)"
  get_code
  continue_with_new_installer
}

create_owner() {
  local output args=(--username "$ADMIN_USER" --email "$ADMIN_EMAIL")
  if ((DRY_RUN)); then info "${DIM}would create the owner admin $ADMIN_USER${R}"; return 0; fi
  output="$(OWNER_PASSWORD="$ADMIN_PASSWORD" compose exec -T -e OWNER_PASSWORD web \
    python manage.py create_owner "${args[@]}" 2>>"$LOG_FILE")" || die "Creating the owner admin failed (see $LOG_FILE)."
  if grep -q 'already exists' <<<"$output"; then
    ok "Admin $ADMIN_USER already exists (its password is unchanged)"
    return 0
  fi
  local generated
  generated="$(sed -n 's/^ *Password (shown once, store it now): //p' <<<"$output")"
  umask 077
  {
    printf 'Smart IPTV owner admin (created %s)\n' "$(date -u +%FT%TZ)"
    printf 'URL:       https://admin.%s\n' "$DOMAIN"
    printf 'Sign-in:   %s\n' "$ADMIN_USER"
    if [[ -n "$generated" ]]; then printf 'Password:  %s\n' "$generated"; else printf 'Password:  the one typed during installation\n'; fi
    printf 'MFA:       enrol an authenticator app at the first sign-in\n'
    printf '\nMove this to a password manager, then delete this file.\n'
  } > "$CREDENTIALS_FILE"
  ADMIN_PASSWORD_GENERATED="$generated"
  ok "Owner admin $ADMIN_USER created"
}

finish_setup() {
  run "Adding the Movies and Series libraries" compose exec -T web python manage.py ensure_libraries
  run "Building the search index" compose exec -T web python manage.py search_reindex
}

check_https() {
  ((DRY_RUN)) && return 0
  local host path url code expected deadline wait_s=0 all_ok=1
  step "Checking the sites"
  # A new edge asks Let's Encrypt for its certificate first: no TLS until it arrives.
  [[ "$ROLE" != edge ]] || wait_s=150
  for host in $(hosts); do
    expected=200
    case "$host" in
      "$EDGE_DOMAIN") path=/healthz ;;
      "$DOMAIN") path=/health ;;
      api.*) path=/api/v1/health ;;
      media.*) path=/healthz ;;
      # The edges host answers registered edges only (ADR-0022): 401 means it's up.
      edges.*) path=/edge/v1/ping expected=401 ;;
      # Monitoring answers only admins signed in through the admin: 401 means it's up.
      grafana.*) path=/api/health expected=401 ;;
      *) path=/ ;;
    esac
    deadline=$(($(date +%s) + wait_s))
    while true; do
      code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host$path" || true)"
      if [[ "$code" == "$expected" ]] || (($(date +%s) >= deadline)); then break; fi
      sleep 5
    done
    [[ "$code" == "$expected" ]] || { warn "$host answers $code on this server"; all_ok=0; continue; }
    url="https://$host$path"
    deadline=$(($(date +%s) + 180))
    until [[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null)" == "$expected" ]]; do
      if (($(date +%s) > deadline)); then
        warn "$host works, but not yet with a trusted certificate over the internet (DNS or Let's Encrypt; it retries by itself)."
        all_ok=0
        continue 2
      fi
      sleep 5
    done
    ok "https://$host"
  done
  ((all_ok)) || info "Check DNS, then: $INSTALL_DIR/scripts/install.sh --update"
}

summary() {
  step "Done"
  say ""
  if ((DRY_RUN)); then
    say "  ${B}Dry run finished: nothing was changed.${R} Without --dry-run it would end with:"
  else
    say "  ${B}Smart IPTV is running.${R}"
  fi
  say ""
  say "  Admin panel      https://admin.$DOMAIN"
  if [[ "$MODE" == install ]]; then
    say "                   sign in as $ADMIN_USER; enrol an authenticator app at first sign-in"
    if [[ -n "${ADMIN_PASSWORD_GENERATED:-}" ]]; then
      say "                   password (shown once): ${B}$ADMIN_PASSWORD_GENERATED${R}"
    fi
    if [[ -f "$CREDENTIALS_FILE" ]]; then
      say "                   also saved in $CREDENTIALS_FILE (root only): move it to a password manager"
    fi
  fi
  say "  Customer portal  https://app.$DOMAIN"
  say "  IPTV apps        server URL https://$DOMAIN (customer logins are made in the admin)"
  say "  Integrations     TMDB, email (SMTP), payments, Telegram, branding: admin → Settings →"
  say "                   Integrations (each with a Test); server facts: Settings → System"
  if monitoring_enabled; then
    say "  Monitoring       https://grafana.$DOMAIN (open it from the admin: Monitoring)"
    say "  Alerts           recipients: admin → Settings → Monitoring; Telegram: → Integrations;"
    say "                   test them: admin → Monitoring → Send a test alert"
  fi
  if live_ingest_enabled; then
    say "  Live ingest      rtmp://$DOMAIN:1935/<path>, srt://$DOMAIN:8890 (users MEDIAMTX_* in .env;"
    say "                   docs/runbooks/live-tv.md), then admin → Live TV → Integrations"
  fi
  say "  Media            copy owned or licensed files into $MEDIA_ROOT/movies and $MEDIA_ROOT/series"
  say ""
  say "  Update later     sudo $INSTALL_DIR/scripts/install.sh --update"
  say "  Change domain    sudo $INSTALL_DIR/scripts/install.sh --reconfigure (domain, admin email,"
  say "                   media folder, monitoring; everything else is in the admin)"
  say "  Service status   cd $INSTALL_DIR && docker compose $(compose_files) ps"
  say "  Install log      $LOG_FILE"
  say "  Backups          every night into ${BACKUP_DIR:-$(get_env BACKUP_DIR)}, restore drill weekly:"
  say "                   admin → Backups; schedule, retention and the bucket: Settings → Backups"
  if [[ -n "${BACKUP_PASSPHRASE_SHOWN:-}" ]]; then
    say "                   backup passphrase (shown once): ${B}$BACKUP_PASSPHRASE_SHOWN${R}"
    say "                   also in $BACKUP_PASSPHRASE_FILE (root only): keep a copy away from this server"
  fi
  if replica_enabled; then
    say "  Read replica     on: reports read from it; failover: docs/runbooks/database-replica.md"
  fi
  say "  Edges            admin → Edges: more media servers (install.sh --edge on each); the"
  say "                   control plane answers them on https://edges.$DOMAIN"
  if crowdsec_enabled; then
    say "  CrowdSec         on; unban an address: cd $INSTALL_DIR && docker compose $(compose_files) \\"
    say "                   exec crowdsec cscli decisions delete --ip <address>"
  fi
  say ""
}

# --- A standalone media edge (--edge, ADR-0022, docs/runbooks/add-edge.md) ---------------------
EDGE_STORAGE="" EDGE_ORIGIN_URL="" EDGE_CORS_ORIGINS="" CODE_BEFORE=""

# json_string NAME JSON / json_list NAME JSON: fields of the ping's flat answer (no jq or
# Python needed before the packages are installed). Its URLs hold no quotes or brackets.
json_string() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" <<<"$2" | head -n 1; }
json_list() { sed -n "s/.*\"$1\": *\[\([^]]*\)\].*/\1/p" <<<"$2" | head -n 1 | tr -d ' "'; }

# What the control plane says about this edge (GET /edge/v1/ping with its id and secret):
# where its media comes from, the S3 origin's URL (no credentials) and the web origins
# for CORS. The secret reaches curl on stdin, never on a command line.
edge_ping() {
  local url="${CONTROL_URL%/}/edge/v1/ping" answer code body problem=""
  answer="$(printf 'header = "Authorization: Bearer %s"\nheader = "X-Edge-Id: %s"\n' "$EDGE_TOKEN" "$EDGE_ID" |
    curl -sS --max-time 15 --config - -w '\n%{http_code}' "$url" 2>>"$LOG_FILE" || true)"
  code="${answer##*$'\n'}"
  body="${answer%$'\n'*}"
  case "$code" in
    200) ;;
    401) problem="The control plane doesn't know edge $EDGE_ID with this secret, or the edge is disabled: check admin → Edges (rotate the secret if it was lost)." ;;
    000 | "") problem="No answer from $url: check the address, its DNS and this server's internet access." ;;
    *) problem="$url answered $code: is it the control plane's edges host (https://edges.<domain>)?" ;;
  esac
  if [[ -z "$problem" && "$(json_string edge_id "$body")" != "$EDGE_ID" ]]; then problem="Unexpected answer from $url."; fi
  if [[ -n "$problem" ]]; then
    ((DRY_RUN)) || die "$problem"
    warn "$problem"
    info "${DIM}dry run: going on as if the edge used the S3 origin${R}"
    EDGE_STORAGE=origin EDGE_ORIGIN_URL="https://s3.example.com/bucket" EDGE_CORS_ORIGINS=""
    return 0
  fi
  EDGE_STORAGE="$(json_string storage "$body")"
  EDGE_ORIGIN_URL="$(json_string origin_url "$body")"
  EDGE_CORS_ORIGINS="$(json_list cors_origins "$body")"
  case "$EDGE_STORAGE" in
    origin)
      [[ "$EDGE_ORIGIN_URL" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9_.~-]+)*/?$ ]] ||
        die "The control plane has no S3 origin for this edge yet: admin → Settings → Storage (publish to S3, Test), then run this again."
      ok "Edge $EDGE_ID is registered; it serves the renditions published to the S3 origin" ;;
    disk) ok "Edge $EDGE_ID is registered; it serves a full copy of the renditions on this server" ;;
    *) die "Unexpected storage \"$EDGE_STORAGE\" from $url." ;;
  esac
}

gather_edge_answers() {
  local current
  step "Questions"
  say "  Add the edge first in admin → Edges (Add edge, kind \"standalone\"): it shows the"
  say "  edge's id and secret once, and this command with the answers filled in."
  current="$(get_env EDGE_CONTROL_URL)"
  ask CONTROL_URL "The control plane's edges address (https://edges.<domain>)" "$current" valid_control_url
  CONTROL_URL="${CONTROL_URL%/}"
  ask EDGE_ID "Edge id" "$(get_env EDGE_ID)" valid_edge_id
  ask EDGE_DOMAIN "This server's name for players (e.g. media2.example.com)" "$(get_env EDGE_DOMAIN)" valid_edge_domain
  EDGE_DOMAIN="${EDGE_DOMAIN,,}"
  if [[ -z "$EDGE_TOKEN" && -r "$INSTALL_DIR/secrets/edge_token" ]]; then
    EDGE_TOKEN="$(head -n 1 "$INSTALL_DIR/secrets/edge_token")"
  fi
  while true; do
    ask_secret EDGE_TOKEN "The edge's secret (hidden; from admin → Edges)"
    [[ -n "$EDGE_TOKEN" ]] || { ((ASSUME_YES)) && die "Pass the edge's secret in EDGE_TOKEN."; warn "The secret is needed."; continue; }
    valid_edge_token "$EDGE_TOKEN" && break
    ((ASSUME_YES)) && die "EDGE_TOKEN isn't an edge secret."
    EDGE_TOKEN=""
  done
  [[ -n "$ADMIN_EMAIL" ]] || ADMIN_EMAIL="$(get_env EDGE_ACME_EMAIL)"
  ask_optional ADMIN_EMAIL "Email for Let's Encrypt's notices (optional)"
  [[ -z "$ADMIN_EMAIL" ]] || valid_email "$ADMIN_EMAIL" || die "Invalid email address."
  current="$(get_env EDGE_CACHE_SIZE)"
  ask EDGE_CACHE_SIZE "Media cache on this disk" "${current:-50g}" valid_cache_size
  step "Asking the control plane"
  edge_ping
  if [[ "$EDGE_STORAGE" == disk ]]; then
    current="$(get_env EDGE_MIRROR_DIR)"
    ask MEDIA_ROOT "Folder with the full copy of the renditions" "${current:-/srv/media/renditions}" valid_dir
  fi
}

show_edge_plan() {
  step "Summary"
  info "Code:          $INSTALL_DIR ($REPO_URL, $BRANCH)"
  info "Edge:          $EDGE_ID at https://$EDGE_DOMAIN (Let's Encrypt${ADMIN_EMAIL:+, notices to $ADMIN_EMAIL})"
  info "Control plane: $CONTROL_URL"
  if [[ "$EDGE_STORAGE" == origin ]]; then
    info "Media:         the S3 origin ($EDGE_ORIGIN_URL), cached here up to $EDGE_CACHE_SIZE"
  else
    info "Media:         the full copy in $MEDIA_ROOT"
  fi
  info "Firewall:      $([[ "$FIREWALL" == yes ]] && echo "ufw: SSH${SSH_FROM:+ from $SSH_FROM}, 80, 443" || echo "unchanged")"
  docker_ready || info "Docker:        will be installed from download.docker.com"
  local go=""
  yes_no go "Go ahead?" yes
  [[ "$go" == yes ]] || die "Nothing was changed."
}

# .env (bootstrap only, mode 600) and the secret (secrets/edge_token, mode 600): nothing
# else secret lives on an edge; its keys arrive on the control channel.
write_edge_config() {
  if ((DRY_RUN)); then
    info "${DIM}would write .env and secrets/edge_token (both mode 600)${R}"
    return 0
  fi
  (
    umask 077
    [[ -f "$(env_file)" ]] || : > "$(env_file)"
    mkdir -p "$INSTALL_DIR/secrets"
    printf '%s\n' "$EDGE_TOKEN" > "$INSTALL_DIR/secrets/edge_token"
  )
  chmod 600 "$(env_file)" "$INSTALL_DIR/secrets/edge_token"
  set_env ROLE edge
  set_env EDGE_ID "$EDGE_ID"
  set_env EDGE_DOMAIN "$EDGE_DOMAIN"
  set_env EDGE_CONTROL_URL "$CONTROL_URL"
  set_env EDGE_ACME_EMAIL "$ADMIN_EMAIL"
  set_env EDGE_CACHE_SIZE "$EDGE_CACHE_SIZE"
  write_edge_origin
  set_commit
  ok "Settings written to $INSTALL_DIR/.env and the secret to secrets/edge_token (mode 600)"
  mkdir -p "$CONF_DIR"
  printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR" > "$CONF_DIR/install.conf"
}

# What the ping said: the media source and CORS (they follow the control plane's settings).
write_edge_origin() {
  set_env EDGE_CORS_ORIGINS "$EDGE_CORS_ORIGINS"
  if [[ "$EDGE_STORAGE" == origin ]]; then
    set_env EDGE_MODE s3
    set_env EDGE_ORIGIN_URL "$EDGE_ORIGIN_URL"
  else
    set_env EDGE_MODE local
    set_env EDGE_MIRROR_DIR "$MEDIA_ROOT"
  fi
}

# The edge's configuration is mounted from the checkout: when an update changed it, the
# container starts again to render it (a few seconds without this edge; players retry).
start_edge() {
  local recreate=()
  if [[ "$MODE" == update && -n "$CODE_BEFORE" ]] &&
    ! git -C "$INSTALL_DIR" diff --quiet "$CODE_BEFORE" HEAD -- streaming/nginx docker/compose.edge.yml 2>/dev/null; then
    recreate=(--force-recreate)
  fi
  run "Starting the edge" compose up -d --wait --wait-timeout 600 --remove-orphans "${recreate[@]}"
}

edge_summary() {
  step "Done"
  say ""
  if ((DRY_RUN)); then
    say "  ${B}Dry run finished: nothing was changed.${R} Without --dry-run it would end with:"
  else
    say "  ${B}Edge $EDGE_ID is running.${R}"
  fi
  say ""
  say "  Media            https://$EDGE_DOMAIN (players reach it through signed links only)"
  say "  Control plane    $CONTROL_URL: admin → Edges shows it healthy within a minute;"
  say "                   press Test there, then give it a weight (canary first, if you like)"
  say "  Update later     sudo $INSTALL_DIR/scripts/install.sh --update"
  say "  Change settings  sudo $INSTALL_DIR/scripts/install.sh --reconfigure"
  say "  New secret       admin → Edges → New secret, then --reconfigure here (it asks for it)"
  say "  Service status   cd $INSTALL_DIR && docker compose $(compose_files) ps"
  say "  Install log      $LOG_FILE"
  say ""
}

main_edge() {
  step "Checking this server"
  check_os
  if [[ "$MODE" != update ]]; then
    check_resources
    if [[ "$MODE" == install ]]; then
      check_ports
      check_no_other_install
    fi
  fi
  check_internet

  if [[ "$MODE" == update ]]; then
    EDGE_DOMAIN="$(get_env EDGE_DOMAIN)" EDGE_ID="$(get_env EDGE_ID)" CONTROL_URL="$(get_env EDGE_CONTROL_URL)"
    MEDIA_ROOT="$(get_env EDGE_MIRROR_DIR)"
    step "Updating"
    install_docker
    update_code
    if ((DRY_RUN)); then info "${DIM}would set APP_COMMIT in .env${R}"; else set_commit; fi
    # The origin or the web origins may have changed on the control plane since.
    if [[ -r "$INSTALL_DIR/secrets/edge_token" ]]; then
      EDGE_TOKEN="$(head -n 1 "$INSTALL_DIR/secrets/edge_token")"
      edge_ping
      write_edge_origin
    fi
    start_edge
    check_https
    edge_summary
    return 0
  fi

  gather_edge_answers
  step "Checking DNS"
  check_dns
  show_edge_plan

  step "Installing"
  install_packages
  install_docker
  configure_firewall
  [[ "$MODE" == install ]] && get_code
  write_edge_config
  start_edge
  check_https
  edge_summary
}

# --- Modes ------------------------------------------------------------------------------------------
locate_install() {
  local here
  if [[ -z "$INSTALL_DIR" && -r "$CONF_DIR/install.conf" ]]; then
    INSTALL_DIR="$(sed -n 's/^INSTALL_DIR=//p' "$CONF_DIR/install.conf")"
  fi
  if [[ -z "$INSTALL_DIR" ]]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
    if [[ -n "$here" && -f "$here/docker/compose.prod.yml" && -d "$here/.git" ]]; then
      INSTALL_DIR="$here"
    else
      INSTALL_DIR="$DEFAULT_DIR"
    fi
  fi
}

choose_mode() {
  if [[ -f "$INSTALL_DIR/.env" && "$(get_env ROLE)" == edge ]]; then
    ROLE=edge
    if [[ -z "$MODE" ]]; then
      info "A Smart IPTV edge ($(get_env EDGE_ID)) is installed in $INSTALL_DIR for $(get_env EDGE_DOMAIN)."
      if ((ASSUME_YES)); then MODE=update; else
        local choice=""
        ask choice "Update it (u), change its settings (s) or quit (q)?" "u"
        case "${choice,,}" in
          u | update) MODE=update ;;
          s | settings | reconfigure) MODE=reconfigure ;;
          *) die "Nothing was changed." ;;
        esac
      fi
    fi
    return 0
  fi
  if [[ "$ROLE" == edge && -f "$INSTALL_DIR/.env" ]]; then
    die "$INSTALL_DIR holds the Smart IPTV platform: a standalone edge goes on another server (or --dir)."
  fi
  if [[ "$ROLE" == edge ]]; then
    [[ "$MODE" == update || "$MODE" == reconfigure ]] && die "No edge installation found in $INSTALL_DIR."
    MODE=install
    return 0
  fi
  if [[ -f "$INSTALL_DIR/.env" && -n "$(get_env DOMAIN)" && "$(get_env DOMAIN)" != localhost ]]; then
    if [[ -z "$MODE" ]]; then
      info "Smart IPTV is already installed in $INSTALL_DIR for $(get_env DOMAIN)."
      if ((ASSUME_YES)); then MODE=update; else
        local choice=""
        ask choice "Update it (u), change its settings (s) or quit (q)?" "u"
        case "${choice,,}" in
          u | update) MODE=update ;;
          s | settings | reconfigure) MODE=reconfigure ;;
          *) die "Nothing was changed." ;;
        esac
      fi
    fi
  else
    [[ "$MODE" == update || "$MODE" == reconfigure ]] && die "No installation found in $INSTALL_DIR."
    MODE=install
  fi
}

main() {
  ORIG_ARGS=("$@")
  parse_args "$@"
  if ((!DRY_RUN)) && ((EUID == 0)); then
    mkdir -p "$(dirname "$LOG_FILE")" && touch "$LOG_FILE" && chmod 600 "$LOG_FILE"
  fi
  printf '\n%s%sSmart IPTV installer%s\n' "$B" "$CYAN" "$R"
  ((DRY_RUN)) && printf '%sDry run: nothing will be changed.%s\n' "$YELLOW" "$R"
  require_root "$@"
  open_terminal
  locate_install
  choose_mode
  log "mode=$MODE role=${ROLE:-platform} dir=$INSTALL_DIR"
  if [[ "$ROLE" == edge ]]; then main_edge; return 0; fi

  step "Checking this server"
  check_os
  if [[ "$MODE" != update ]]; then
    check_resources
    if [[ "$MODE" == install ]]; then
      check_ports
      check_no_other_install
    fi
  fi
  check_internet

  if [[ "$MODE" == update ]]; then
    DOMAIN="$(get_env DOMAIN)"
    MEDIA_ROOT="$(get_env MEDIA_ROOT)"; MEDIA_ROOT="${MEDIA_ROOT:-/srv/media}"
    step "Updating"
    install_docker
    [[ -n "${SMART_IPTV_CONTINUED:-}" ]] || restore_point
    update_code
    if ((DRY_RUN)); then info "${DIM}would set APP_COMMIT in .env${R}"; else set_commit; fi
    if ((DRY_RUN)); then info "${DIM}would add new .env keys (scripts/secrets.sh)${R}"; else
      (cd "$INSTALL_DIR" && scripts/secrets.sh >> "$LOG_FILE" 2>&1) || die "scripts/secrets.sh failed."
    fi
    write_backup_config
    write_crowdsec_config
    write_replica_config
    show_passphrase
    # --monitoring / --no-monitoring switch the overlay; otherwise .env decides.
    if [[ -n "$MONITORING" ]]; then
      [[ "$MONITORING" == no || -n "$ALERT_EMAIL" ]] || ALERT_EMAIL="$(get_env ACME_EMAIL)"
      write_monitoring_config
    elif ! monitoring_enabled && [[ -z "$(get_env MONITORING)" ]]; then
      info "Monitoring (Grafana, Prometheus, Loki, alerts) is available: add --monitoring to turn it on."
    fi
    info "Monitoring: $(monitoring_enabled && echo "on (grafana.$DOMAIN)" || echo off)"
    info "Read replica: $(replica_enabled && echo on || echo off)"
    grant_media_writes
    update_stack
    drop_replica_slot
    # A recipient the admin already chose in Settings wins over .env's.
    [[ "$MONITORING" != yes ]] || write_monitoring_settings --if-unset
    write_backup_settings --if-unset
    run "Building the search index" compose exec -T web python manage.py search_reindex
    report_server_config
    check_https
    summary
    return 0
  fi

  gather_answers
  step "Checking DNS"
  check_dns
  show_plan

  step "Installing"
  install_packages
  install_docker
  configure_firewall
  [[ "$MODE" == install ]] && get_code
  write_config
  show_passphrase
  prepare_media
  grant_media_writes
  start_stack
  drop_replica_slot
  write_integration_settings
  write_monitoring_settings
  write_backup_settings
  if [[ "$MODE" == install ]]; then
    create_owner
    finish_setup
  fi
  check_https
  summary
}

main "$@"
