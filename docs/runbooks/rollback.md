# Runbook: roll back an update

How to undo an update that went wrong: the code only, or the code and the database.

```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'   # plus your overlays
```

## Before every update
1. **Back up now:** admin → **Backups** → **Back up now**, or `dc exec web python manage.py backup_now --wait`. Wait for it to succeed.
2. **Note the running commit:** `git -C /opt/iptv log -1 --format='%h %s'`. It's also on **Settings → System**.
3. **Read the release notes** (README "Recent updates"): note migrations that change or drop data, and new settings.
4. **Update:** `sudo /opt/iptv/scripts/install.sh --update`.
   - Before pulling, it creates a named **restore point** in the database's WAL, `pre-update-<UTC time>`, and prints its name. It's also in `/var/log/smart-iptv-install.log`.
   - Restoring to it undoes everything the update's migrations did, and everything written after them.

**The first update to the version with backups (M14b)** has no backup yet to roll back to: backups start with that update. Take a logical dump by hand before it:
```bash
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /root/pre-m14b.dump
chmod 600 /root/pre-m14b.dump
```
To restore it, start from an empty database and run `pg_restore --clean --if-exists` as the owner; it's a one-off, so ask for help if you get there.

## Decide what to roll back
| Situation | Roll back |
|---|---|
| The new version misbehaves, and its migrations only added tables or columns | **the code only** (below); old code ignores what it doesn't know |
| A migration changed or deleted data, or the old code fails against the new schema | **the code and the database**, to the restore point |
| Data was damaged after the update, by an admin or a job | the database only, to a time just before ([backups.md](backups.md#restore-the-database-in-place-to-now-a-time-or-a-restore-point)) |

To see which migrations the update brought:
```bash
git -C /opt/iptv diff --stat <old commit> HEAD -- 'backend/apps/*/migrations/'
```

**Zero-downtime updates (M15)** don't change this: a rollback is an update to an older commit. Rolling back the code alone replaces web replica by replica like any update (`scripts/rollout.sh`, [deploy.md](deploy.md#zero-downtime-updates-m15-adr-0022)). Rolling back the database stops the site while it restores.

## Roll back the code only
```bash
cd /opt/iptv
git checkout --detach <old commit>
sed -i "s/^APP_COMMIT=.*/APP_COMMIT=$(git rev-parse --short HEAD)/" .env
dc up -d --build --wait --wait-timeout 900 --remove-orphans
```
- `up` runs `migrate`, which does nothing: the database is already ahead.
- Check the [deploy runbook's Verify list](deploy.md#verify).
- When the fix is released, return to the branch with `git checkout main`, then `install.sh --update`.

## Roll back the code and the database
Writes made since the update are lost: new customers, payments, watch progress. Payments the providers took are still in their dashboards and can be reconciled by hand.
1. **Stop the writers and the database:**
   ```bash
   dc stop web worker beat watcher transcoder live backup postgres
   ```
2. **Restore to the restore point:**
   ```bash
   dc --profile restore run --rm restore --target name:pre-update-20261011T120000Z --yes
   ```
3. **Check out the old code**, as above (`git checkout --detach`, `APP_COMMIT`).
4. **Start everything:** `dc up -d --build --wait --wait-timeout 900 --remove-orphans`.
   - PostgreSQL replays WAL up to the restore point and opens on a new timeline.
   - `migrate` finds nothing to apply.
5. **Rebuild search:** `dc exec web python manage.py search_reindex`.
6. **Take a full backup:** `dc exec web python manage.py backup_now --full --wait`.
7. **With the read replica on:** [re-seed it](database-replica.md#re-seed-the-replica), because it can't follow the new timeline.

If the restore point is missing (the database was down when the update ran), use the time the update started as `--target 'YYYY-MM-DD HH:MM:SS+00'`. The install log's lines carry UTC times.

## Migrating backwards instead
`manage.py migrate <app> <migration>` as the owner (`dc run --rm migrate python manage.py migrate <app> <migration>`) reverses specific migrations without losing other writes. Use it only when the migrations are reversible and you've read them. The restore above is the safer default.
