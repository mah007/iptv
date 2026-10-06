#!/bin/sh
# Alertmanager entrypoint (ADR-0018): renders /run/alertmanager/alertmanager.yml from
# the environment and the Docker secrets, then replaces itself with Alertmanager,
# which has no environment expansion of its own.
#
# Every alert goes to the `ops` receiver: email and Telegram (SPEC §14). A channel is
# configured only when its settings are present; secrets stay in files that
# Alertmanager reads itself (auth_password_file, bot_token_file, chat_id_file,
# url_file) and never appear in the rendered configuration or the logs.
#
#   ALERT_EMAIL_TO            recipients, comma-separated (email off when empty)
#   ALERT_SMTP_SMARTHOST      host:port of the SMTP server (Django's EMAIL_HOST:EMAIL_PORT)
#   ALERT_SMTP_FROM           sender (Django's DEFAULT_FROM_EMAIL)
#   ALERT_SMTP_USERNAME       SMTP user, if the server needs one (EMAIL_HOST_USER)
#   ALERT_SMTP_REQUIRE_TLS    true/false (EMAIL_USE_TLS); port 465 means implicit TLS
#   MONITORING_PUBLIC_URL     where browsers reach the monitoring host, for links
#   /run/secrets/alert_smtp_password        SMTP password (optional)
#   /run/secrets/alert_telegram_bot_token   Telegram bot token   } Telegram is on when
#   /run/secrets/alert_telegram_chat_id     Telegram chat id     } both are present
#   /run/secrets/alert_watchdog_url         a dead man's switch URL for the Watchdog alert
set -eu

OUT=/run/alertmanager/alertmanager.yml
SECRETS=${ALERT_SECRETS_DIR:-/run/secrets}

die() {
    echo "alertmanager-entrypoint: error: $*" >&2
    exit 1
}

note() {
    echo "alertmanager-entrypoint: $*" >&2
}

matches() {
    case $1 in
        *"
"*) return 1 ;;
    esac
    printf '%s\n' "$1" | grep -Eq "^($2)\$"
}

# A secret file is usable when it exists and holds something other than whitespace.
has_secret() {
    [ -f "$SECRETS/$1" ] && [ -n "$(tr -d ' \t\r\n' < "$SECRETS/$1")" ]
}

# YAML double-quoted scalar.
quote() {
    printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
}

email_to=${ALERT_EMAIL_TO:-}
smarthost=${ALERT_SMTP_SMARTHOST:-}
from=${ALERT_SMTP_FROM:-}
username=${ALERT_SMTP_USERNAME:-}
require_tls=$(printf '%s' "${ALERT_SMTP_REQUIRE_TLS:-true}" | tr 'A-Z' 'a-z')
public_url=${MONITORING_PUBLIC_URL:-http://localhost:9093}

matches "$public_url" 'https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?' ||
    die "MONITORING_PUBLIC_URL must be http(s)://host[:port] (got '$public_url')"
case $require_tls in
    1 | true | yes | on) require_tls=true ;;
    0 | false | no | off) require_tls=false ;;
    *) die "ALERT_SMTP_REQUIRE_TLS must be true or false (got '$require_tls')" ;;
esac

EMAIL=0
if [ -n "$email_to" ]; then
    matches "$email_to" '[^@ ,]+@[^@ ,]+(, *[^@ ,]+@[^@ ,]+)*' ||
        die "ALERT_EMAIL_TO must be one or more addresses separated by commas"
    case $smarthost in
        "" | :*) note "ALERT_EMAIL_TO is set but no SMTP server is (EMAIL_HOST): email alerts are off" ;;
        *)
            matches "$smarthost" '[A-Za-z0-9.-]+:[0-9]{1,5}' ||
                die "ALERT_SMTP_SMARTHOST must be host:port (got '$smarthost')"
            [ -n "$from" ] || die "ALERT_SMTP_FROM (DEFAULT_FROM_EMAIL) is required for email alerts"
            EMAIL=1
            ;;
    esac
else
    note "ALERT_EMAIL_TO is empty: email alerts are off"
fi

TELEGRAM=0
if has_secret alert_telegram_bot_token && has_secret alert_telegram_chat_id; then
    matches "$(tr -d ' \t\r\n' < "$SECRETS/alert_telegram_chat_id")" '-?[0-9]{1,20}' ||
        die "the Telegram chat id must be a number (ALERT_TELEGRAM_CHAT_ID)"
    TELEGRAM=1
else
    note "Telegram alerts are off: ALERT_TELEGRAM_BOT_TOKEN and ALERT_TELEGRAM_CHAT_ID are not both set"
fi

[ "$EMAIL" = 1 ] || [ "$TELEGRAM" = 1 ] ||
    note "WARNING: no notification channel is configured; alerts are visible only in the UIs"

mkdir -p "$(dirname "$OUT")"
{
    cat <<EOF
# Rendered by monitoring/alertmanager/entrypoint.sh; edit that file, not this one.
global:
  resolve_timeout: 5m

templates:
  - /etc/alertmanager/templates/*.tmpl

route:
  receiver: ops
  group_by: [alertname, severity]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # Always firing: pings the optional dead man's switch, never a person.
    - matchers: ['alertname="Watchdog"']
      receiver: watchdog
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 5m
    - matchers: ['alertname="FireDrill"']
      receiver: ops
      group_wait: 10s
      group_interval: 1m
      repeat_interval: 1h
    - matchers: ['severity="warning"']
      receiver: ops
      repeat_interval: 12h

inhibit_rules:
  - source_matchers: ['alertname="DiskSpaceCritical"']
    target_matchers: ['alertname="DiskSpaceLow"']
    equal: [instance, mountpoint]
  - source_matchers: ['alertname="MediaEdgeDown"']
    target_matchers: ['alertname="EdgeMedia5xxHigh"']
  - source_matchers: ['alertname="PostgresDown"']
    target_matchers: ['alertname="PostgresReplicationLag"']

receivers:
  - name: ops
EOF
    if [ "$EMAIL" = 1 ]; then
        echo "    email_configs:"
        echo "      - to: $(quote "$email_to")"
        echo "        from: $(quote "$from")"
        echo "        smarthost: $(quote "$smarthost")"
        echo "        require_tls: $require_tls"
        if [ -n "$username" ]; then
            echo "        auth_username: $(quote "$username")"
            if has_secret alert_smtp_password; then
                echo "        auth_password_file: $SECRETS/alert_smtp_password"
            fi
        fi
        echo "        send_resolved: true"
        echo "        headers:"
        echo "          Subject: '{{ template \"iptv.subject\" . }}'"
        echo "        text: '{{ template \"iptv.text\" . }}'"
    fi
    if [ "$TELEGRAM" = 1 ]; then
        echo "    telegram_configs:"
        echo "      - bot_token_file: $SECRETS/alert_telegram_bot_token"
        echo "        chat_id_file: $SECRETS/alert_telegram_chat_id"
        echo "        send_resolved: true"
        echo "        parse_mode: HTML"
        echo "        message: '{{ template \"iptv.telegram\" . }}'"
    fi
    echo "  - name: watchdog"
    if has_secret alert_watchdog_url; then
        echo "    webhook_configs:"
        echo "      - url_file: $SECRETS/alert_watchdog_url"
        echo "        send_resolved: false"
    fi
} > "$OUT"

note "email $([ "$EMAIL" = 1 ] && echo on || echo off), telegram $([ "$TELEGRAM" = 1 ] && echo on || echo off), watchdog $(has_secret alert_watchdog_url && echo on || echo off)"

if [ "${ALERTMANAGER_RENDER_ONLY:-}" = 1 ]; then
    cat "$OUT"
    exit 0
fi

exec /bin/alertmanager \
    --config.file="$OUT" \
    --storage.path=/alertmanager \
    --web.external-url="${public_url%/}/alertmanager/" \
    --web.route-prefix=/alertmanager \
    --cluster.listen-address= \
    "$@"
