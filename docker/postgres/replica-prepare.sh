#!/bin/sh
# The read replica's needs on the primary (M15, ADR-0022; docker/compose.replica.yml),
# on every start, idempotent. Over the primary's local socket (trusted there, like
# db-roles), as the postgres user that owns PGDATA:
#   - a physical replication slot, so the primary keeps the WAL the replica still needs
#     (bounded by max_slot_wal_keep_size: a replica gone for long can't fill the disk);
#   - one pg_hba.conf line letting iptv_replicator (db-roles) stream from the backend
#     network with its password;
#   - a reload.
set -eu

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${PGDATA:?}"
SLOT=${REPLICA_SLOT:-replica1}
KEEP=${REPLICA_MAX_SLOT_WAL_KEEP:-8GB}
export PGHOST="${PGHOST:-/var/run/postgresql}" PGUSER="$POSTGRES_USER" PGDATABASE="$POSTGRES_DB"

for _ in $(seq 60); do
    pg_isready --quiet && break
    sleep 1
done

line="host replication iptv_replicator samenet scram-sha-256"
if ! grep -qxF "$line" "$PGDATA/pg_hba.conf"; then
    printf '\n# The read replica streams from the backend network (M15, ADR-0022).\n%s\n' "$line" \
        >> "$PGDATA/pg_hba.conf"
fi

psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 -v slot="$SLOT" -v keep="$KEEP" <<'SQL'
SELECT pg_create_physical_replication_slot(:'slot')
WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = :'slot');
ALTER SYSTEM SET max_slot_wal_keep_size = :'keep';
SELECT pg_reload_conf();
SQL
echo "replica-prepare: slot $SLOT and the replication rule are ready"
