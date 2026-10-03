#!/bin/sh
# Smart IPTV media edge: add the public artwork location (images.conf, mounted at
# /etc/nginx/smart-iptv/images.conf) to the server that 40-edge-config.sh rendered.
# The official image's entrypoint runs this after 40-edge-config.sh (sorted order).
# ADR-0009; slice 3 can move images.conf into streaming/nginx/snippets/ and drop this.
set -eu
out=/etc/nginx/conf.d/edge/server.conf
[ -f "$out" ] || { echo "${0##*/}: $out is missing (40-edge-config.sh did not run)" >&2; exit 1; }
printf '\n# Public artwork (50-images.sh).\ninclude /etc/nginx/smart-iptv/images.conf;\n' >> "$out"
