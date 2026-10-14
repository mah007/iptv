# Runbook: the PostgreSQL read replica

The optional hot standby of M15 (ADR-0022): reports read from it, and it is a copy of the database to promote if the primary's data is lost. It runs on the same host as the primary (`docker/compose.replica.yml`), so it protects against a damaged data volume or a broken primary, not against losing the server: that is what backups are for ([backups.md](backups.md)).

```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml -f docker/compose.replica.yml'   # plus your other overlays
```
`docker compose ... ps` in the installer's summary prints the full list.

## Turn it on or off
- **On:** `sudo /opt/iptv/scripts/install.sh --update --replica`. This sets `REPLICA=1` in `.env`, and the update starts the replica before it rolls web.
  - The first start copies the primary with `pg_basebackup`. That takes about a minute per few GB, and the site keeps serving meanwhile.
  - It needs about 1 GB of memory (`REPLICA_MEM_LIMIT`) and a second copy of the database on disk.
- **Off:** `sudo /opt/iptv/scripts/install.sh --update --no-replica`.
  - The replica's containers stop, and its replication slot is dropped, so the primary stops keeping WAL for it.
  - Its volume (`smart-iptv_postgres-replica-data`) stays until you delete it.
- **In development:** `make up REPLICA=1`, or `REPLICA=1` in `.env`.

What reads it: dashboard charts, billing figures, the storage report and the minute gauges (`@replica.reporting` in the code). Everything else (sign-in, playback, writes, migrations, backups, the restore drill) stays on the primary.

## Check it
```bash
dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT application_name, state, replay_lag FROM pg_stat_replication"'
dc exec -T postgres-replica sh -c 'psql -U "$POSTGRES_USER" -d postgres -h 127.0.0.1 -Atc "SELECT pg_is_in_recovery(), now() - pg_last_xact_replay_timestamp()"'
```
- The first command should show one row with `state` = `streaming`; the second, `t` and a small interval.
- **Settings → Database:**
  - `database.replica_reads` switches reporting reads to the replica (on by default when a replica exists).
  - `database.replica_max_lag_s` (30) is the lag above which reports read from the primary again.

  A replica that is down or behind only makes reports read from the primary: nothing breaks.

## When it falls behind (PostgresReplicationLag)
The alert fires when the lag has been over 30 s for 5 minutes. A primary always reports 0.
1. Is it replaying?
   ```bash
   dc exec -T postgres-replica sh -c 'psql -U "$POSTGRES_USER" -d postgres -h 127.0.0.1 -Atc "SELECT pg_get_wal_replay_pause_state()"'
   ```
   If the answer is `paused` (someone paused it), resume replay with `SELECT pg_wal_replay_resume()`.
2. Is it streaming? Run `dc logs --tail 50 postgres-replica`.
   - Authentication errors: compare `POSTGRES_REPLICATION_PASSWORD` in `.env` with the role. `dc run --rm db-roles` sets the role again.
   - "requested WAL segment has already been removed": it was gone longer than `max_slot_wal_keep_size` (8 GB) allows. **Re-seed it** (below).
3. A long report on the replica can hold replay back for up to 30 s (`max_standby_streaming_delay`). That is normal under load, and it does not fire the alert.

## Failover: the primary's data is lost
Use this when the primary can't start (a damaged volume, a failed disk under it) and the replica is healthy. If the whole server is lost, restore from backups instead ([backups.md](backups.md)).

This was rehearsed on a throwaway Compose project with the same scripts: promotion took under a second, and no committed row was lost.

1. **Make sure the primary is really gone** (`dc ps postgres`, `dc logs --tail 100 postgres`). Note how far the replica got:
   ```bash
   dc exec -T postgres-replica sh -c 'psql -U "$POSTGRES_USER" -d postgres -h 127.0.0.1 -Atc "SELECT pg_last_wal_replay_lsn(), pg_last_xact_replay_timestamp()"'
   ```
2. **Stop the primary** so it can't come back by itself: `dc stop postgres`.
3. **Promote the replica:**
   ```bash
   dc exec -T postgres-replica sh -c 'psql -U "$POSTGRES_USER" -d postgres -h 127.0.0.1 -Atc "SELECT pg_promote(wait => true)"'
   ```
4. **Point the app at it.**
   - Add `POSTGRES_HOST=postgres-replica` to `.env`.
   - Set **Settings → Database → `database.replica_reads`** off (the "replica" is now the primary).
   - Then recreate the services that use the database: `dc up -d --no-deps web worker beat watcher live transcoder`.
   - Check the [deploy runbook's Verify list](deploy.md#verify).
5. **What doesn't work in this state:**
   - **Backups.** pgBackRest archives from the primary's container only. Fail back within the day.
   - **The restore drill and `install.sh --update`.** Don't update until you have failed back.

## Fail back: rebuild the primary from the promoted replica
This takes a few minutes of downtime: the app stops while the data is copied, so no write is lost.
1. **Stop the writers:** `dc stop web worker beat watcher live transcoder`.
2. **Move the old primary's data aside.** It isn't deleted, so it can be examined or recovered later:
   ```bash
   dc run --rm --no-deps --entrypoint sh postgres -c 'mv /var/lib/postgresql/18 /var/lib/postgresql/18.lost-$(date +%Y%m%d)'
   ```
3. **Copy the promoted replica into the primary's volume:**
   ```bash
   dc run --rm --no-deps --entrypoint sh postgres -c 'set -e
     mkdir -p "$PGDATA"; chown -R postgres:postgres /var/lib/postgresql; chmod 700 "$PGDATA"
     PGPASSWORD="$POSTGRES_REPLICATION_PASSWORD" su-exec postgres pg_basebackup --host postgres-replica \
       --username iptv_replicator --no-password --pgdata "$PGDATA" --wal-method stream --checkpoint fast'
   ```
   Pass `-e POSTGRES_REPLICATION_PASSWORD` if your `dc` doesn't load `.env` into that service.
4. **Switch back:**
   - Remove `POSTGRES_HOST` from `.env`.
   - Run `dc stop postgres-replica`, then `dc up -d --wait postgres`.
   - Run `dc up -d --no-deps web worker beat watcher live transcoder`, then set `database.replica_reads` on again.
5. **Re-seed the replica** (below).
6. **Back up now:** admin → **Backups** → **Back up now**. WAL written during the failover wasn't archived, so a point-in-time restore can't cross that window. A fresh full backup starts a clean chain.

## Re-seed the replica
Do this after a fail back, after a point-in-time restore of the primary (a new timeline the replica can't follow), or after the replica fell too far behind.
```bash
dc stop postgres-replica
dc run --rm --no-deps --entrypoint sh postgres-replica -c 'mv /var/lib/postgresql/18 /var/lib/postgresql/18.old-$(date +%Y%m%d)'
dc up -d --wait postgres-replica      # replica-prepare recreates the slot; pg_basebackup copies the primary again
```
Then [check it](#check-it). When it streams, delete the old folder:
```bash
dc run --rm --no-deps --entrypoint sh postgres-replica -c 'rm -rf /var/lib/postgresql/18.old-*'
```

## Passwords and roles
- **`iptv_replicator`** is created by `db-roles` when `POSTGRES_REPLICATION_PASSWORD` is set (`scripts/secrets.sh` adds it to `.env`). It has REPLICATION and nothing else.
- **Rotation:** change the value in `.env`, run `dc run --rm db-roles` (it sets the new password), then [re-seed the replica](#re-seed-the-replica). The replica keeps the password it was copied with in its `primary_conninfo`, so it would fail at its next reconnect.
- **The app reads the replica as `iptv_app`,** the same role as on the primary (physical replication copies roles). A standby refuses writes anyway.
