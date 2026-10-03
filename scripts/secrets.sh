#!/usr/bin/env bash
# Create .env from .env.example with freshly generated development secrets.
# Never overwrites an existing .env; delete it first to regenerate (data volumes
# keep the old passwords, so `make down` and remove the volumes too).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists; leaving it untouched."
  exit 0
fi

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

port_in_use() {
  if command -v ss >/dev/null; then
    ss -ltn | awk '{print $4}' | grep -qE "[:.]$1\$"
  else
    netstat -an 2>/dev/null | grep -i listen | awk '{print $4}' | grep -qE "[:.]$1\$"
  fi
}

umask 077
tmp="$(mktemp .env.XXXXXX)"
trap 'rm -f "$tmp"' EXIT

# Hex keeps every value safe inside URLs and shell quoting.
awk '{
  while (index($0, "__GENERATE__")) {
    cmd = "openssl rand -hex 32"; cmd | getline value; close(cmd)
    sub(/__GENERATE__/, value)
  }
  print
}' .env.example > "$tmp"

if port_in_use 80; then
  sed -i.bak 's/^HTTP_PORT=80$/HTTP_PORT=8080/' "$tmp" && rm -f "$tmp.bak"
  echo "Port 80 is busy on this machine; Traefik will listen on 8080 (HTTP_PORT in .env)."
fi

mv "$tmp" .env
trap - EXIT
echo "Created .env with generated secrets (mode 600). Keep it out of git."
