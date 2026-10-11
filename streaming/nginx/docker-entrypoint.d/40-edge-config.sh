#!/bin/sh
# Smart IPTV media edge (ADR-0007): render the deployment-specific nginx config from
# EDGE_* variables before nginx starts. The official image's /docker-entrypoint.sh
# runs every executable script in /docker-entrypoint.d/; any error here stops the
# container with a message naming the variable. streaming/README.md lists them all.
set -eu
# No globbing: the list variables below are split on whitespace.
set -f

ME=${0##*/}
SRC=${EDGE_CONFIG_SRC:-/etc/nginx/edge}
OUT=/etc/nginx/conf.d/edge

die() {
    echo "$ME: error: $*" >&2
    exit 1
}

note() {
    if [ -z "${NGINX_ENTRYPOINT_QUIET_LOGS:-}" ]; then
        echo "$ME: $*"
    fi
}

# matches VALUE ERE: VALUE is a single line matching the extended regex in full.
matches() {
    case $1 in
        *"
"*) return 1 ;;
    esac
    printf '%s\n' "$1" | grep -Eq "^(${2})\$"
}

# check NAME VALUE ERE WHAT: stop unless VALUE (the value of NAME) matches ERE.
check() {
    matches "$2" "$3" || die "$1 must be $4 (got '$2')"
}

# list VALUE: a comma- or space-separated list, one item per word.
list() {
    printf '%s' "$1" | tr ',' ' '
}

HOST='[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?'
PORT='[0-9]{1,5}'

EDGE_MODE=${EDGE_MODE:-local}
EDGE_ID=${EDGE_ID:-$(hostname)}
EDGE_AUTH_UPSTREAM=${EDGE_AUTH_UPSTREAM:-}
EDGE_AUTH_HOST=${EDGE_AUTH_HOST:-${EDGE_AUTH_UPSTREAM%:*}}
EDGE_AUTH_CACHE_TTL=${EDGE_AUTH_CACHE_TTL:-60s}
EDGE_KEYS_FILE=${EDGE_KEYS_FILE:-/run/secrets/media_token_keys.json}
EDGE_MEDIA_ROOT=${EDGE_MEDIA_ROOT:-/srv/media}
EDGE_RESOLVER=${EDGE_RESOLVER:-$(awk '$1 == "nameserver" && $2 !~ /:/ { printf "%s%s", sep, $2; sep = " " }' /etc/resolv.conf)}
EDGE_RESOLVER_VALID=${EDGE_RESOLVER_VALID:-10s}
EDGE_REAL_IP_FROM=${EDGE_REAL_IP_FROM:-}
EDGE_CORS_ORIGINS=${EDGE_CORS_ORIGINS:-}
EDGE_REQUEST_ERROR_LOG_LEVEL=${EDGE_REQUEST_ERROR_LOG_LEVEL:-emerg}
EDGE_ORIGIN_URL=${EDGE_ORIGIN_URL:-}
EDGE_MEDIA_CACHE_SIZE=${EDGE_MEDIA_CACHE_SIZE:-10g}
EDGE_LIVE_ROOT=${EDGE_LIVE_ROOT:-}
EDGE_LIVE_RELAY=${EDGE_LIVE_RELAY:-}
EDGE_ORIGIN_SCHEME=
EDGE_ORIGIN_AUTHORITY=
EDGE_ORIGIN_HOST=
EDGE_ORIGIN_HOSTPORT=
EDGE_ORIGIN_PREFIX=

check EDGE_MODE "$EDGE_MODE" 'local|s3' 'local or s3'
check EDGE_ID "$EDGE_ID" '[A-Za-z0-9_.-]{1,64}' '1-64 characters of [A-Za-z0-9_.-]'
[ -n "$EDGE_AUTH_UPSTREAM" ] ||
    die "EDGE_AUTH_UPSTREAM is required: host:port of Django's internal endpoint (e.g. web:8000)"
check EDGE_AUTH_UPSTREAM "$EDGE_AUTH_UPSTREAM" "$HOST:$PORT" 'host:port'
check EDGE_AUTH_HOST "$EDGE_AUTH_HOST" "$HOST(:$PORT)?" 'a host name, optionally with :port'
check EDGE_AUTH_CACHE_TTL "$EDGE_AUTH_CACHE_TTL" '[1-9][0-9]{0,4}(ms|s|m)?' 'an nginx time such as 60s'
check EDGE_KEYS_FILE "$EDGE_KEYS_FILE" '/[A-Za-z0-9_./-]+' 'an absolute path'
[ -f "$EDGE_KEYS_FILE" ] && [ -r "$EDGE_KEYS_FILE" ] ||
    die "EDGE_KEYS_FILE ($EDGE_KEYS_FILE) is missing or unreadable"
[ -n "$EDGE_RESOLVER" ] || die "EDGE_RESOLVER is required: no IPv4 nameserver in /etc/resolv.conf"
check EDGE_RESOLVER "$EDGE_RESOLVER" '[][0-9A-Fa-f.:]+( [][0-9A-Fa-f.:]+)*' 'resolver addresses separated by spaces'
check EDGE_RESOLVER_VALID "$EDGE_RESOLVER_VALID" '[1-9][0-9]{0,4}s' 'seconds such as 10s'
check EDGE_REQUEST_ERROR_LOG_LEVEL "$EDGE_REQUEST_ERROR_LOG_LEVEL" \
    'debug|info|notice|warn|error|crit|alert|emerg' 'an nginx log level'
for cidr in $(list "$EDGE_REAL_IP_FROM"); do
    check EDGE_REAL_IP_FROM "$cidr" '[0-9A-Fa-f.:]+(/[0-9]{1,3})?' 'IP addresses or CIDR blocks'
done
for origin in $(list "$EDGE_CORS_ORIGINS"); do
    check EDGE_CORS_ORIGINS "$origin" "\\*|https?://$HOST(:$PORT)?" 'origins such as https://app.example.com, or *'
done

# Live TV (ADR-0017): both or neither.
if [ -n "$EDGE_LIVE_ROOT$EDGE_LIVE_RELAY" ]; then
    [ -n "$EDGE_LIVE_ROOT" ] && [ -n "$EDGE_LIVE_RELAY" ] ||
        die "EDGE_LIVE_ROOT and EDGE_LIVE_RELAY go together: set both for live TV, or neither"
    check EDGE_LIVE_ROOT "$EDGE_LIVE_ROOT" '/[A-Za-z0-9_./-]*[A-Za-z0-9_-]' \
        'an absolute path without a trailing slash'
    [ -d "$EDGE_LIVE_ROOT" ] || die "EDGE_LIVE_ROOT ($EDGE_LIVE_ROOT) is not a directory"
    check EDGE_LIVE_RELAY "$EDGE_LIVE_RELAY" "$HOST:$PORT" 'host:port'
fi

case $EDGE_MODE in
    local)
        check EDGE_MEDIA_ROOT "$EDGE_MEDIA_ROOT" '/[A-Za-z0-9_./-]*[A-Za-z0-9_-]' \
            'an absolute path without a trailing slash'
        [ -d "$EDGE_MEDIA_ROOT" ] || die "EDGE_MEDIA_ROOT ($EDGE_MEDIA_ROOT) is not a directory"
        ;;
    s3)
        [ -n "$EDGE_ORIGIN_URL" ] || die "EDGE_ORIGIN_URL is required when EDGE_MODE=s3"
        # The value is not echoed: a URL with user:password@ would end up in the logs.
        matches "$EDGE_ORIGIN_URL" "https?://$HOST(:$PORT)?(/[A-Za-z0-9_.~-]+)*/?" ||
            die "EDGE_ORIGIN_URL must be http(s)://host[:port][/path], without credentials or a query"
        EDGE_ORIGIN_SCHEME=${EDGE_ORIGIN_URL%%://*}
        rest=${EDGE_ORIGIN_URL#*://}
        EDGE_ORIGIN_AUTHORITY=${rest%%/*}
        EDGE_ORIGIN_HOST=${EDGE_ORIGIN_AUTHORITY%%:*}
        port=${EDGE_ORIGIN_AUTHORITY#"$EDGE_ORIGIN_HOST"}
        port=${port#:}
        if [ -z "$port" ]; then
            if [ "$EDGE_ORIGIN_SCHEME" = https ]; then port=443; else port=80; fi
        fi
        EDGE_ORIGIN_HOSTPORT=$EDGE_ORIGIN_HOST:$port
        EDGE_ORIGIN_PREFIX=${rest#"$EDGE_ORIGIN_AUTHORITY"}
        EDGE_ORIGIN_PREFIX=${EDGE_ORIGIN_PREFIX%/}
        check EDGE_MEDIA_CACHE_SIZE "$EDGE_MEDIA_CACHE_SIZE" '[1-9][0-9]{0,5}[kKmMgG]' 'a size such as 10g'
        ;;
esac

# Fail now, with a readable message, rather than serving 503s: the same rules as
# token.js, and no key material in the output.
if ! keys=$(njs -q -m -p "$SRC/njs" "$SRC/njs/check_keys.js" "$EDGE_KEYS_FILE" 2>&1); then
    die "$(printf '%s\n' "$keys" | grep -m 1 'Error' || echo "EDGE_KEYS_FILE ($EDGE_KEYS_FILE) is invalid")"
fi

export EDGE_ID EDGE_AUTH_UPSTREAM EDGE_AUTH_HOST EDGE_AUTH_CACHE_TTL EDGE_KEYS_FILE \
    EDGE_MEDIA_ROOT EDGE_RESOLVER EDGE_RESOLVER_VALID EDGE_REQUEST_ERROR_LOG_LEVEL \
    EDGE_ORIGIN_SCHEME EDGE_ORIGIN_AUTHORITY EDGE_ORIGIN_HOST EDGE_ORIGIN_HOSTPORT \
    EDGE_ORIGIN_PREFIX EDGE_MEDIA_CACHE_SIZE EDGE_LIVE_ROOT EDGE_LIVE_RELAY
# Only these variables are substituted; nginx's own $variables pass through untouched.
# shellcheck disable=SC2016 # envsubst's SHELL-FORMAT: literal ${NAME}s, not expansions
VARS='${EDGE_ID} ${EDGE_AUTH_UPSTREAM} ${EDGE_AUTH_HOST} ${EDGE_AUTH_CACHE_TTL}
${EDGE_KEYS_FILE} ${EDGE_MEDIA_ROOT} ${EDGE_RESOLVER} ${EDGE_RESOLVER_VALID}
${EDGE_REQUEST_ERROR_LOG_LEVEL} ${EDGE_ORIGIN_SCHEME} ${EDGE_ORIGIN_AUTHORITY}
${EDGE_ORIGIN_HOST} ${EDGE_ORIGIN_HOSTPORT} ${EDGE_ORIGIN_PREFIX} ${EDGE_MEDIA_CACHE_SIZE}
${EDGE_LIVE_ROOT} ${EDGE_LIVE_RELAY}'

render() {
    envsubst "$VARS" < "$SRC/templates/$1.template" > "$OUT/$1"
}

mkdir -p "$OUT"
render http.conf
render server.conf

if [ -n "$EDGE_LIVE_ROOT" ]; then
    envsubst "$VARS" < "$SRC/templates/live-http.conf.template" >> "$OUT/http.conf"
    render live.conf
else
    echo "# Live TV is not configured on this edge (EDGE_LIVE_ROOT, EDGE_LIVE_RELAY)." > "$OUT/live.conf"
fi

if [ -n "$EDGE_REAL_IP_FROM" ]; then
    {
        echo
        echo "# Trusted proxies (EDGE_REAL_IP_FROM): the client is the last untrusted"
        echo "# address in X-Forwarded-For."
        echo "real_ip_header X-Forwarded-For;"
        echo "real_ip_recursive on;"
        for cidr in $(list "$EDGE_REAL_IP_FROM"); do
            echo "set_real_ip_from $cidr;"
        done
    } >> "$OUT/server.conf"
fi

# "*" lets any page read media responses: web IPTV players (an IPTVnator PWA) follow
# the Xtream host's redirect, after which browsers send `Origin: null`. No cookies or
# credential headers are involved: the signed URL is the only credential (ADR-0017).
any_origin=
for origin in $(list "$EDGE_CORS_ORIGINS"); do
    if [ "$origin" = "*" ]; then any_origin=1; fi
done
{
    echo "# Rendered by $ME from EDGE_CORS_ORIGINS: the Origin is echoed when allowed."
    # shellcheck disable=SC2016 # nginx variables, written literally
    echo 'map $http_origin $edge_cors_origin {'
    if [ -n "$any_origin" ]; then
        echo '    default "*";'
    else
        echo '    default "";'
        for origin in $(list "$EDGE_CORS_ORIGINS"); do
            echo "    \"$origin\" \$http_origin;"
        done
    fi
    echo '}'
} > "$OUT/cors.conf"

case $EDGE_MODE in
    local)
        echo "include $SRC/edge.conf;" > "$OUT/mode.conf"
        ;;
    s3)
        render origin.conf
        printf 'include %s/origin.conf;\ninclude %s/edge-s3.conf;\n' "$OUT" "$SRC" > "$OUT/mode.conf"
        ;;
esac

note "$keys"
note "rendered $OUT for EDGE_MODE=$EDGE_MODE, edge $EDGE_ID${EDGE_LIVE_ROOT:+, live TV on}"
