#!/bin/sh
# Smart IPTV media edge (ADR-0007, ADR-0022): render the deployment-specific nginx config
# from EDGE_* variables before nginx starts. The official image's /docker-entrypoint.sh
# runs every executable script in /docker-entrypoint.d/; any error here stops the
# container with a message naming the variable. streaming/README.md lists them all.
#
# Two shapes of edge:
#   - the control host's edge (nginx-stream): plain HTTP behind Traefik, the key file
#     from a Docker secret, stream-auth on the internal network (EDGE_AUTH_UPSTREAM);
#   - a standalone edge (docker/compose.edge.yml): EDGE_CONTROL_URL set, its own TLS
#     (EDGE_TLS), keys and the origin's read-only key from the control channel, and
#     stream-auth on the control plane's edges host with its bearer secret.
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

# secret_file NAME PATH: the first line of a secret file, checked (never echoed).
secret_file() {
    [ -f "$2" ] && [ -r "$2" ] || die "$1 ($2) is missing or unreadable"
    value=$(head -n 1 "$2" | tr -d '\r\n')
    matches "$value" '[A-Za-z0-9_+/=.-]{16,255}' ||
        die "$1 ($2) must hold one line of 16-255 letters, digits or _+/=.-"
    printf '%s' "$value"
}

# url_parts NAME URL: split an http(s) URL without credentials into SCHEME, AUTHORITY,
# HOST, HOSTPORT and PREFIX (globals).
url_parts() {
    matches "$2" "https?://$HOST(:$PORT)?(/[A-Za-z0-9_.~-]+)*/?" ||
        die "$1 must be http(s)://host[:port][/path], without credentials or a query"
    SCHEME=${2%%://*}
    rest=${2#*://}
    AUTHORITY=${rest%%/*}
    URL_HOST=${AUTHORITY%%:*}
    port=${AUTHORITY#"$URL_HOST"}
    port=${port#:}
    if [ -z "$port" ]; then
        if [ "$SCHEME" = https ]; then port=443; else port=80; fi
    fi
    HOSTPORT=$URL_HOST:$port
    PREFIX=${rest#"$AUTHORITY"}
    PREFIX=${PREFIX%/}
}

HOST='[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?'
PORT='[0-9]{1,5}'
SIZE='0|[1-9][0-9]{0,5}[kKmMgG]?'

EDGE_MODE=${EDGE_MODE:-local}
EDGE_ID=${EDGE_ID:-$(hostname)}
EDGE_CONTROL_URL=${EDGE_CONTROL_URL:-}
EDGE_CONTROL_HOST=${EDGE_CONTROL_HOST:-}
EDGE_CONTROL_TOKEN_FILE=${EDGE_CONTROL_TOKEN_FILE:-/run/secrets/edge_token}
EDGE_CONTROL_CA_FILE=${EDGE_CONTROL_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}
EDGE_AUTH_UPSTREAM=${EDGE_AUTH_UPSTREAM:-}
EDGE_AUTH_HOST=${EDGE_AUTH_HOST:-${EDGE_AUTH_UPSTREAM%:*}}
EDGE_AUTH_TOKEN_FILE=${EDGE_AUTH_TOKEN_FILE:-/run/secrets/edge_auth_token}
EDGE_AUTH_CACHE_TTL=${EDGE_AUTH_CACHE_TTL:-60s}
EDGE_KEYS_FILE=${EDGE_KEYS_FILE:-/run/secrets/media_token_keys.json}
EDGE_MEDIA_ROOT=${EDGE_MEDIA_ROOT:-/srv/media}
EDGE_RESOLVER=${EDGE_RESOLVER:-$(awk '$1 == "nameserver" && $2 !~ /:/ { printf "%s%s", sep, $2; sep = " " }' /etc/resolv.conf)}
EDGE_RESOLVER_VALID=${EDGE_RESOLVER_VALID:-10s}
EDGE_REAL_IP_FROM=${EDGE_REAL_IP_FROM:-}
EDGE_CORS_ORIGINS=${EDGE_CORS_ORIGINS:-}
EDGE_REQUEST_ERROR_LOG_LEVEL=${EDGE_REQUEST_ERROR_LOG_LEVEL:-emerg}
EDGE_ORIGIN_URL=${EDGE_ORIGIN_URL:-}
EDGE_ORIGIN_CREDENTIALS_FILE=${EDGE_ORIGIN_CREDENTIALS_FILE:-}
EDGE_MEDIA_CACHE_SIZE=${EDGE_MEDIA_CACHE_SIZE:-10g}
EDGE_LIVE_ROOT=${EDGE_LIVE_ROOT:-}
EDGE_LIVE_RELAY=${EDGE_LIVE_RELAY:-}
EDGE_CDN=${EDGE_CDN:-off}
EDGE_CDN_SECRET_FILE=${EDGE_CDN_SECRET_FILE:-}
EDGE_TLS=${EDGE_TLS:-off}
EDGE_DOMAIN=${EDGE_DOMAIN:-}
EDGE_ACME_DIRECTORY=${EDGE_ACME_DIRECTORY:-https://acme-v02.api.letsencrypt.org/directory}
EDGE_ACME_EMAIL=${EDGE_ACME_EMAIL:-}
EDGE_ACME_CA_FILE=${EDGE_ACME_CA_FILE:-}
EDGE_ACME_STATE_DIR=${EDGE_ACME_STATE_DIR:-/var/lib/nginx-acme}
EDGE_TLS_CERT=${EDGE_TLS_CERT:-}
EDGE_TLS_KEY=${EDGE_TLS_KEY:-}
EDGE_RATE_LIMIT=${EDGE_RATE_LIMIT:-3m}
EDGE_RATE_LIMIT_UHD=${EDGE_RATE_LIMIT_UHD:-10m}
EDGE_RATE_AFTER=${EDGE_RATE_AFTER:-16m}
EDGE_HSTS=
EDGE_ORIGIN_SCHEME=
EDGE_ORIGIN_AUTHORITY=
EDGE_ORIGIN_HOST=
EDGE_ORIGIN_HOSTPORT=
EDGE_ORIGIN_PREFIX=
EDGE_CONTROL_SCHEME=
EDGE_CONTROL_HOSTPORT=
EDGE_CONTROL_HOST_HEADER=
EDGE_CONTROL_SNI=
EDGE_ACME_CONTACT_LINE=
EDGE_ACME_CA_LINE=

check EDGE_MODE "$EDGE_MODE" 'local|s3' 'local or s3'
check EDGE_ID "$EDGE_ID" '[A-Za-z0-9_.-]{1,64}' '1-64 characters of [A-Za-z0-9_.-]'
check EDGE_AUTH_CACHE_TTL "$EDGE_AUTH_CACHE_TTL" '[1-9][0-9]{0,4}(ms|s|m)?' 'an nginx time such as 60s'
[ -n "$EDGE_RESOLVER" ] || die "EDGE_RESOLVER is required: no IPv4 nameserver in /etc/resolv.conf"
check EDGE_RESOLVER "$EDGE_RESOLVER" '[][0-9A-Fa-f.:]+( [][0-9A-Fa-f.:]+)*' 'resolver addresses separated by spaces'
check EDGE_RESOLVER_VALID "$EDGE_RESOLVER_VALID" '[1-9][0-9]{0,4}s' 'seconds such as 10s'
check EDGE_REQUEST_ERROR_LOG_LEVEL "$EDGE_REQUEST_ERROR_LOG_LEVEL" \
    'debug|info|notice|warn|error|crit|alert|emerg' 'an nginx log level'
check EDGE_RATE_LIMIT "$EDGE_RATE_LIMIT" "$SIZE" 'a rate such as 3m (bytes per second; 0: none)'
check EDGE_RATE_LIMIT_UHD "$EDGE_RATE_LIMIT_UHD" "$SIZE" 'a rate such as 10m'
check EDGE_RATE_AFTER "$EDGE_RATE_AFTER" "$SIZE" 'a size such as 16m'
check EDGE_CDN "$EDGE_CDN" 'on|off' 'on or off'
check EDGE_TLS "$EDGE_TLS" 'off|acme|file' 'off, acme or file'
for cidr in $(list "$EDGE_REAL_IP_FROM"); do
    check EDGE_REAL_IP_FROM "$cidr" '[0-9A-Fa-f.:]+(/[0-9]{1,3})?' 'IP addresses or CIDR blocks'
done
for origin in $(list "$EDGE_CORS_ORIGINS"); do
    check EDGE_CORS_ORIGINS "$origin" "\\*|https?://$HOST(:$PORT)?" 'origins such as https://app.example.com, or *'
done

# The secrets nginx's master reads at load (js_preload_object edge_secrets): written to
# the tmpfs config folder, root only, and never echoed.
CONTROL_TOKEN=
INTERNAL_TOKEN=
CDN_SECRET=

# Where the keys and stream-auth come from.
if [ -n "$EDGE_CONTROL_URL" ]; then
    url_parts EDGE_CONTROL_URL "$EDGE_CONTROL_URL"
    [ -z "$PREFIX" ] || die "EDGE_CONTROL_URL must be the edges host only, e.g. https://edges.example.com"
    EDGE_CONTROL_URL=$SCHEME://$AUTHORITY
    EDGE_CONTROL_SCHEME=$SCHEME
    EDGE_CONTROL_HOSTPORT=$HOSTPORT
    EDGE_CONTROL_SNI=$URL_HOST
    if [ -n "$EDGE_CONTROL_HOST" ]; then
        check EDGE_CONTROL_HOST "$EDGE_CONTROL_HOST" "$HOST(:$PORT)?" 'a host name, optionally with :port'
        EDGE_CONTROL_HOST_HEADER=$EDGE_CONTROL_HOST
    else
        EDGE_CONTROL_HOST_HEADER=$AUTHORITY
    fi
    [ -f "$EDGE_CONTROL_CA_FILE" ] || die "EDGE_CONTROL_CA_FILE ($EDGE_CONTROL_CA_FILE) is missing"
    CONTROL_TOKEN=$(secret_file EDGE_CONTROL_TOKEN_FILE "$EDGE_CONTROL_TOKEN_FILE")
else
    [ -n "$EDGE_AUTH_UPSTREAM" ] ||
        die "EDGE_AUTH_UPSTREAM is required: host:port of Django's internal endpoint (e.g. web:8000), or EDGE_CONTROL_URL for a standalone edge"
    check EDGE_AUTH_UPSTREAM "$EDGE_AUTH_UPSTREAM" "$HOST:$PORT" 'host:port'
    check EDGE_AUTH_HOST "$EDGE_AUTH_HOST" "$HOST(:$PORT)?" 'a host name, optionally with :port'
    check EDGE_KEYS_FILE "$EDGE_KEYS_FILE" '/[A-Za-z0-9_./-]+' 'an absolute path'
    [ -f "$EDGE_KEYS_FILE" ] && [ -r "$EDGE_KEYS_FILE" ] ||
        die "EDGE_KEYS_FILE ($EDGE_KEYS_FILE) is missing or unreadable"
    # G-16 (ADR-0021): the internal stream-auth's shared secret, sent as X-Edge-Auth when
    # the Docker secret is mounted (Django refuses calls without it).
    if [ -e "$EDGE_AUTH_TOKEN_FILE" ]; then
        INTERNAL_TOKEN=$(secret_file EDGE_AUTH_TOKEN_FILE "$EDGE_AUTH_TOKEN_FILE")
    else
        note "no $EDGE_AUTH_TOKEN_FILE: stream-auth calls go without X-Edge-Auth"
    fi
fi
if [ -n "$EDGE_CDN_SECRET_FILE" ]; then
    CDN_SECRET=$(secret_file EDGE_CDN_SECRET_FILE "$EDGE_CDN_SECRET_FILE")
fi

# Live TV (ADR-0017): both or neither; never on a standalone edge (control host only).
if [ -n "$EDGE_LIVE_ROOT$EDGE_LIVE_RELAY" ]; then
    [ -n "$EDGE_LIVE_ROOT" ] && [ -n "$EDGE_LIVE_RELAY" ] ||
        die "EDGE_LIVE_ROOT and EDGE_LIVE_RELAY go together: set both for live TV, or neither"
    [ -z "$EDGE_CONTROL_URL" ] || die "live TV is served by the control host's edge only (ADR-0022)"
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
        url_parts EDGE_ORIGIN_URL "$EDGE_ORIGIN_URL"
        EDGE_ORIGIN_SCHEME=$SCHEME
        EDGE_ORIGIN_AUTHORITY=$AUTHORITY
        EDGE_ORIGIN_HOST=$URL_HOST
        EDGE_ORIGIN_HOSTPORT=$HOSTPORT
        EDGE_ORIGIN_PREFIX=$PREFIX
        check EDGE_MEDIA_CACHE_SIZE "$EDGE_MEDIA_CACHE_SIZE" '[1-9][0-9]{0,5}[kKmMgG]' 'a size such as 10g'
        if [ -n "$EDGE_ORIGIN_CREDENTIALS_FILE" ]; then
            [ -f "$EDGE_ORIGIN_CREDENTIALS_FILE" ] ||
                die "EDGE_ORIGIN_CREDENTIALS_FILE ($EDGE_ORIGIN_CREDENTIALS_FILE) is missing"
        fi
        ;;
esac

case $EDGE_TLS in
    off) ;;
    acme | file)
        [ -n "$EDGE_DOMAIN" ] || die "EDGE_DOMAIN is required with EDGE_TLS=$EDGE_TLS"
        check EDGE_DOMAIN "$EDGE_DOMAIN" "$HOST" 'a host name such as media2.example.com'
        EDGE_HSTS="max-age=31536000"
        ;;
esac
if [ "$EDGE_TLS" = acme ]; then
    matches "$EDGE_ACME_DIRECTORY" "https://$HOST(:$PORT)?(/[A-Za-z0-9_.~-]+)*/?" ||
        die "EDGE_ACME_DIRECTORY must be an https URL"
    if [ -n "$EDGE_ACME_EMAIL" ]; then
        check EDGE_ACME_EMAIL "$EDGE_ACME_EMAIL" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' 'an email address'
        EDGE_ACME_CONTACT_LINE="contact mailto:$EDGE_ACME_EMAIL;"
    fi
    if [ -n "$EDGE_ACME_CA_FILE" ]; then
        [ -f "$EDGE_ACME_CA_FILE" ] || die "EDGE_ACME_CA_FILE ($EDGE_ACME_CA_FILE) is missing"
        EDGE_ACME_CA_LINE="ssl_trusted_certificate $EDGE_ACME_CA_FILE;"
    fi
    check EDGE_ACME_STATE_DIR "$EDGE_ACME_STATE_DIR" '/[A-Za-z0-9_./-]+' 'an absolute path'
    [ -d "$EDGE_ACME_STATE_DIR" ] && [ -w "$EDGE_ACME_STATE_DIR" ] ||
        die "EDGE_ACME_STATE_DIR ($EDGE_ACME_STATE_DIR) must be a writable folder (a volume)"
fi
if [ "$EDGE_TLS" = file ]; then
    [ -f "$EDGE_TLS_CERT" ] && [ -f "$EDGE_TLS_KEY" ] ||
        die "EDGE_TLS_CERT and EDGE_TLS_KEY must name the certificate chain and its key"
fi

# Fail now, with a readable message, rather than serving 503s: the same rules as
# token.js, and no key material in the output. (A standalone edge's keys arrive on the
# control channel and are checked there.)
keys="keys from the control plane (${EDGE_CONTROL_URL:-})"
if [ -z "$EDGE_CONTROL_URL" ]; then
    if ! keys=$(njs -q -m -p "$SRC/njs" "$SRC/njs/check_keys.js" "$EDGE_KEYS_FILE" 2>&1); then
        die "$(printf '%s\n' "$keys" | grep -m 1 'Error' || echo "EDGE_KEYS_FILE ($EDGE_KEYS_FILE) is invalid")"
    fi
fi

export EDGE_ID EDGE_MODE EDGE_AUTH_UPSTREAM EDGE_AUTH_HOST EDGE_AUTH_CACHE_TTL EDGE_KEYS_FILE \
    EDGE_MEDIA_ROOT EDGE_RESOLVER EDGE_RESOLVER_VALID EDGE_REQUEST_ERROR_LOG_LEVEL \
    EDGE_ORIGIN_URL EDGE_ORIGIN_SCHEME EDGE_ORIGIN_AUTHORITY EDGE_ORIGIN_HOST \
    EDGE_ORIGIN_HOSTPORT EDGE_ORIGIN_PREFIX EDGE_MEDIA_CACHE_SIZE EDGE_LIVE_ROOT \
    EDGE_LIVE_RELAY EDGE_CONTROL_URL EDGE_CONTROL_HOST EDGE_CONTROL_SCHEME \
    EDGE_CONTROL_HOSTPORT EDGE_CONTROL_HOST_HEADER EDGE_CONTROL_SNI EDGE_CONTROL_CA_FILE \
    EDGE_RATE_LIMIT EDGE_RATE_LIMIT_UHD EDGE_RATE_AFTER EDGE_HSTS EDGE_DOMAIN \
    EDGE_ACME_DIRECTORY EDGE_ACME_CONTACT_LINE EDGE_ACME_CA_LINE EDGE_ACME_STATE_DIR
# Only these variables are substituted; nginx's own $variables pass through untouched.
# shellcheck disable=SC2016 # envsubst's SHELL-FORMAT: literal ${NAME}s, not expansions
VARS='${EDGE_ID} ${EDGE_MODE} ${EDGE_AUTH_UPSTREAM} ${EDGE_AUTH_HOST} ${EDGE_AUTH_CACHE_TTL}
${EDGE_KEYS_FILE} ${EDGE_MEDIA_ROOT} ${EDGE_RESOLVER} ${EDGE_RESOLVER_VALID}
${EDGE_REQUEST_ERROR_LOG_LEVEL} ${EDGE_ORIGIN_URL} ${EDGE_ORIGIN_SCHEME} ${EDGE_ORIGIN_AUTHORITY}
${EDGE_ORIGIN_HOST} ${EDGE_ORIGIN_HOSTPORT} ${EDGE_ORIGIN_PREFIX} ${EDGE_MEDIA_CACHE_SIZE}
${EDGE_LIVE_ROOT} ${EDGE_LIVE_RELAY} ${EDGE_CONTROL_URL} ${EDGE_CONTROL_HOST}
${EDGE_CONTROL_SCHEME} ${EDGE_CONTROL_HOSTPORT} ${EDGE_CONTROL_HOST_HEADER} ${EDGE_CONTROL_SNI}
${EDGE_CONTROL_CA_FILE} ${EDGE_RATE_LIMIT} ${EDGE_RATE_LIMIT_UHD} ${EDGE_RATE_AFTER} ${EDGE_HSTS}
${EDGE_DOMAIN} ${EDGE_ACME_DIRECTORY} ${EDGE_ACME_CONTACT_LINE} ${EDGE_ACME_CA_LINE}
${EDGE_ACME_STATE_DIR}'

render() { # render TEMPLATE [TARGET]: TARGET defaults to TEMPLATE's name
    envsubst "$VARS" < "$SRC/templates/$1.template" > "$OUT/${2:-$1}"
}

append() { # append TEMPLATE TARGET
    envsubst "$VARS" < "$SRC/templates/$1.template" >> "$OUT/$2"
}

mkdir -p "$OUT"
render http.conf

# Keys: the key file (preloaded by the master at load and reload), or the control
# channel's shared dictionary on a standalone edge.
if [ -n "$EDGE_CONTROL_URL" ]; then
    append auth-control.conf http.conf
    render server-control.conf server.conf
else
    {
        echo
        echo "# The master reads the key file at configuration load and on reload; workers"
        echo "# never open it, so it can be readable by root only. Rotation: replace it, then reload."
        echo "js_preload_object edge_keys from $EDGE_KEYS_FILE;"
    } >> "$OUT/http.conf"
    append auth-internal.conf http.conf
    render server.conf
fi

# The secrets json, preloaded by the master only (root, mode 600, tmpfs).
if [ -n "$CONTROL_TOKEN$INTERNAL_TOKEN$CDN_SECRET" ]; then
    umask 077
    printf '{"control_token": "%s", "internal_token": "%s", "cdn_secret": "%s"}\n' \
        "$CONTROL_TOKEN" "$INTERNAL_TOKEN" "$CDN_SECRET" > "$OUT/secrets.json"
    umask 022
    printf '\njs_preload_object edge_secrets from %s/secrets.json;\n' "$OUT" >> "$OUT/http.conf"
fi
if [ -n "$EDGE_ORIGIN_CREDENTIALS_FILE" ]; then
    printf '\n# The origin'"'"'s read-only key, from a file (ADR-0022).\njs_preload_object edge_origin from %s;\n' \
        "$EDGE_ORIGIN_CREDENTIALS_FILE" >> "$OUT/http.conf"
fi

# Listeners: plain HTTP behind Traefik, or this edge's own TLS.
: > "$OUT/main.conf"
case $EDGE_TLS in
    off)
        printf 'listen 8080 default_server;\nserver_name _;\n' > "$OUT/listen.conf"
        ;;
    acme)
        echo "load_module modules/ngx_http_acme_module.so;" > "$OUT/main.conf"
        append acme.conf http.conf
        render listen-tls.conf listen.conf
        {
            echo "acme_certificate edge $EDGE_DOMAIN;"
            # shellcheck disable=SC2016 # nginx variables, written literally
            echo 'ssl_certificate $acme_certificate;'
            # shellcheck disable=SC2016
            echo 'ssl_certificate_key $acme_certificate_key;'
            echo "ssl_certificate_cache max=2;"
        } >> "$OUT/listen.conf"
        ;;
    file)
        render listen-tls.conf listen.conf
        printf 'ssl_certificate %s;\nssl_certificate_key %s;\n' "$EDGE_TLS_CERT" "$EDGE_TLS_KEY" \
            >> "$OUT/listen.conf"
        ;;
esac

if [ -n "$EDGE_LIVE_ROOT" ]; then
    append live-http.conf http.conf
    render live.conf
else
    echo "# Live TV is not configured on this edge (EDGE_LIVE_ROOT, EDGE_LIVE_RELAY)." > "$OUT/live.conf"
fi

if [ "$EDGE_CDN" = on ]; then
    render "cdn-$EDGE_MODE.conf" cdn.conf
else
    echo "# CDN pulls are not configured on this edge (EDGE_CDN)." > "$OUT/cdn.conf"
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
if [ "$EDGE_TLS" != off ]; then
    render redirect.conf
    printf 'include %s/redirect.conf;\n' "$OUT" >> "$OUT/mode.conf"
fi

note "$keys"
note "rendered $OUT for EDGE_MODE=$EDGE_MODE, edge $EDGE_ID, TLS $EDGE_TLS${EDGE_LIVE_ROOT:+, live TV on}${EDGE_CONTROL_URL:+, control channel on}"
