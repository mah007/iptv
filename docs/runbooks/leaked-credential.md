# Runbook: a credential leaked

What to do when a password, key or token may be in the wrong hands. Act first, investigate after: every step below can be undone, and a leaked secret can't be un-leaked.
- For the investigation, see [incident-response.md](incident-response.md).
- For the rotation commands, see [rotate-keys.md](rotate-keys.md).

```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'   # plus your overlays
```

## Find the row that matches
| What leaked | Do now | Then |
|---|---|---|
| **A customer's IPTV login** (shared on a forum, many addresses using it) | Admin → **Customers** → the customer → **Devices** → **Reset password** on that device (the old Xtream password stops working at once); **Sessions** → **Stop** on their open streams | Send the customer the new login. Repeat offenders: **Suspend**. Lower concurrency with the plan or access rules |
| **A customer's portal password** | **Customers** → the customer → **Portal password link**; setting the new password ends their portal and app sign-ins | Check **Devices** for devices they don't recognise and **Revoke** them |
| **A playback URL** | It expires on its own (`playback.token_ttl_vod_s`, 2 h; live 6 h). To stop it now, **Sessions** → **Stop** | If URLs leak routinely, turn on `playback.ip_binding` (URLs then work only from the address that asked for them) |
| **An admin's password** | Admin → **Admins** → that admin → status **Suspended**: they can't sign in, and their open sessions lose all access at once | Set a new password with `dc exec web python manage.py changepassword <sign-in name>` (it prompts), then reactivate. Read the audit log for what the account did ([incident-response.md](incident-response.md#3-find-out-what-happened)) |
| **An admin's authenticator (TOTP), security key and password** | Suspend the admin, as above | Set a new password (as above), then **Admins** → the admin → **Reset sign-in confirmation** (or `dc exec -T web python manage.py reset_admin_mfa <sign-in name>` on the server) and reactivate: their next sign-in enrols a new authenticator ([security.md](security.md#locked-out-admins)) |
| **Many customers' logins for one app** (a MAC-app vendor's cloud breached, a public dump of one app's playlists) | Admin → **Customers** → **Revoke by app**: pick the app, check the count, tick *Email the customers*, revoke. Every login made for that app stops at once, with its streams | Customers make new logins in the portal. Watch **Customers** filtered by *Sharing: flagged* for the following days |
| **`.env`** (a copy left somewhere, a screenshot, a public paste) | Rotate, in this order: `secret-key` then `secret-key --finish` (everyone signs in again); `field-encryption`; `db-owner`, `db-app`, `db-reporting` (and `db-monitor` with monitoring); `valkey-default`, `valkey-app` (and `valkey-exporter`); `MEILI_MASTER_KEY`, `BACKUP_AGENT_TOKEN` and `CROWDSEC_BOUNCER_KEY` by hand | The data stores aren't reachable from outside (no published ports), so the database and Valkey passwords alone don't expose data, but rotate them all the same |
| **`secrets/`** | Rotate `media-token`, then `media-token --finish` with short TTLs (see rotate-keys.md); `BACKUP_AGENT_TOKEN`; `CROWDSEC_BOUNCER_KEY`; with monitoring, the passwords under `secrets/monitoring` (they come from `.env`: rotate `db-monitor` and `valkey-exporter`) | `secrets/backup_passphrase` is in there too: see "The backups" below |
| **`FIELD_ENCRYPTION_KEY` alone** | `rotate-keys.sh field-encryption` | Only matters together with a copy of the database. If both may have leaked, also rotate every integration secret below |
| **An integration secret** (TMDB, TheTVDB, SMTP, Stripe, Moyasar, Telegram bot, the backup bucket's keys) | Revoke it **at the provider** first (Stripe and Moyasar dashboards, BotFather `/revoke`, the bucket's key page, the SMTP account) | Enter the new one in **Settings → Integrations** (or **Settings → Backups** for the bucket) and press **Test**. Payments: check the provider's dashboard for refunds or payouts you didn't make |
| **The backups** (a copy of `BACKUP_DIR` or the bucket) | Without the passphrase they are unreadable (AES-256). Take away the copy's access: the bucket's keys, a lost disk | **If the passphrase leaked too**, treat it as a leak of everything: the database (customers, password hashes, encrypted secrets), `.env` and `secrets/`. Do every row above, then start new repositories with a new passphrase (below) |
| **SSH access to the server** | Remove the key from `~/.ssh/authorized_keys` of every user; check `last` and `/var/log/auth.log` | Assume the server is compromised: [incident-response.md](incident-response.md#a-compromised-server) |

## Sharing flags
The sharing score (ADR-0021) runs every 5 minutes over the networks (ASNs), countries and TV logins each customer was seen on, from IP geolocation (DB-IP Lite). It flags a customer when the points reach `antisharing.flag_score` (4):

| Signal | Points | Limit (setting) |
|---|---|---|
| Providers (ASNs) in the last hour | 2 for each one above the limit | `antisharing.max_asns_1h` (2) |
| Providers in the last 24 hours | 1 for each one above the limit | `antisharing.max_asns_24h` (3) |
| Countries in the last 24 hours | 2 for each one above the limit | `antisharing.max_countries_24h` (1) |
| Impossible journeys: two countries further apart than `antisharing.travel_min_km` (500 km) in less time than `antisharing.travel_speed_kmh` (900 km/h) allows | 3 for each, counting up to 3 | |
| Networks one TV login was used from in an hour (address changes within one provider count only when the networks were used at the same time) | 2 for each one above the limit | `antisharing.max_networks_per_credential_1h` (2) |

A phone on mobile data and a TV on home broadband in one country score nothing. **A flag never blocks anything.** Admins with *customers.view* see it in **Customers** (filter *Sharing: flagged*) and on the customer's **Security** tab, with the evidence; with `antisharing.notify_admins`, the alert recipients and Telegram are told once per flag.

What to do, step by step (each step is audited and the customer gets an email):
1. **Look first:** a trip abroad, a VPN, or a family member at university explain many flags. If so, **Mark reviewed** with a note.
2. **Warn:** the customer is told their account looks shared and that sharing breaks the terms.
3. **Reset app logins** (needs *devices.manage* and a step-up): every TV login gets a password nobody knows and its streams stop; the customer makes new ones in the portal.
4. **Suspend:** playback stops everywhere; they can still sign in to the portal. Reactivate from the customer page.

The evidence is kept `privacy.antisharing_retention_days` (90) days. If the IP geolocation database is missing, countries and providers are unknown and the score sees only networks ([security.md](security.md#ip-geolocation-db-ip-lite)).

## Rotate the backup passphrase
The passphrase can't be changed for an existing repository, so new repositories replace the old:
1. Turn backups off (**Settings → Backups**), then stop the backup service: `dc stop backup`.
2. Move the old repositories aside: `BACKUP_DIR=$(sed -n 's/^BACKUP_DIR=//p' .env)`, then `mv "$BACKUP_DIR" "$BACKUP_DIR.old"`. For the bucket, change `backups.s3_prefix` to a new folder.
3. Write a new passphrase:
   ```bash
   umask 077; openssl rand -base64 48 | tr -d '\n' > secrets/backup_passphrase
   ```
   Store a copy offline.
4. `mkdir -p "$BACKUP_DIR"`, `dc up -d --wait`, then turn backups on. The first full backup and a drill run within a few minutes.
5. Once the new repositories have the history you need (2 weeks for the database, longer for file snapshots), delete `$BACKUP_DIR.old` and the bucket's old folder.

## Afterwards
- Note what leaked, when, how, and what you rotated, in your incident log.
- **The audit log:** every rotation (`keys.rotate`), suspension, credential reset and settings change is in it.
- **Customers' personal data:** if it may have been exposed (a database or backup leak, with the passphrase), your local data-protection law may require telling them and the regulator within a fixed time. In Saudi Arabia, the PDPL requires notifying SDAIA within 72 hours.
