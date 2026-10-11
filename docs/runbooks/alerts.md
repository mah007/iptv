# Runbook: alerts

What each alert of the monitoring overlay means and what to do (ADR-0018). Every alert's email and Telegram message links here, to the heading with its name. Rules: `monitoring/prometheus/rules/alerts.yml`.

**Severity.** **Critical**: customers are affected, or will be soon; act now. **Warning**: act today. Delivery (who, which severities) is set in the admin under **Settings → Monitoring**, the SMTP server and the Telegram bot and chat under **Settings → Integrations** (each with a Test); a critical alert repeats every 4 hours while it fires, a warning every 12.

## First steps for any alert
1. **Look before you act.** Open the admin → **Monitoring** → **Dashboards and logs**. The **Platform** dashboard shows every service at once; each other dashboard covers one area. **Explore → Loki** finds logs: `{project="smart-iptv", service="web"} |= "error"`. A request id in any line links to every line of that request (Traefik, Django and the media edge).
2. **On the server**, use one command prefix (production with monitoring):
   ```bash
   cd /opt/iptv
   alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml -f docker/compose.monitoring.yml -f docker/compose.monitoring.prod.yml'
   dc ps                          # every service should be "healthy"
   dc logs --tail 100 <service>   # web, worker, transcoder, nginx-stream, postgres, ...
   ```
3. **Planned maintenance:** silence the alerts first (admin → Monitoring → **Alerts** → New silence) so nobody is paged for it.
4. **Afterwards:** note what happened, the cause and the fix (a short post-incident note), and whether an alert or this runbook should change.

## Streaming and the media edge

### EdgeEgressHigh
**Means:** the media edge has sent more than 80% of its uplink (Settings → Monitoring → *Media edge capacity*) for 10 minutes.
**Users notice:** new streams start slowly, players drop to lower qualities or buffer.
**Check:** the **Edges** dashboard (egress per edge, requests); **Streaming Ops** (streams by plan and rendition). Is it a real audience peak or one client downloading in a loop (look up the session in the admin's **Live sessions**)?
**Do:** stop abusive sessions from the admin; lower the plans' quality ceilings for the peak; move to a bigger uplink or add a CDN/edge (the medium tier, M15). If the setting is wrong for this server, correct it.

### EdgeMedia5xxHigh
**Means:** over 1% of media requests failed with a server error for 5 minutes.
**Users notice:** playback fails or stops ("unavailable").
**Check:** **Edges** → *Refusals by reason* (`unavailable` means stream-auth or storage failed); `dc logs --tail 200 nginx-stream`; is `web` healthy (stream-auth lives there)? Is the media volume full or missing (`df -h`, `ls /srv/media`)?
**Do:** restart what's unhealthy (`dc up -d web` or `dc restart nginx-stream`); free disk space; check the media folder is mounted.

### MediaEdgeDown
**Means:** the edge's `/healthz` has failed for 2 minutes.
**Users notice:** no playback and no artwork at all.
**Check:** `dc ps nginx-stream`, `dc logs --tail 100 nginx-stream` (a bad key file stops it at start).
**Do:** `dc up -d nginx-stream`. If it doesn't stay up, check `secrets/media_token_keys.json` (it must exist and be valid JSON) and the last change to `streaming/nginx/`.

## Data stores

### RedisStateEvictions
**Means:** redis-state, which must never evict, dropped keys.
**Users notice:** streams stopped or refused at random, people signed out, queued jobs lost.
**Check:** the **Platform** dashboard → Valkey memory; `dc exec redis-state valkey-cli info memory` (password in `.env`).
**Do:** this is a configuration fault: redis-state must run without a `maxmemory` eviction policy and with enough memory. Give the host memory, find what grew (key counts by prefix), and restore the setting from `docker/compose.yml`. Active sessions recover by themselves as players reconnect.

### RedisStateMemoryHigh
**Means:** redis-state has used over 80% of its memory ceiling (`REDIS_STATE_MAXMEMORY` in `.env`, 1 GB by default) for 10 minutes. It never evicts: at the ceiling it refuses writes (G-35).
**Users notice:** nothing yet; at the ceiling, sign-ins, playback starts and queued jobs fail.
**Check:** the **Platform** dashboard → Valkey memory; what grew: `dc exec redis-state valkey-cli --scan --count 1000 | cut -d: -f1 | sort | uniq -c | sort -rn | head` (as the default user, password in `.env`); a Celery queue that nobody consumes (`CeleryQueueBacklog`)?
**Do:** fix what grows (start the stopped worker, end a runaway job). If the load is real, raise `REDIS_STATE_MAXMEMORY` in `.env`, keeping it well under the host's free memory, and `sudo scripts/install.sh --update`.

### RedisDown
**Means:** the exporter can't reach redis-state or redis-cache.
**Users notice:** redis-state down: no sign-ins, no playback, no background jobs. redis-cache down: everything slower.
**Check:** `dc ps redis-state redis-cache`, `dc logs --tail 50 redis-state`.
**Do:** `dc up -d redis-state` (it keeps its data on disk, append-only). If the exporter alone fails (NOAUTH), run `scripts/secrets.sh`, then `dc up -d --force-recreate redis-exporter`.

### PostgresDown
**Means:** the exporter can't connect to PostgreSQL.
**Users notice:** the admin, the portal and Xtream sign-ins fail. Running streams continue (the edge checks Redis only) until their tokens expire.
**Check:** `dc ps postgres`, `dc logs --tail 100 postgres` (disk full? out of memory?), `df -h`.
**Do:** free disk if needed, then `dc up -d postgres`. If only the exporter fails: `dc up -d --force-recreate postgres-monitor-init postgres-exporter`.

### PostgresReplicationLag
**Means:** a PostgreSQL replica is over 30 seconds behind. A single server has no replica, so this only fires once one exists (M15).
**Check:** the replica's logs and its network to the primary.
**Do:** reads that need fresh data should use the primary until the replica catches up.

### MeilisearchUnhealthy
**Means:** Meilisearch has failed its health check for 2 minutes.
**Users notice:** search still works, through PostgreSQL, but slower and less tolerant of typos.
**Check:** `dc logs --tail 100 meilisearch`, disk space.
**Do:** `dc up -d meilisearch`, then `dc exec web python manage.py search_reindex`.

## Background work

### CeleryWorkerDown
**Means:** the `worker` (scans, metadata, notifications, schedules) or the `transcoder` hasn't answered for 2 minutes.
**Users notice:** new files aren't processed, emails don't go out, transcodes stop; playback is unaffected.
**Check:** `dc ps worker transcoder`, `dc logs --tail 100 worker`. Out of memory? (ContainerOOMKilled fires too.)
**Do:** `dc up -d worker transcoder`. Jobs queued meanwhile run once it's back.

### CeleryBeatStalled
**Means:** no heartbeat from the scheduler for over 3 minutes: beat schedules one every 30 s and a worker runs it, so either is stuck.
**Users notice:** scheduled jobs stop: the session sweeper, access expiry, subscription renewals, reminders, metrics.
**Check:** `dc ps beat worker`, `dc logs --tail 50 beat`; the **Platform** dashboard → *Queue lengths* (is `default` piling up?).
**Do:** `dc restart beat` (and `worker` if it's the one stuck).

### WatcherStalled
**Means:** the library watcher hasn't beaten for 5 minutes.
**Users notice:** new files appear only after the next scheduled scan instead of within seconds.
**Check:** `dc logs --tail 100 watcher` (a library folder missing or unreadable?).
**Do:** `dc restart watcher`. For network shares, set `LIBRARY_WATCHER_POLLING=true` in `.env`.

### CeleryQueueBacklog
**Means:** over 500 messages have waited in one queue (not transcoding) for 15 minutes.
**Check:** the **Platform** dashboard → *Queue lengths* and *Tasks by state*; `dc logs --tail 200 worker`. A big library import does this for a while, which is expected.
**Do:** if the worker is stuck, restart it. If it's just slow, wait; for a huge import, raise the worker's concurrency.

## Media and library

### TranscodeJobTooOld
**Means:** a transcode job has waited (`queued`) or run (`running`) for over 2 hours.
**Users notice:** that title stays "processing" and can't be played.
**Check:** the admin's **Transcode jobs** page (progress, speed, errors); the **Transcoding** dashboard. On a CPU-only server a long film can legitimately take over 2 hours at 1x.
**Do:** queued for long: is a transcoder running (CeleryWorkerDown)? Running but no progress: cancel and retry it from the admin. Persistent failures: the job's error in the admin.

### ReviewQueueLarge
**Means:** over 50 metadata matches have waited for review for 30 minutes.
**Users notice:** those titles stay out of the catalogue.
**Do:** resolve them in the admin's **Review queue** (keyboard shortcuts help). Many wrong matches from one library usually mean its file names need cleaning up.

## Billing

### PaymentWebhookFailures
**Means:** a payment provider's event (Stripe or Moyasar) couldn't be applied.
**Users notice:** a customer paid but their subscription wasn't extended.
**Check:** the admin → **Payments** (the payment's webhook events and their errors); `{service="web"} |= "webhook"` in Loki.
**Do:** fix the cause (a missing plan, a database error); the provider resends the event and it's applied once. If the customer is waiting, extend their subscription by hand, then check the event doesn't apply twice (it can't: events are stored once).

### PaymentWebhookRejected
**Means:** more than 5 webhooks with a bad signature or token in 30 minutes.
**Check:** was the webhook secret changed at the provider? Compare it with `STRIPE_WEBHOOK_SECRET` / `MOYASAR_WEBHOOK_SECRET` in `.env`.
**Do:** fix the secret (then `dc up -d web worker`). Unknown senders probing the endpoint are refused anyway; CrowdSec (M14) will block them.

## The platform

### WebNotReady
**Means:** Django's readiness check fails: web itself, or a store it needs (PostgreSQL, a Valkey, Meilisearch), is down.
**Users notice:** the admin, the portal, the API and Xtream fail.
**Check:** `dc ps`, then the alerts about the store that's down; `dc logs --tail 100 web`.
**Do:** fix the store, or `dc up -d web`. This alert also arrives through Alertmanager's own fallback channels, since Django can't send it.

### PublicEndpointDown
**Means:** one of the public addresses fails over HTTPS from the server's side for 5 minutes (TLS, routing or the service behind it).
**Check:** `curl -sv https://<host>/` from outside; `dc logs --tail 100 traefik`; DNS (`dig <host>`).
**Do:** fix the service or Traefik (`dc up -d traefik`); for TLS, see CertificateExpiringSoon.

### CertificateExpiringSoon
**Means:** a certificate expires in under 14 days, so Let's Encrypt renewal (due at 30 days) is failing.
**Check:** `dc logs traefik | grep -i acme`: port 80 blocked? DNS no longer pointing here? Rate-limited?
**Do:** fix the cause and restart Traefik (`dc restart traefik`); it retries at once. Keep the `traefik-acme` volume: deleting it forces re-issuing every certificate.

### DiskSpaceLow
**Means:** a filesystem is over 85% full.
**Check:** `df -h`; `docker system df`; the **Hosts & GPU** dashboard → *Free space in 7 days*.
**Do:** remove what isn't needed: old renditions (the admin's **Storage** page cleans orphans), unused Docker images (`docker image prune`), the media you no longer carry. Monitoring itself is capped (Prometheus 10 GB, Loki 30 days).

### DiskSpaceCritical
**Means:** over 95% full: PostgreSQL, redis-state's append-only file and transcodes are about to fail.
**Do:** as DiskSpaceLow, now. Stop the transcoder (`dc stop transcoder`) to gain time if renditions are what grows.

### HostMemoryLow
**Means:** under 10% of the host's memory has been available for 10 minutes.
**Check:** the **Hosts & GPU** dashboard → *Top memory*; `docker stats --no-stream`.
**Do:** lower the transcoder's concurrency (`TRANSCODER_CONCURRENCY`), stop what isn't needed, or add memory. The monitoring overlay itself uses about 1.5–2 GB.

### ContainerOOMKilled
**Means:** a container hit its memory limit and the kernel killed a process in it.
**Check:** which service (the alert says), `dc logs --tail 100 <service>`; repeated kills in the **Hosts & GPU** dashboard.
**Do:** usually a heavy transcode (4K) or a runaway query. Raise that service's limit, or reduce its work.

### MonitoringTargetDown
**Means:** Prometheus can't scrape one exporter or service for 10 minutes; the alerts built on it are blind meanwhile.
**Check:** Prometheus (admin → Monitoring → **Metrics**) → *Status → Targets* shows the error; `dc ps`.
**Do:** `dc up -d <exporter>`. After changing passwords in `.env`, run `scripts/secrets.sh` and recreate the exporters.

### AlertDeliveryFailing
**Means:** alerts failed to go out: through Django (channel `email` or `telegram`) or through Alertmanager (integration `webhook`: Django didn't answer; `email` or `telegram`: the fallback).
**Check:** admin → **Settings → Monitoring** (recipients) and **Settings → Integrations** (SMTP, Telegram bot and chat: press **Test**); `{service="web"} |= "alert delivery failed"` and `dc logs alertmanager` in Loki. A Telegram bot must have been added to the chat, and a group's id starts with `-100`.
**Do:** correct the setting, then **Send a test alert** from the admin's Monitoring page. This alert also goes to Alertmanager's own fallback channels.

## Backups and the restore drill
Procedures: [backups.md](backups.md). Without the monitoring overlay, Django sends these same alerts itself.

### BackupFailed
**Means:** a backup job (database, files or offsite copy) failed.
**Users notice:** nothing; the risk is losing data if the server fails before the next good backup.
**Check:** the admin's **Backups** page: the failed run and its error. `dc logs --tail 100 backup`. Disk space where backups are kept (`df -h /var/backups`). For the offsite copy: the bucket's address, keys and quota (**Test destination** on the Backups page).
**Do:** fix the cause, then **Back up now**. A full disk: lower the retention (Settings → Backups) or move `BACKUP_DIR` to a larger disk ([backups.md](backups.md#moving-the-backups)).

### RestoreDrillFailed
**Means:** the latest backup could not be restored into the throwaway database, or the restored copy failed a check (missing migrations or tables, empty tables, old data, repository errors).
**Users notice:** nothing. Assume the backups might not restore until a drill passes.
**Check:** the Backups page → the drill's checks and error; `dc logs --tail 200 backup`.
**Do:** take a full backup (`dc exec web python manage.py backup_now --full --wait`), then run the drill again (**Run the restore drill**). If it still fails, follow [backups.md](backups.md#when-the-drill-fails).

### BackupStale
**Means:** no database backup has succeeded for 26 hours (or ever, on a server with backups switched on).
**Check:** is the backup service running (`dc ps backup`; BackupAgentDown)? Did last night's run fail (BackupFailed)? Is the schedule right (Settings → Backups)?
**Do:** fix that, then **Back up now** and watch it finish.

### RestoreDrillStale
**Means:** no restore drill has passed for 8 days; the production checklist (SPEC §17) needs one within 7.
**Do:** check that the drill is switched on (Settings → Backups), then **Run the restore drill**.

### BackupAgentDown
**Means:** the backup service has not asked Django for work for over 10 minutes. Nothing is backed up and no drill runs; WAL archiving still works (PostgreSQL does it itself).
**Check:** `dc ps backup`, `dc logs --tail 100 backup` (a missing `secrets/backup_passphrase` stops it).
**Do:** `dc up -d backup`. If web itself is down, fix that first: the service needs it.

### WalArchiveFailing
**Means:** PostgreSQL failed to archive its last WAL segment and keeps retrying. Point-in-time recovery has stopped, and unarchived WAL piles up on the database's disk until it fills.
**Check:** `dc logs --tail 100 postgres | grep -i archive`; space where backups are kept (`df -h`); is `/var/backups/smart-iptv` (BACKUP_DIR) mounted and writable?
**Do:** free or add space, then confirm the alert clears within minutes. If archiving cannot be fixed quickly, switch backups off in Settings → Backups: PostgreSQL then releases WAL instead of keeping it (point-in-time recovery stops until you switch them on again and a new full backup runs).

## Meta

### Watchdog
**Means:** nothing: it always fires, to prove the alerting pipeline is alive. It never reaches email or Telegram.
**Optional dead man's switch:** set `ALERT_WATCHDOG_URL` in `.env` to a push URL of a monitor you run elsewhere (for example a self-hosted Uptime Kuma "push" monitor on another machine), then `scripts/secrets.sh` and `dc up -d alertmanager`. Alertmanager posts to it every 5 minutes; when the posts stop, that monitor alerts you that this server, or its monitoring, is down.

### FireDrill
**Means:** a test, started from the admin's Monitoring page (**Send a test alert**) or with `manage.py fire_drill`. It travels exactly like a real alert: scrape, rule, Alertmanager, Django, email and Telegram.
**Do:** confirm the email and the Telegram message both arrived, then stop it (or let it resolve after 10 minutes). If one didn't arrive, see AlertDeliveryFailing.
