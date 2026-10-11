#!/bin/sh
# Valkey with one ACL user per kind of client (G-38, ADR-0020). Renders the ACL file
# into tmpfs from the environment, with SHA-256 hashes, never the passwords, then hands
# over to the image's own entrypoint, which gives /data to the valkey user (earlier
# releases ran Valkey as root, so an existing volume holds root's files) and drops root
# before starting valkey-server with the arguments given.
#   default   the operator's account and the test lanes: every command
#   app       the app services: every command except the administrative and
#             destructive ones (FLUSHALL, FLUSHDB, CONFIG, DEBUG, MODULE, KEYS, ...)
#   exporter  redis_exporter (monitoring overlay): read-only metrics, as its README advises
# A password that is empty turns its user off.
set -eu

acl=/tmp/users.acl
umask 077

user_line() { # name password rules
    if [ -n "$2" ]; then
        hash=$(printf '%s' "$2" | sha256sum | cut -d' ' -f1)
        printf 'user %s on #%s %s\n' "$1" "$hash" "$3"
    else
        printf 'user %s off resetpass -@all\n' "$1"
    fi
}

: "${VALKEY_DEFAULT_PASSWORD:?the default user needs a password}"
{
    user_line default "$VALKEY_DEFAULT_PASSWORD" "~* &* +@all"
    user_line app "${VALKEY_APP_PASSWORD:-}" \
        "~* &* +@all -flushall -flushdb -config -debug -module -keys -shutdown -acl -monitor -save -bgsave -bgrewriteaof -replicaof -slaveof -failover -migrate -sync -psync -swapdb -cluster -lastsave -latency|reset -slowlog|reset"
    user_line exporter "${VALKEY_EXPORTER_PASSWORD:-}" \
        "-@all +@connection +memory -readonly +strlen +config|get +xinfo +pfcount -quit +zcard +type +xlen -readwrite -command +client -wait +scard +llen +hlen +get +eval +slowlog +cluster|info +cluster|slots +cluster|nodes -hello -echo +info +latency +scan -reset -auth -asking"
} > "$acl"
chown valkey:valkey "$acl"

exec /usr/local/bin/docker-entrypoint.sh valkey-server --aclfile "$acl" "$@"
