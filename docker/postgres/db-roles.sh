#!/bin/sh
# The database roles of G-37 (ADR-0020), created or updated before `migrate` on every
# start; idempotent, so an existing database upgrades in place.
#   iptv_app        what the app services sign in as (rows only; apps/ops/grants.py
#                   grants its privileges after each migrate)
#   iptv_reporting  read-only, for the operator's reports
# It signs in as the owner over PostgreSQL's local socket (shared with the database
# container, trusted there), so it needs no owner password. The role passwords come
# from the environment and reach psql through its environment, never its arguments.
set -eu

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${POSTGRES_APP_PASSWORD:?set POSTGRES_APP_PASSWORD in .env (scripts/secrets.sh adds it)}"
: "${POSTGRES_REPORTING_PASSWORD:?set POSTGRES_REPORTING_PASSWORD in .env (scripts/secrets.sh adds it)}"
export PGHOST="${PGHOST:-/var/run/postgresql}" PGUSER="$POSTGRES_USER" PGDATABASE="$POSTGRES_DB"

for _ in $(seq 60); do
    pg_isready --quiet && break
    sleep 1
done

psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_password POSTGRES_APP_PASSWORD
\getenv reporting_password POSTGRES_REPORTING_PASSWORD
SELECT 'CREATE ROLE iptv_app LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'iptv_app') \gexec
ALTER ROLE iptv_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    INHERIT PASSWORD :'app_password';
SELECT 'CREATE ROLE iptv_reporting LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'iptv_reporting') \gexec
ALTER ROLE iptv_reporting WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 5 PASSWORD :'reporting_password';
-- Reporting reads, never writes, whatever a session asks for.
ALTER ROLE iptv_reporting SET default_transaction_read_only = on;
SQL
echo "db-roles: iptv_app and iptv_reporting are ready"
