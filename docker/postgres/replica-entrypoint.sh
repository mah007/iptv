#!/bin/sh
# The read replica (M15, ADR-0022; docker/compose.replica.yml): a hot standby of the
# primary, streaming over the backend network.
#
# First start (an empty volume): pg_basebackup from the primary through the slot
# replica-prepare made, as iptv_replicator; `-R` writes standby.signal and the
# connection settings. Afterwards it just starts: the official entrypoint skips its own
# initialisation for an existing data folder and runs PostgreSQL as postgres.
# After a point-in-time restore of the primary (a new timeline), re-seed it:
# docs/runbooks/database-replica.md moves this volume's data aside first.
set -eu

: "${PGDATA:?}" "${PRIMARY_HOST:?}" "${POSTGRES_REPLICATION_PASSWORD:?set POSTGRES_REPLICATION_PASSWORD in .env (scripts/secrets.sh adds it)}"
SLOT=${REPLICA_SLOT:-replica1}

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    mkdir -p "$PGDATA"
    chown -R postgres:postgres "$(dirname "$PGDATA")"
    chmod 700 "$PGDATA"
    for _ in $(seq 120); do
        pg_isready --quiet -h "$PRIMARY_HOST" -p 5432 && break
        sleep 2
    done
    echo "replica: copying the primary (pg_basebackup through slot $SLOT)"
    PGPASSWORD=$POSTGRES_REPLICATION_PASSWORD su-exec postgres pg_basebackup \
        --host "$PRIMARY_HOST" --port 5432 --username iptv_replicator --no-password \
        --pgdata "$PGDATA" --wal-method stream --slot "$SLOT" --write-recovery-conf \
        --checkpoint fast
    echo "replica: copied; starting as a hot standby"
fi

exec docker-entrypoint.sh postgres \
    -c hot_standby=on \
    -c "primary_slot_name=$SLOT" \
    -c hot_standby_feedback=on \
    -c max_standby_streaming_delay=30s
