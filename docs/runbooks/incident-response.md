# Runbook: incident response

For when something is wrong beyond a single alert: a suspected break-in, data that changed without explanation, an outage with no obvious cause, or a leak. For one alert at a time, see [alerts.md](alerts.md).

```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'   # plus your overlays
```

Keep a running log from the first minute: times in UTC, what you saw, what you did. It's the basis of the write-up and of any notification you owe.

## 1. Assess (minutes)
- **What's affected:** customers can't play? Admin misbehaving? Data wrong? Unknown activity?
- **Since when:**
  - Grafana's dashboards (admin → **Monitoring**);
  - the admin's dashboard;
  - `dc ps` (unhealthy or restarting services);
  - `dc logs --since 1h --tail 200 <service>`.
- **Is it an attack?** Signs:
  - admin actions nobody remembers (audit log);
  - new admins or roles;
  - settings changed;
  - CrowdSec alerts (`dc exec crowdsec cscli alerts list`);
  - floods of 401, 403 or 429 in Traefik's logs;
  - unknown SSH sign-ins (`last -a`, `/var/log/auth.log`);
  - unknown processes or containers (`docker ps -a`, `top`).

## 2. Contain
Choose the smallest step that stops the damage:
| Problem | Step |
|---|---|
| One admin account misused | **Admins** → status **Suspended**: they lose access at once |
| One customer or device abusing | **Customers** → **Suspend**; **Devices** → **Block**; **Sessions** → **Stop** |
| A flood from some addresses | `dc exec crowdsec cscli decisions add --ip <address> --duration 24h --reason incident` (or `--range <cidr>`), or the provider's firewall. ufw rules don't reach Docker's published ports |
| A leaked secret | [leaked-credential.md](leaked-credential.md) |
| The admin itself compromised | Suspend every admin but yourself (**Admins**). If you can't trust your own account either, take the server off the internet (see "A compromised server" below) |
| Data being destroyed | Stop the writers: `dc stop web worker beat watcher transcoder live`. Customers lose service, but nothing more is lost. WAL archiving has kept every change until now ([backups.md](backups.md)) |

## 3. Find out what happened
- **The audit log** (admin → **Audit**): every admin action, settings change, sign-in to the admin, suspension, credential reset, key rotation, backup and retention run. It records who, from which address, and the before and after (secrets never). The database itself refuses edits and deletes (append-only, purge only after a year).
- **Application logs:**
  - with monitoring: Grafana → Explore → Loki, e.g. `{service="web"} |= "error"`, or `{service="traefik"} | json | DownstreamStatus >= 400`;
  - without: `dc logs --since 24h web worker traefik`.
  
  Logs never contain passwords or tokens.
- **Traefik's access log:** the address, status, user agent and router of every request, without paths.
- **Database:** the restore drill's throwaway copy is a safe place to compare data. Restoring to a time before the incident into a separate project lets you diff without touching production (ask for help, or follow [backups.md](backups.md) on another machine).
- **Host:**
  - `last -a`, `journalctl --since '-2 days'`;
  - `/var/log/auth.log`;
  - `docker events --since 24h`;
  - files changed recently in `/opt/iptv` (`git -C /opt/iptv status` shows local changes to the code).

## 4. Recover
- **Data damaged:** restore to just before the incident ([backups.md](backups.md#restore-the-database-in-place-to-now-a-time-or-a-restore-point)). Writes after that point are lost, so note them first (payments: the providers' dashboards).
- **Code or configuration changed:** `git -C /opt/iptv status` and `git diff`, then `git checkout -- .` and `install.sh --update`. Compare `.env` with the last backup's copy ([backups.md](backups.md#restore-single-files-configuration-artwork)).
- **Secrets:** rotate whatever the attacker could have read ([leaked-credential.md](leaked-credential.md)).
- **Back in service:** the Verify list in [deploy.md](deploy.md#verify), a test playback, a test alert, then **Back up now**.

## A compromised server
If someone had root, a shell in a container, or the Docker socket, you can't trust anything on the server. Rebuild it rather than clean it:
1. **Keep the evidence** if you will need it: a provider snapshot of the disk.
2. **Take it off the internet:** the provider's firewall, or `dc stop traefik` (ufw can't do it: Docker publishes its ports past ufw). Keep your SSH session open.
3. **Rebuild on a new server** from the backups, as in disaster recovery ([backups.md](backups.md#disaster-recovery-the-server-is-gone)). Choose a restore point **before** the compromise, because later backups may contain the attacker's changes.
4. **Rotate everything** in the `.env` row of [leaked-credential.md](leaked-credential.md), plus `media-token`, the integration secrets at their providers, and the backup passphrase (new repositories).
5. **Point DNS at the new server**, and destroy the old one when you no longer need the evidence.

## 5. Afterwards
- **Write it up** (timeline, cause, impact, what worked, what to change) and make the changes: a new alert, a CrowdSec rule, a setting.
- **Notify** if personal data was exposed: customers, and the regulator where the law requires it. In Saudi Arabia, the PDPL requires notifying SDAIA within 72 hours of becoming aware.
- **Payments:** reconcile with the providers if the database was restored to an earlier time.
- **Review access:** remove admins and SSH keys that aren't needed.
