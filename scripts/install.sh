#!/usr/bin/env bash
# Smart IPTV installer and updater for one server (the small tier, docs/runbooks/deploy.md).
#
#   Fresh server (Ubuntu 22.04/24.04 or Debian 12/13), as root or with sudo:
#     curl -fsSL https://raw.githubusercontent.com/mah007/iptv/main/scripts/install.sh -o install.sh
#     sudo bash install.sh
#   From a checkout:          sudo scripts/install.sh
#   Update an installation:   sudo /opt/iptv/scripts/install.sh --update
#   All options:              scripts/install.sh --help
#
# It asks for the domain, the admin's email and sign-in name, the media folder and the
# optional TMDB and SMTP settings, and checks the server (OS, CPU, memory, disk, ports
# 80/443, DNS). Then it installs Docker if needed, gets the code, writes .env with fresh
# secrets, builds and starts the stack, creates the owner admin and the default
# libraries, and checks HTTPS on every host. Run again on an installed server, it offers
# to update or to change the settings.
#
# Secrets are never taken from the command line (other users can read it): pass them in
# environment variables (ADMIN_PASSWORD, TMDB_TOKEN, SMTP_PASSWORD) or type them when
# asked. Nothing secret is written to the log.
set -Eeuo pipefail

readonly DEFAULT_REPO="https://github.com/mah007/iptv.git"
readonly DEFAULT_DIR="/opt/iptv"
readonly LOG_FILE="/var/log/smart-iptv-install.log"
readonly CONF_DIR="/etc/smart-iptv"
readonly CREDENTIALS_FILE="/root/smart-iptv-admin.txt"
readonly MIN_CPUS=2 MIN_RAM_MB=3500 MIN_DISK_GB=25
readonly GOOD_CPUS=4 GOOD_RAM_MB=7500 GOOD_DISK_GB=60

# --- Answers: options and environment variables fill these; questions fill the rest. ----
MODE=""                       # install | update | reconfigure
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
  --update               pull the latest code and restart; keeps every setting
  --reconfigure          change the domain, email, media folder, TMDB or SMTP settings

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

Other:
  -y, --yes              unattended: no questions; defaults for whatever isn't given
  --skip-dns-check       don't stop when the DNS records don't point at this server
  --dry-run              ask and check everything, then show the plan; change nothing
  -h, --help             this help

Secrets come from environment variables, never options:
  ADMIN_PASSWORD   the owner admin's password (12+ characters); generated if empty
  TMDB_TOKEN       TMDB API read access token (v4) for metadata; optional
  SMTP_PASSWORD    the SMTP password; optional

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

compose() {
  docker compose --project-directory "$INSTALL_DIR" \
    -f "$INSTALL_DIR/docker/compose.yml" -f "$INSTALL_DIR/docker/compose.prod.yml" "$@"
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

hosts() { printf '%s\n' "$DOMAIN" "admin.$DOMAIN" "app.$DOMAIN" "api.$DOMAIN" "media.$DOMAIN"; }

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
  cpus="$(nproc)"
  ram_mb="$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)"
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
    info "Create two DNS A records pointing at $ip, then run the installer again:"
    info "  $DOMAIN        A  $ip"
    info "  *.$DOMAIN      A  $ip"
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

  info ""
  info "${B}TMDB (optional).${R} Posters, plots and cast in English and Arabic come from"
  info "TMDB: create a free account, then an API key at themoviedb.org/settings/api and"
  info "copy the \"API Read Access Token\". Without one, titles keep their file names."
  if [[ -n "$(get_env TMDB_READ_ACCESS_TOKEN)" ]]; then info "A token is already set; press Enter to keep it."; fi
  ask_secret TMDB_TOKEN "TMDB API read access token"

  info ""
  info "${B}Email (optional).${R} Invitations, password resets and invoices are emailed"
  info "through your SMTP server. Skip it now and they wait in the outbox until it's set."
  ask_optional SMTP_HOST "SMTP server (e.g. smtp.example.com)"
  [[ -z "$SMTP_HOST" ]] && SMTP_HOST="$(get_env EMAIL_HOST)"
  if [[ -n "$SMTP_HOST" ]]; then
    current="$(get_env EMAIL_PORT)"
    ask SMTP_PORT "SMTP port" "${current:-587}" valid_port
    ask SMTP_SECURITY "Encryption: starttls, ssl or none" "starttls" valid_security
    ask_optional SMTP_USER "SMTP user name"
    if [[ -n "$SMTP_USER" ]]; then ask_secret SMTP_PASSWORD "SMTP password"; fi
    ask SMTP_FROM "Send emails from" "Smart IPTV <no-reply@$DOMAIN>" valid_env_value
  fi

  if ! docker_ready; then
    info ""
    info "${B}Docker.${R} The app runs in Docker containers; Docker isn't installed yet."
    yes_no INSTALL_DOCKER "Install Docker from Docker's official repository?" yes
    [[ "$INSTALL_DOCKER" == yes ]] || die "Docker Engine with the Compose plugin is needed."
  fi

  if command -v ufw >/dev/null 2>&1; then
    info ""
    info "${B}Firewall.${R} ufw can allow only SSH, HTTP and HTTPS. Say no if another"
    info "firewall (e.g. your cloud provider's) already does this."
    yes_no FIREWALL "Configure and enable ufw?" no
  else
    FIREWALL=no
  fi
}

show_plan() {
  step "Summary"
  info "Code:          $INSTALL_DIR ($REPO_URL, $BRANCH)"
  info "Domain:        $DOMAIN (+ admin., app., api., media.)"
  info "Owner admin:   ${ADMIN_USER:-unchanged} <$ADMIN_EMAIL>, password $([[ -n "$ADMIN_PASSWORD" ]] && echo "as typed" || echo "generated")"
  info "Media folder:  $MEDIA_ROOT$([[ "$SAMPLE_MEDIA" == yes ]] && echo ", with sample titles")"
  info "TMDB:          $([[ -n "$TMDB_TOKEN" || -n "$(get_env TMDB_READ_ACCESS_TOKEN)" ]] && echo "token set" || echo "not set (file names only)")"
  info "Email:         $([[ -n "$SMTP_HOST" ]] && echo "$SMTP_HOST:$SMTP_PORT ($SMTP_SECURITY)" || echo "not set (outbox only)")"
  info "Firewall:      $([[ "$FIREWALL" == yes ]] && echo "ufw: SSH, 80, 443" || echo "unchanged")"
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

configure_firewall() {
  [[ "$FIREWALL" == yes ]] || return 0
  local ports
  ports="$(ss -Hltnp 2>/dev/null | awk '/sshd/ {n = split($4, a, ":"); print a[n]}' | sort -u)"
  [[ -n "$ports" ]] || ports=22
  for port in $ports; do run "Allowing SSH on port $port" ufw allow "$port/tcp"; done
  run "Allowing HTTP and HTTPS" sh -c "ufw allow 80/tcp && ufw allow 443/tcp"
  run "Enabling ufw" ufw --force enable
}

get_code() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]]; then
      die "$INSTALL_DIR has local changes to tracked files; commit or discard them first."
    fi
    run "Updating the code ($BRANCH)" git -C "$INSTALL_DIR" pull --ff-only -q
  elif [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    die "$INSTALL_DIR exists and isn't a Smart IPTV checkout; choose another --dir."
  else
    run "Downloading the code to $INSTALL_DIR" git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
  ((DRY_RUN)) || ok "Code at $(git -C "$INSTALL_DIR" log -1 --format='%h %s' 2>/dev/null)"
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
  [[ -z "$TMDB_TOKEN" ]] || set_env TMDB_READ_ACCESS_TOKEN "$TMDB_TOKEN"
  if [[ -n "$SMTP_HOST" ]]; then
    set_env EMAIL_HOST "$SMTP_HOST"
    set_env EMAIL_PORT "$SMTP_PORT"
    set_env EMAIL_USE_TLS "$([[ "$SMTP_SECURITY" == starttls ]] && echo true || echo false)"
    set_env EMAIL_USE_SSL "$([[ "$SMTP_SECURITY" == ssl ]] && echo true || echo false)"
    [[ -z "$SMTP_USER" ]] || set_env EMAIL_HOST_USER "$SMTP_USER"
    [[ -z "$SMTP_PASSWORD" ]] || set_env EMAIL_HOST_PASSWORD "$SMTP_PASSWORD"
    set_env DEFAULT_FROM_EMAIL "$SMTP_FROM"
  fi
  ok "Settings written to $INSTALL_DIR/.env (mode 600; secrets generated on this server)"
  mkdir -p "$CONF_DIR"
  printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR" > "$CONF_DIR/install.conf"
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

start_stack() {
  if [[ "$MODE" == install ]]; then
    info "The first build compiles the images and takes about 10–20 minutes."
  else
    info "Rebuilding the changed images takes a few minutes."
  fi
  run "Building and starting the services" compose up -d --build --wait --wait-timeout 1800
  run "Applying database migrations" compose exec -T web python manage.py migrate --noinput
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
  local host path url code deadline all_ok=1
  step "Checking the sites"
  for host in $(hosts); do
    case "$host" in
      "$DOMAIN") path=/health ;;
      api.*) path=/api/v1/health ;;
      media.*) path=/healthz ;;
      *) path=/ ;;
    esac
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host$path" || true)"
    [[ "$code" == 200 ]] || { warn "$host answers $code on this server"; all_ok=0; continue; }
    url="https://$host$path"
    deadline=$(($(date +%s) + 180))
    until curl -fsS -o /dev/null --max-time 10 "$url" 2>/dev/null; do
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
  say "  Media            copy owned or licensed files into $MEDIA_ROOT/movies and $MEDIA_ROOT/series"
  say ""
  say "  Update later     sudo $INSTALL_DIR/scripts/install.sh --update"
  say "  Change settings  sudo $INSTALL_DIR/scripts/install.sh --reconfigure"
  say "  Service status   cd $INSTALL_DIR && docker compose -f docker/compose.yml -f docker/compose.prod.yml ps"
  say "  Install log      $LOG_FILE"
  say "  Back up          $INSTALL_DIR/.env and $INSTALL_DIR/secrets/ (the databases need them)"
  say ""
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
  log "mode=$MODE dir=$INSTALL_DIR"

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
    get_code
    if ((DRY_RUN)); then info "${DIM}would add new .env keys (scripts/secrets.sh)${R}"; else
      (cd "$INSTALL_DIR" && scripts/secrets.sh >> "$LOG_FILE" 2>&1) || die "scripts/secrets.sh failed."
    fi
    start_stack
    run "Building the search index" compose exec -T web python manage.py search_reindex
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
  prepare_media
  start_stack
  if [[ "$MODE" == install ]]; then
    create_owner
    finish_setup
  fi
  check_https
  summary
}

main "$@"
