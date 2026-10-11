#!/bin/sh
# The read-only PostgreSQL role postgres-exporter signs in as (ADR-0018): `iptv_monitor`,
# a member of pg_monitor (statistics views, database sizes; no table data, no writes).
# Run by the postgres-monitor-init service once per start; idempotent. The passwords
# come from Docker secrets and reach psql through its environment, never its arguments.
set -eu

PGPASSWORD=$(cat /run/secrets/postgres_password)
MONITOR_PASSWORD=$(cat /run/secrets/postgres_monitor_password)
export PGPASSWORD MONITOR_PASSWORD
[ -n "$MONITOR_PASSWORD" ] || {
    echo "monitor-role: POSTGRES_MONITOR_PASSWORD is empty; run scripts/secrets.sh" >&2
    exit 1
}

psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 <<'SQL'
\getenv monitor_password MONITOR_PASSWORD
SELECT 'CREATE ROLE iptv_monitor LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'iptv_monitor') \gexec
ALTER ROLE iptv_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    CONNECTION LIMIT 5 PASSWORD :'monitor_password';
GRANT pg_monitor TO iptv_monitor;
SQL
echo "monitor-role: iptv_monitor is ready"
