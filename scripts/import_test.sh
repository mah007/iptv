#!/usr/bin/env bash
# The import round trip on the dev stack (make import-test; ADR-0025).
#
# A throwaway container from the dev image joins the egress network as
# `iptv-import-test` and serves synthetic clips with rclone: SFTP (user and a generated
# password) and HTTPS (a throwaway CA and certificate; the dev importer trusts that CA
# through IMPORT_EXTRA_CA_FILE, honoured with DEBUG only). `manage.py import_test` then
# adds a library over media/import-test and an SFTP source, Tests it through the
# importer, imports a folder (a clip, its subtitle and a poster that must stay behind)
# and a direct link, and waits until the scanner has probed both files. Everything it
# made is removed afterwards; the container isn't in Traefik. Never the internet.
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml)
network=smart-iptv-egress
server=iptv-import-test
image=smart-iptv/app:dev-tools
work=media/.import-test
library=media/import-test
user=import-tester
password="$(openssl rand -hex 16)"
uid="$(id -u)"
gid="$(id -g)"

cleanup() {
  docker rm -f "$server" >/dev/null 2>&1 || true
  rm -rf "$work" "$library"
}
trap cleanup EXIT
cleanup

started=$SECONDS
[[ -n "$("${compose[@]}" ps -q --status running importer 2>/dev/null)" ]] || {
  echo "The importer isn't running: make up" >&2
  exit 1
}

echo "Import round trip (SFTP and an HTTPS link, through the importer):"
mkdir -p "$work/files/Films/Import Test Clip (2026)" "$work/files/links" "$library"
# Two 2-second clips (FFmpeg test patterns), a subtitle and a poster that must not come.
docker run --rm --user "$uid:$gid" -v "$PWD/$work/files:/out" --entrypoint sh "$image" -c '
  ffmpeg -loglevel error -f lavfi -i testsrc=size=320x180:rate=25 -f lavfi -i sine=frequency=440 \
    -t 2 -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart -shortest \
    "/out/Films/Import Test Clip (2026)/Import Test Clip (2026).mp4"
  ffmpeg -loglevel error -f lavfi -i testsrc2=size=320x180:rate=25 -t 2 -c:v libx264 \
    -pix_fmt yuv420p -movflags +faststart "/out/links/Import Link Clip (2026).mp4"'
printf '1\n00:00:00,500 --> 00:00:01,500\nImport test\n' \
  > "$work/files/Films/Import Test Clip (2026)/Import Test Clip (2026).en.srt"
printf 'not a video' > "$work/files/Films/Import Test Clip (2026)/poster.jpg"

# A throwaway CA and a certificate for the server's name on the egress network.
# Python 3.13 verifies strictly (RFC 5280): key usages and key identifiers are required.
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 \
  -subj "/CN=import-test CA" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" -keyout "$work/ca.key" -out "$work/ca.pem" 2>/dev/null
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=$server" \
  -keyout "$work/server.key" -out "$work/server.csr" 2>/dev/null
openssl x509 -req -in "$work/server.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" \
  -CAcreateserial -days 1 -out "$work/server.pem" -extfile <(printf '%s\n' \
    "subjectAltName=DNS:$server" "keyUsage=critical,digitalSignature" \
    "extendedKeyUsage=serverAuth" "authorityKeyIdentifier=keyid,issuer" \
    "subjectKeyIdentifier=hash") 2>/dev/null
chmod 644 "$work"/*.pem
chmod 600 "$work"/*.key

# SFTP signs in with the user and password (environment, not the command line); the
# HTTPS server takes none (a link with credentials is refused anyway).
docker run -d --name "$server" --network "$network" --user "$uid:$gid" -l traefik.enable=false \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  -e HOME=/tmp -e RCLONE_CONFIG=/dev/null -e SFTP_USER="$user" -e SFTP_PASS="$password" \
  -v "$PWD/$work:/srv:ro" --entrypoint sh "$image" -c '
    RCLONE_USER="$SFTP_USER" RCLONE_PASS="$SFTP_PASS" rclone serve sftp /srv/files --addr :2022 &
    env -u SFTP_USER -u SFTP_PASS rclone serve http /srv/files/links --addr :8443 \
      --cert /srv/server.pem --key /srv/server.key &
    wait' >/dev/null
deadline=$((SECONDS + 30))
until docker exec "$server" sh -c 'ls /tmp/.cache/rclone/serve-sftp/id_ed25519 >/dev/null 2>&1'; do
  ((SECONDS < deadline)) || { echo "  FAIL  the test server did not start" >&2; docker logs "$server" >&2; exit 1; }
  sleep 1
done
echo "  ok    test server up (SFTP :2022, HTTPS :8443) in $((SECONDS - started)) s"

printf '%s' "$password" | "${compose[@]}" exec -T web python manage.py import_test \
  --sftp-host "$server" --sftp-port 2022 --sftp-user "$user" \
  --link "https://$server:8443/Import%20Link%20Clip%20(2026).mp4"
echo "make import-test passed in $((SECONDS - started)) s."
