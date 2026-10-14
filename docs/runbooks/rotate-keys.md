# Runbook: rotate keys and passwords

`scripts/rotate-keys.sh` changes a key or password on a running server without signing anyone out unnecessarily (ADR-0020).
- New values are generated on the server and written to `.env` (mode 600) or `secrets/`. They are never printed, and never on a command line.
- Every step is recorded in the audit log as `keys.rotate`, with the kind as the target (`media_token`, `secret_key`, `field_encryption`, `db`, `valkey`).
- The admin's **Settings → System** page shows the last rotation of each kind.

```bash
sudo /opt/iptv/scripts/rotate-keys.sh --help
sudo /opt/iptv/scripts/rotate-keys.sh media-token        # asks before changing anything; --yes skips the question
```

The script finds the installation from `/etc/smart-iptv/install.conf`, and the overlays (monitoring, live ingest, CrowdSec) from `.env`. `--dev` targets the development stack.

**When to rotate:**
- yearly as routine;
- at once when someone with access leaves;
- at once when a secret may have leaked: see [leaked-credential.md](leaked-credential.md), which says which rotation fits each leak.

The backups hold the old values (nightly, encrypted). That is fine for routine rotation, but after a leak remember that restoring an old `.env` brings the old secrets back.

## Media URL signing key (`media-token`)
It signs every playback URL. The edge accepts the current and the previous key (by `kid`).
1. `rotate-keys.sh media-token`:
   - **stage:** adds a new key that the edge accepts but Django doesn't sign with yet; every edge reloads;
   - **standalone edges (M15, ADR-0022):** the script waits, up to 5 minutes, until every enabled standalone edge on the admin's **Edges** page reports the new key on its control channel (`manage.py edges_wait_keys`). A down edge holds the rotation: disable it on the Edges page first, or fix it;
   - **promote:** five seconds later, Django signs with the new key, and the old one becomes "previous".
   
   Playback never stops.
2. `rotate-keys.sh media-token --finish` once the longest URL lifetime has passed. That is the larger of `playback.token_ttl_vod_s` and `playback.token_ttl_live_s` (6 hours by default), plus 30 seconds of clock skew. The first step prints the earliest time, and `--finish` refuses before it. This retires the old key.

**After a leak:** lower both TTLs in **Settings → Playback** (the minimum is 60 seconds) before step 1. `--finish` is then allowed a couple of minutes later, and every URL signed with the old key stops working the moment it is retired. Put the TTLs back afterwards.

## Django secret key (`secret-key`)
It signs admin and portal sessions, password-reset and invite links, and CSRF tokens.
1. `rotate-keys.sh secret-key`: the new key signs; the old one moves to `DJANGO_SECRET_KEY_FALLBACKS` and still verifies, so nobody is signed out.
2. `rotate-keys.sh secret-key --finish`:
   - routine: after a month (portal sessions last up to `security.customer_session_days`, 30);
   - after a leak: right away, and then everyone signs in again.

## Field encryption key (`field-encryption`)
`FIELD_ENCRYPTION_KEY` encrypts the secrets stored in the database:
- the settings' secrets (TMDB, SMTP, payments, Telegram, the backup bucket);
- TOTP secrets;
- live channel sources;
- live integration credentials;
- EPG source URLs.

One command does it all:
1. puts the new key first (the old one still decrypts);
2. restarts the app services;
3. re-encrypts every stored value with the new key in one transaction (`manage.py key_rotation reencrypt`, which includes `apps.core.services.reencrypt_secret_settings()`);
4. checks that the new key alone decrypts everything;
5. drops the old key and restarts again.

If step 4 finds values only the old key can read, the script stops with both keys in place (nothing breaks) and lists them (`manage.py key_rotation check-first-key`). Re-enter those values in the admin, or run `key_rotation reencrypt` again, then rerun the script.

## Database passwords (`db-app`, `db-reporting`, `db-monitor`, `db-owner`)
| Kind | Role | How |
|---|---|---|
| `db-app` | `iptv_app` (the app services) | `.env`, then `db-roles` sets it, then the app services restart |
| `db-reporting` | `iptv_reporting` | the same; give the new password to whoever uses reporting |
| `db-monitor` | `iptv_monitor` (postgres-exporter) | `.env` and `secrets/monitoring`, then the monitoring role and exporter restart |
| `db-owner` | the owner (`POSTGRES_USER`) | `ALTER ROLE` in PostgreSQL first, then `.env` and the Docker secrets, then the app services restart |

Each restarts the app services once (a few seconds of errors for requests in flight).

## Valkey passwords (`valkey-app`, `valkey-exporter`, `valkey-default`)
For both Valkeys:
1. the new password is added to the running user (`ACL SETUSER ... >new`), so clients holding the old one keep working;
2. `.env` is updated, and the consumers restart (the app services, the exporter, or nothing for `default`);
3. the Valkeys restart once (clients reconnect within a second), and their ACL file then holds only the new password.

redis-state is persistent (AOF), so sessions and slots survive the restart.

## Edge secrets and the replication password (M15)
- **A standalone edge's secret:** admin → **Edges** → **New secret** (step-up), then `install.sh --reconfigure` on the edge's host with the new one ([add-edge.md](add-edge.md#day-to-day)). The old secret stops working at once.
- **The CDN origin secret** (`cdn.origin_secret`) and Bunny's token key: change them in Settings → Integrations and at Bunny together ([cdn-bunny.md](cdn-bunny.md)). Standalone edges pick up the new origin secret within 15 s.
- **The S3 keys** (`storage.s3_*`): create new keys at the provider, save them in Settings → Storage, press **Test**, then delete the old keys at the provider. Edges receive the new read-only key on their next poll.
- **`POSTGRES_REPLICATION_PASSWORD`:** see [database-replica.md](database-replica.md#passwords-and-roles).

## Secrets the script doesn't cover
| Secret | How to rotate |
|---|---|
| `MEILI_MASTER_KEY` | delete its line from `.env`, run `scripts/secrets.sh` (it generates missing keys), then `dc up -d --force-recreate meilisearch web worker beat watcher transcoder live` |
| `BACKUP_AGENT_TOKEN` | delete its line, run `scripts/secrets.sh` (it also refreshes `secrets/runtime/`), then `dc up -d --force-recreate web backup` |
| `CROWDSEC_BOUNCER_KEY` | delete its line, run `scripts/secrets.sh`, run `dc exec crowdsec cscli bouncers delete traefik`, then `dc up -d --force-recreate crowdsec traefik` |
| Integration secrets (TMDB, SMTP, Stripe, Moyasar, Telegram, the backup bucket) | change them at the provider, then in the admin under **Settings → Integrations** or **Settings → Backups** |
| The backup passphrase | can't be changed for existing repositories; see [backups.md](backups.md). If it leaked, the backups are readable by whoever has them too: start new repositories with a new passphrase and delete the old ones when the new ones have enough history |
| Admin and customer passwords, TOTP, passkeys | in the admin (**Admins**, **Customers**), or by the person; see [leaked-credential.md](leaked-credential.md) |

## Checking a rotation
- **Audit log:** filter by action `keys.rotate`. Each step has the phase (`staged`, `signing`, `retired`, `started`, `finished`, `completed`) and the role or user.
- **Playback:** play a title in the portal and in an IPTV app.
- **Admin:** sign in. With `secret-key --finish`, expect to sign in again.
- `make smoke` (development) or the Verify list in [deploy.md](deploy.md#verify).

The rotations are tested end to end on a throwaway compose project (each kind, with playback, sign-in, the stored secrets and the backups checked afterwards).
