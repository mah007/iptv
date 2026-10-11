# Runbook: backups, restores and the restore drill

What is backed up, how to check it, and how to restore: one table, a single file, the database to a point in time, or the whole server on new hardware (ADR-0020).

## What runs
- **The `backup` service** (`apps/ops/agent.py`, image `smart-iptv/postgres-backup`) asks Django for work every 30 seconds. It never decides anything itself.
  - The schedule, retention and the offsite bucket are admin settings: **Settings → Backups**.
  - The admin's **Backups** page shows the last runs and the last drill, and has **Back up now**, **Run the restore drill** and **Test the destinations**.
- **PostgreSQL** archives its WAL continuously into the backup repository through `pgbackrest-iptv-archive`. Archiving is on once the backup service has set the repository up, and is switched off by turning backups off in the settings.

| What | Tool | Where | When | Kept |
|---|---|---|---|---|
| Database base backups | pgBackRest 2.59 | `BACKUP_DIR/pgbackrest` | full on `backups.full_weekday`, differential the other nights, at `backups.schedule_time` | `backups.db_full_retention` fulls (2), each with its differentials and WAL |
| WAL (point-in-time recovery) | pgBackRest | `BACKUP_DIR/pgbackrest` | continuously; at least every `PG_ARCHIVE_TIMEOUT` (300 s) | as long as the fulls it belongs to |
| `.env`, `secrets/`, artwork (`media-data/images`) | restic 0.19 | `BACKUP_DIR/restic` | nightly | `backups.files_keep_daily/weekly/monthly` (7/4/6) |
| A logical dump (`pg_dump -Fc`) | restic | `BACKUP_DIR/restic` | nightly | the same |
| Media libraries (opt-in, `backups.media_enabled`) | restic | `BACKUP_DIR/restic` | nightly | the same |
| Everything above, offsite (opt-in) | restic | an S3-compatible bucket | the pgBackRest repository every `backups.offsite_interval_minutes` (60); the night's snapshots after the nightly backup | the same policy |

- **Not backed up:**
  - transcoded renditions: re-created from the libraries, and uploaded subtitles live in the database;
  - Meilisearch: rebuilt from the database ([search-index.md](search-index.md));
  - redis-state: sessions, slots and entitlements rebuild themselves, and app clients sign in again;
  - the live catch-up archive.
- **Encryption:** every backup is encrypted with the **backup passphrase**, `secrets/backup_passphrase` (pgBackRest's AES-256 repository cipher and restic's password).
  - `scripts/secrets.sh` creates it once, and the installer shows it once and keeps a copy in `/root/smart-iptv-backup-passphrase.txt`.
  - **Keep a copy away from the server.** Without it no backup can be read, and it cannot be changed for existing repositories.
- **Recovery points:**
  - on the server: up to 5 minutes of data can be lost (`PG_ARCHIVE_TIMEOUT`);
  - offsite: up to `backups.offsite_interval_minutes`.

## Check that backups work
- The admin's **Backups** page: last database backup, last restore drill, the backup service's state, and free space.
- Alerts: BackupFailed, RestoreDrillFailed, BackupStale, RestoreDrillStale, BackupAgentDown and WalArchiveFailing ([alerts.md](alerts.md)). They go by email and Telegram through the monitoring overlay, or from Django itself when the overlay is off.
- On the server:
  ```bash
  cd /opt/iptv
  alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'   # add the overlays you run
  dc ps backup                                   # healthy
  dc exec -u postgres backup pgbackrest info     # base backups and the WAL range
  dc exec backup restic snapshots                # the local snapshots
  dc exec web python manage.py backup_now --wait # back up now and wait for the result
  dc exec web python manage.py restore_drill --wait
  ```

## The restore drill
It runs weekly (`backups.drill_weekday`, `backups.drill_time`), right after the first backup of a new server, and whenever an admin asks.
1. The latest base backup is restored into a throwaway data folder (`backup-drill` volume) and all archived WAL is replayed.
2. The throwaway server starts with no network listener and no archiving, so it can't touch the repository or be reached.
3. Its migrations and the row counts of the main tables are read and compared with the live database. A drill fails when migrations or tables are missing, a table is empty that isn't empty live, the data is more than a day old (archiving stopped), or `pgbackrest verify` finds errors.
4. The throwaway copy is deleted.

The Backups page shows the restore time (the recovery time objective for this size of database) and each check. In development: `make restore-test`.

### When the drill fails
1. Read the error and the checks on the Backups page; `dc logs --tail 200 backup`.
2. Take a full backup (`dc exec web python manage.py backup_now --full --wait`) and drill again.
3. "Data more than a day old": WAL archiving stopped. Look for WalArchiveFailing and disk space (`df -h`), and check that `BACKUP_DIR` is mounted.
4. "Migrations missing": a migration ran after the last base backup, and its WAL never reached the repository. Same as 3.
5. `verify` errors: the repository is damaged (disk errors?). Take a new full backup now, and restore from the offsite copy if you need an older point.

## Restore the database in place (to now, a time, or a restore point)
Use this after a destructive mistake, such as a bad migration or a mass delete, while the server itself is fine. Customers lose access during the restore: the database is stopped.

1. **Choose the target:**
   - `latest`: everything archived;
   - a time just before the mistake, in UTC, e.g. `'2026-10-11 12:00:00+00'`;
   - `name:pre-update-20261011T120000Z`: the restore point `install.sh --update` creates before every update (its name is in the installer's output and log).
2. **Stop everything that writes, then the database:**
   ```bash
   dc stop web worker beat watcher transcoder live backup postgres
   ```
3. **Restore** (the `restore` service is on demand only):
   ```bash
   dc --profile restore run --rm restore --target '2026-10-11 12:00:00+00' --yes
   ```
4. **Start the database.** It replays WAL up to the target, starts a new timeline and accepts connections:
   ```bash
   dc up -d --wait postgres
   dc logs postgres | grep -E 'recovery stopping|archive recovery complete'
   ```
5. **Start the rest:** `dc up -d --wait`. Then search: `dc exec web python manage.py search_reindex`. Sessions in redis-state that refer to rows created after the target simply expire.
6. **Take a new full backup** at once (`backup_now --full --wait`): the old timeline's backups stay usable, but the new one starts here.

Tested on the development stack and a throwaway project: a point-in-time restore took 27 seconds for a 17 MB database.

## Restore single files (configuration, artwork)
```bash
dc exec backup restic snapshots --tag files
dc exec backup restic restore latest --tag files --include /sources/config/.env --target /tmp/restore
dc exec backup cat /tmp/restore/sources/config/.env        # compare, then copy what you need
```
`/sources/config` holds `.env` and `secrets/`, and `/sources/media-data/images` the artwork. `restic dump --tag files latest /sources/config/.env` prints one file. `/tmp` in the container is temporary: copy out with `docker compose cp backup:/tmp/restore ./restore`.

## Disaster recovery: the server is gone
You need:
- the backup passphrase (your offline copy);
- the backups: a copy of `BACKUP_DIR`, or the S3 bucket's address, name, prefix and keys.

1. **New server:** install with the same domain (`install.sh`), and let it finish. It makes new secrets, which step 3 replaces.
2. **Stop the stack:** `cd /opt/iptv && dc down`. Data volumes stay; they hold nothing yet.
3. **Get the old `.env` and `secrets/` back:**
   ```bash
   echo -n 'the-backup-passphrase' > /root/passphrase && chmod 600 /root/passphrase
   docker run --rm -v /root/passphrase:/pass:ro -v /tmp/restore:/restore \
     -e RESTIC_PASSWORD_FILE=/pass -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
     restic/restic:0.19.1 -r s3:https://ENDPOINT/BUCKET/PREFIX restore latest --tag files --target /restore
   ```
   With a copy of `BACKUP_DIR` instead, use `-v /path/to/copy/restic:/repo -r /repo`. Copy `/tmp/restore/sources/config/.env` and `secrets/` into `/opt/iptv` (mode 600 for `.env`; the passphrase file comes with them).
4. **Put the pgBackRest repository in `BACKUP_DIR`:**
   - offsite: `restic restore latest --tag pgbackrest --target /tmp/repo`, then move `/tmp/repo/backups/pgbackrest` to `BACKUP_DIR/pgbackrest`;
   - from a copy: copy it there.
   
   Then `chown -R 70:70 BACKUP_DIR/pgbackrest` (the postgres user in the containers).
5. **Restore the database into the new, empty volume.** Create the volume with one start and stop, so it exists with the right owner, then restore:
   ```bash
   dc up -d --wait postgres && dc stop postgres
   dc --profile restore run --rm restore --target latest --yes
   dc up -d --wait postgres
   ```
6. **Start everything:** `dc up -d --wait`; `dc exec web python manage.py search_reindex`. Then the artwork: `restic restore latest --tag files --include /sources/media-data/images` into the `media-data` volume (or let the metadata jobs fetch it again). Transcoded files are re-created from the libraries.
7. **Check:** sign in to the admin, play a title, and take a full backup (**Back up now**).

## Moving the backups
`BACKUP_DIR` (in `.env`; the installer asks) can move to another disk:
1. Turn backups off in Settings → Backups.
2. `dc stop backup postgres` and copy the folder, ownership included (`cp -a`).
3. Set `BACKUP_DIR` in `.env`, then `dc up -d --wait`, and turn backups on again.

## Settings (admin → Settings → Backups)
Schedule time and time zone, the full-backup day, retention, media on or off, the drill day and time, and the bucket: address, region, name, folder, path-style, keys (stored encrypted), and the offsite interval.
- What is set at installation is shown read-only: the folder and whether the passphrase is in place.
- Changing the bucket starts a new offsite repository at the next offsite copy.
- **Test the destinations** on the Backups page checks each repository right away.

**Bucket advice:** credentials that can only read and write that bucket. Turn on versioning or object lock if your provider has it, so a compromised server can't delete the offsite history (restic's prune needs delete; give the lock a retention longer than your monthly snapshots).
