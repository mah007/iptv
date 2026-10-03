#!/usr/bin/env bash
# Create or complete an env file from .env.example with freshly generated secrets.
#   scripts/secrets.sh          -> .env (the dev stack's)
#   scripts/secrets.sh <path>   -> <path> (e.g. a throwaway file for image smoke tests)
#
# New file: a copy of .env.example with every placeholder replaced by a fresh value.
# Existing file (merge mode): keys of .env.example missing from it are appended,
# placeholders generated; existing lines are never changed. Only the names of the
# added keys are printed, never values.
#
# For the dev .env it also creates secrets/media_token_keys.json (media token keys,
# ADR-0007) when that file is missing.
#
# Placeholders:
#   __GENERATE__         64 hex characters (safe inside URLs and shell quoting)
#   __GENERATE_FERNET__  a Fernet key: urlsafe base64 of 32 random bytes
#
# To regenerate .env from scratch, delete it first: data volumes keep the old
# passwords, so `make down` and remove the volumes too.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-.env}"
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

port_in_use() {
  if command -v ss >/dev/null; then
    ss -ltn | awk '{print $4}' | grep -qE "[:.]$1\$"
  else
    netstat -an 2>/dev/null | grep -i listen | awk '{print $4}' | grep -qE "[:.]$1\$"
  fi
}

# Copy stdin to stdout, replacing each placeholder with a fresh random value.
generate() {
  awk '{
    while (index($0, "__GENERATE_FERNET__")) {
      cmd = "openssl rand -base64 32 | tr \"+/\" \"-_\""; cmd | getline value; close(cmd)
      sub(/__GENERATE_FERNET__/, value)
    }
    while (index($0, "__GENERATE__")) {
      cmd = "openssl rand -hex 32"; cmd | getline value; close(cmd)
      sub(/__GENERATE__/, value)
    }
    print
  }'
}

# The dev stack moves Traefik to 8080 when something already listens on port 80.
pick_port() {
  local file="$1"
  if [[ "$out" == ".env" ]] && grep -q '^HTTP_PORT=80$' "$file" && port_in_use 80; then
    sed -i.bak 's/^HTTP_PORT=80$/HTTP_PORT=8080/' "$file" && rm -f "$file.bak"
    echo "Port 80 is busy on this machine; Traefik will listen on 8080 (HTTP_PORT in $out)."
  fi
}

# Media token keys for the edge and Django: one random key, kid k1. Rotate them with
# streaming/tools/sign_token.py (streaming/README.md).
media_keys() {
  local keys=secrets/media_token_keys.json
  [[ "$out" == ".env" && ! -s "$keys" ]] || return 0
  command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
  mkdir -p secrets
  chmod 700 secrets
  python3 streaming/tools/sign_token.py genkeys --kid k1 > "$keys.tmp"
  # The edge's master reads it as root without DAC_OVERRIDE, so the file itself is
  # world-readable; the secrets/ folder (700) keeps other host users out.
  chmod 644 "$keys.tmp"
  mv "$keys.tmp" "$keys"
  echo "Created $keys (media token keys)."
}

umask 077
media_keys

if [[ -f "$out" && -s "$out" ]]; then
  additions="$(mktemp)"
  trap 'rm -f "$additions"' EXIT
  while IFS= read -r line; do
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || continue
    grep -qE "^${BASH_REMATCH[1]}=" "$out" || printf '%s\n' "$line"
  done < .env.example | generate > "$additions"
  if [[ ! -s "$additions" ]]; then
    echo "$out already has every key in .env.example; leaving it untouched."
    exit 0
  fi
  pick_port "$additions"
  # Keep the current last line intact when the file lacks a final newline.
  [[ -z "$(tail -c 1 "$out")" ]] || printf '\n' >> "$out"
  cat "$additions" >> "$out"
  echo "Added to $out: $(cut -d= -f1 "$additions" | paste -sd ' ' -)"
  exit 0
fi

tmp="$(mktemp "${out}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
generate < .env.example > "$tmp"
pick_port "$tmp"
mv "$tmp" "$out"
trap - EXIT
echo "Created $out with generated secrets (mode 600). Keep it out of git."
