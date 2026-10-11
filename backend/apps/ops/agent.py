"""The backup agent: the `backup` service beside PostgreSQL (ADR-0020).

It runs in the `backup` target of `docker/postgres.Dockerfile`, which carries the same
PostgreSQL binaries as the database, pgBackRest and restic. It imports no Django (the
standard library only, like `apps.live.relay`), and it holds no schedule of its own:

- every `AGENT_POLL_S` it posts its status to web's internal
  `/internal/backups/agent/poll` and gets back the configuration (from the admin's
  settings, group Backups) and, when one is due, a job;
- it renders `pgbackrest.conf` (the repository cipher passphrase comes from its own
  secret file, never from Django), creates the stanza, and switches WAL archiving on by
  writing the flag that the database's `archive_command` checks;
- it runs the job and posts the result to `/internal/backups/agent/jobs/<id>`.

Jobs:
- `db_full`, `db_diff`: pgBackRest base backups into the local repository; WAL is
  archived continuously by PostgreSQL itself (`pgbackrest-iptv-archive`);
- `files`: a restic snapshot of the server's `.env` and `secrets/`, the artwork, a
  logical `pg_dump` and, when chosen, the media libraries, into the local restic
  repository; then `forget` (and `prune` when asked) by the retention settings;
- `offsite`: when an S3-compatible bucket is configured, a restic snapshot of the
  pgBackRest repository there (hourly), plus a copy of the night's local snapshots;
- `drill`: the restore drill: the latest backup restored into a throwaway PostgreSQL
  with all archived WAL replayed, then its migrations and row counts read back;
- `check`: `pgbackrest check` and each restic repository's reachability.

The process runs as root with only DAC_READ_SEARCH (restic reads the 0600 `.env`),
SETUID and SETGID (gosu) and CHOWN. pgBackRest, `pg_dump` and the drill's PostgreSQL run
as the `postgres` user: pgBackRest refuses root for anything but restore. Secrets never
reach a command line, a log line or a result: every output is scrubbed of the
passphrase, the agent token and the S3 keys before it leaves.

Other commands, for the healthcheck and the runbooks:
- `health`: exit 0 while the loop's heartbeat is fresh;
- `restore-db --target latest|<time>|name:<restore point> --yes`: restore the database
  in place, with PostgreSQL stopped (docs/runbooks/backups.md);
- `render-config`: write `pgbackrest.conf` without Django (disaster recovery).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = "1"
STANZA = "main"
MASK = "***"
TAIL_LINES = 40
TAIL_CHARS = 6000
TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
#: Restore targets: a time PostgreSQL understands, or a named restore point.
TIME_TARGET = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}(:?\d{2})?|Z)?"
)
RESTORE_POINT = re.compile(r"[A-Za-z0-9._:-]{1,63}")
LAST_TRANSACTION = re.compile(r"last completed transaction was at log time (?P<at>[0-9 :.+-]+)")

logger = logging.getLogger("backup-agent")


class AgentError(Exception):
    """A step failed; the message is safe to report (already scrubbed)."""


# --- Configuration from the environment ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Paths:
    repo: Path = Path("/backups")
    config: Path = Path("/backup-config")
    drill: Path = Path("/drill")
    sources: Path = Path("/sources")
    pgdata: Path = Path("/var/lib/postgresql/18/docker")
    pg_socket: Path = Path("/var/run/postgresql")

    @property
    def pgbackrest_repo(self) -> Path:
        return self.repo / "pgbackrest"

    @property
    def restic_repo(self) -> Path:
        return self.repo / "restic"

    @property
    def pgbackrest_conf(self) -> Path:
        return self.config / "pgbackrest.conf"

    @property
    def archive_flag(self) -> Path:
        return self.config / "archive.enabled"

    @property
    def lock_dir(self) -> Path:
        return self.config / "lock"

    @property
    def heartbeat(self) -> Path:
        return self.config / "heartbeat"

    @property
    def restic_cache(self) -> Path:
        return self.config / "restic-cache"


@dataclass(frozen=True, slots=True)
class Env:
    api_url: str = "http://web:8000/internal/backups/agent"
    api_host: str = "web"
    token_file: Path = Path("/run/secrets/backup_agent_token")
    passphrase_file: Path = Path("/run/secrets/backup_passphrase")
    pg_user: str = "iptv"
    pg_database: str = "iptv"
    run_as: str = "postgres"
    poll_s: float = 30.0
    paths: Paths = field(default_factory=Paths)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Env:
        def path(name: str, default: Path) -> Path:
            return Path(environ.get(name) or default)

        base = cls()
        defaults = base.paths
        return cls(
            api_url=environ.get("AGENT_URL") or base.api_url,
            api_host=environ.get("AGENT_HOST") or base.api_host,
            token_file=path("AGENT_TOKEN_FILE", base.token_file),
            passphrase_file=path("BACKUP_PASSPHRASE_FILE", base.passphrase_file),
            pg_user=environ.get("PGUSER") or base.pg_user,
            pg_database=environ.get("PGDATABASE") or base.pg_database,
            run_as=environ.get("PG_RUN_AS") or base.run_as,
            poll_s=float(environ.get("AGENT_POLL_S") or base.poll_s),
            paths=Paths(
                repo=path("BACKUP_REPO_DIR", defaults.repo),
                config=path("BACKUP_CONFIG_DIR", defaults.config),
                drill=path("BACKUP_DRILL_DIR", defaults.drill),
                sources=path("BACKUP_SOURCES_DIR", defaults.sources),
                pgdata=path("PGDATA", defaults.pgdata),
                pg_socket=path("PGHOST", defaults.pg_socket),
            ),
        )


def read_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentError(f"cannot read {path.name}: {exc.strerror}") from None
    if not value:
        raise AgentError(f"{path.name} is empty")
    return value


# --- What Django sends ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class S3Target:
    endpoint: str
    bucket: str
    prefix: str
    region: str
    access_key: str
    secret_key: str
    path_style: bool

    @property
    def repository(self) -> str:
        endpoint = self.endpoint.rstrip("/")
        prefix = self.prefix.strip("/")
        return f"s3:{endpoint}/{self.bucket}" + (f"/{prefix}" if prefix else "")

    @classmethod
    def parse(cls, raw: object) -> S3Target | None:
        if not isinstance(raw, Mapping):
            return None
        endpoint = str(raw.get("endpoint") or "")
        bucket = str(raw.get("bucket") or "")
        if not endpoint.startswith(("https://", "http://")) or not bucket:
            return None
        return cls(
            endpoint=endpoint,
            bucket=bucket,
            prefix=str(raw.get("prefix") or ""),
            region=str(raw.get("region") or ""),
            access_key=str(raw.get("access_key") or ""),
            secret_key=str(raw.get("secret_key") or ""),
            path_style=bool(raw.get("path_style")),
        )


@dataclass(frozen=True, slots=True)
class Config:
    enabled: bool = False
    db_full_retention: int = 2
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6
    media: bool = False
    s3: S3Target | None = None

    @classmethod
    def parse(cls, raw: object) -> Config:
        if not isinstance(raw, Mapping):
            return cls()

        def number(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(raw.get(name, default))
            except (TypeError, ValueError):
                return default
            return min(max(value, low), high)

        return cls(
            enabled=bool(raw.get("enabled")),
            db_full_retention=number("db_full_retention", 2, 1, 52),
            keep_daily=number("keep_daily", 7, 1, 365),
            keep_weekly=number("keep_weekly", 4, 0, 104),
            keep_monthly=number("keep_monthly", 6, 0, 120),
            media=bool(raw.get("media")),
            s3=S3Target.parse(raw.get("s3")),
        )


# --- Running commands -------------------------------------------------------------------


@dataclass(slots=True)
class Result:
    code: int
    output: str


class Scrubber:
    """Replaces every known secret with *** in text that leaves the agent."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, *values: str) -> None:
        self._secrets.update(value for value in values if value and len(value) >= 4)

    def __call__(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, MASK)
        return text


def tail(text: str, lines: int = TAIL_LINES, chars: int = TAIL_CHARS) -> str:
    kept = "\n".join(text.strip().splitlines()[-lines:])
    return kept[-chars:]


type Runner = Callable[..., Result]


def run_command(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin: int | None = None,
) -> Result:
    """Run a command, output merged; never raises for a non-zero exit."""
    try:
        completed = subprocess.run(  # noqa: S603 (fixed argument lists built here)
            list(args),
            env=dict(env) if env is not None else None,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output.decode("utf-8", "replace") if isinstance(exc.output, bytes) else ""
        return Result(124, output + f"\ntimed out after {timeout:.0f} s")
    except OSError as exc:
        return Result(127, f"cannot run {args[0]}: {exc.strerror}")
    return Result(completed.returncode, completed.stdout.decode("utf-8", "replace"))


# --- The agent --------------------------------------------------------------------------


class Agent:
    def __init__(
        self,
        env: Env,
        *,
        runner: Runner = run_command,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        is_root: bool | None = None,
    ) -> None:
        self.env = env
        self.paths = env.paths
        self.run = runner
        self.clock = clock
        self.sleep = sleep
        self.is_root = os.geteuid() == 0 if is_root is None else is_root
        self.scrub = Scrubber()
        self.config = Config()
        self._passphrase: str | None = None
        self._token: str | None = None
        self._conf_digest = ""
        self._stanza_ready = False
        self._s3_ready: str | None = None
        self._status_cache: dict[str, Any] = {}
        self._status_at = 0.0

    # Secrets -------------------------------------------------------------------------

    @property
    def passphrase(self) -> str:
        if self._passphrase is None:
            self._passphrase = read_secret(self.env.passphrase_file)
            self.scrub.add(self._passphrase)
        return self._passphrase

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = read_secret(self.env.token_file)
            self.scrub.add(self._token)
        return self._token

    # Command helpers -----------------------------------------------------------------

    def as_postgres(self, args: Sequence[str]) -> list[str]:
        """Commands that must not run as root (pgBackRest, pg_dump, the drill)."""
        return ["gosu", self.env.run_as, *args] if self.is_root else list(args)

    def pgbackrest(self, *args: str) -> list[str]:
        return self.as_postgres(
            ["pgbackrest", f"--config={self.paths.pgbackrest_conf}", f"--stanza={STANZA}", *args]
        )

    def restic_env(self, s3: S3Target | None = None) -> dict[str, str]:
        env = {
            "PATH": os.environ.get(
                "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "HOME": "/tmp",  # noqa: S108 (restic's own default cache lives elsewhere)
            "RESTIC_PASSWORD_FILE": str(self.env.passphrase_file),
            "RESTIC_CACHE_DIR": str(self.paths.restic_cache),
            "RESTIC_REPOSITORY": str(self.paths.restic_repo),
        }
        if s3 is not None:
            env.update(
                {
                    "RESTIC_REPOSITORY": s3.repository,
                    "AWS_ACCESS_KEY_ID": s3.access_key,
                    "AWS_SECRET_ACCESS_KEY": s3.secret_key,
                }
            )
            if s3.region:
                env["AWS_DEFAULT_REGION"] = s3.region
            self.scrub.add(s3.access_key, s3.secret_key)
        return env

    def restic(self, *args: str, s3: S3Target | None = None) -> list[str]:
        """A restic command line; the repository and its secrets travel in `restic_env`."""
        options = ["--quiet"]
        if s3 is not None:
            options.append(f"--option=s3.bucket-lookup={'path' if s3.path_style else 'dns'}")
            if s3.region:
                options.append(f"--option=s3.region={s3.region}")
        return ["restic", *options, *args]

    def from_local(self) -> list[str]:
        """restic's options naming the local repository as the source (init, copy)."""
        return [
            f"--from-repo={self.paths.restic_repo}",
            f"--from-password-file={self.env.passphrase_file}",
        ]

    def must(self, what: str, args: Sequence[str], **kwargs: Any) -> str:
        result = self.run(args, **kwargs)
        if result.code != 0:
            raise AgentError(
                f"{what} failed (exit {result.code}):\n{self.scrub(tail(result.output))}"
            )
        return result.output

    # Files and directories -----------------------------------------------------------

    def chown_postgres(self, path: Path) -> None:
        if self.is_root:
            shutil.chown(path, user=self.env.run_as, group=self.env.run_as)

    def prepare_dirs(self) -> None:
        """The repository and config folders, each owned by the user that writes it."""
        for path, owner_postgres, mode in (
            (self.paths.config, False, 0o755),
            (self.paths.lock_dir, True, 0o700),
            (self.paths.restic_cache, False, 0o700),
            (self.paths.pgbackrest_repo, True, 0o750),
            (self.paths.restic_repo, False, 0o700),
            (self.paths.drill, True, 0o700),
        ):
            path.mkdir(parents=True, exist_ok=True)
            if owner_postgres:
                self.chown_postgres(path)
            with contextlib.suppress(PermissionError):
                path.chmod(mode)

    def write_private(self, path: Path, text: str) -> None:
        """Write atomically, 0600, readable by the postgres user (the database reads it)."""
        temporary = path.with_name(f".{path.name}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.chown_postgres(temporary)
        temporary.replace(path)

    # pgBackRest configuration --------------------------------------------------------

    def render_pgbackrest(self, config: Config) -> str:
        return "\n".join(
            [
                "# Written by the backup agent (apps/ops/agent.py); changes are overwritten.",
                "[global]",
                f"repo1-path={self.paths.pgbackrest_repo}",
                "repo1-retention-full-type=count",
                f"repo1-retention-full={config.db_full_retention}",
                "repo1-cipher-type=aes-256-cbc",
                f"repo1-cipher-pass={self.passphrase}",
                "compress-type=zst",
                "compress-level=3",
                "process-max=2",
                "start-fast=y",
                "archive-check=y",
                f"lock-path={self.paths.lock_dir}",
                "log-level-console=info",
                "log-level-stderr=off",
                "log-level-file=off",
                "",
                f"[{STANZA}]",
                f"pg1-path={self.paths.pgdata}",
                f"pg1-socket-path={self.paths.pg_socket}",
                f"pg1-user={self.env.pg_user}",
                f"pg1-database={self.env.pg_database}",
                "",
            ]
        )

    def apply_config(self, config: Config) -> None:
        """Render the configuration; create the stanza; switch WAL archiving on or off."""
        self.config = config
        self.prepare_dirs()
        text = self.render_pgbackrest(config)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != self._conf_digest or not self.paths.pgbackrest_conf.exists():
            self.write_private(self.paths.pgbackrest_conf, text)
            self._conf_digest = digest
        if not config.enabled:
            self.paths.archive_flag.unlink(missing_ok=True)
            return
        if not self._stanza_ready:
            self.must("pgbackrest stanza-create", self.pgbackrest("stanza-create"), timeout=600)
            self._stanza_ready = True
        if not self.paths.archive_flag.exists():
            self.paths.archive_flag.write_text(
                "WAL archiving on (backup agent)\n", encoding="utf-8"
            )
            logger.info("WAL archiving switched on")

    # Status --------------------------------------------------------------------------

    def pgbackrest_info(self) -> list[dict[str, Any]]:
        result = self.run(self.pgbackrest("--output=json", "info"), timeout=120)
        if result.code != 0:
            return []
        try:
            stanzas = json.loads(result.output)
        except ValueError:
            return []
        backups = []
        for stanza in stanzas if isinstance(stanzas, list) else []:
            for backup in stanza.get("backup", []):
                info = backup.get("info", {})
                repository = info.get("repository", {})
                stamps = backup.get("timestamp", {})
                backups.append(
                    {
                        "label": backup.get("label", ""),
                        "type": backup.get("type", ""),
                        "start": stamps.get("start"),
                        "stop": stamps.get("stop"),
                        "size": info.get("size"),
                        "repo_size": repository.get("delta"),
                        "error": bool(backup.get("error")),
                    }
                )
        return backups

    def restic_summary(self, s3: S3Target | None = None) -> dict[str, Any]:
        result = self.run(
            self.restic("snapshots", "--json", s3=s3), env=self.restic_env(s3), timeout=300
        )
        if result.code != 0:
            return {"ok": False, "snapshots": 0, "latest": None}
        try:
            snapshots = json.loads(result.output or "[]")
        except ValueError:
            return {"ok": False, "snapshots": 0, "latest": None}
        times = sorted(str(item.get("time", "")) for item in snapshots if isinstance(item, dict))
        return {"ok": True, "snapshots": len(times), "latest": times[-1] if times else None}

    def disk(self) -> dict[str, int]:
        try:
            usage = shutil.disk_usage(self.paths.repo)
        except OSError:
            return {}
        return {"total_bytes": usage.total, "free_bytes": usage.free}

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        """What the admin's Backups page shows; the slow parts every 10 minutes at most."""
        now = self.clock()
        if refresh or not self._status_cache or now - self._status_at > 600:
            cache: dict[str, Any] = {
                "db_backups": [],
                "local_snapshots": None,
                "offsite_snapshots": None,
            }
            if self.config.enabled and self._stanza_ready:
                cache["db_backups"] = self.pgbackrest_info()[-20:]
            if (self.paths.restic_repo / "config").exists():
                cache["local_snapshots"] = self.restic_summary()
            if self.config.s3 is not None and self._s3_ready == self.config.s3.repository:
                cache["offsite_snapshots"] = self.restic_summary(self.config.s3)
            self._status_cache = cache
            self._status_at = now
        return {
            "version": VERSION,
            "archiving": self.paths.archive_flag.exists(),
            "stanza": self._stanza_ready,
            "passphrase": self.env.passphrase_file.exists(),
            "disk": self.disk(),
            **self._status_cache,
        }

    # Talking to Django ---------------------------------------------------------------

    def api(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(  # noqa: S310 (fixed internal http URL)
            f"{self.env.api_url}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Host": self.env.api_host,
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise AgentError("unexpected answer from Django")
        return data

    def report(self, job_id: str, payload: Mapping[str, Any]) -> None:
        clean = json.loads(self.scrub(json.dumps(payload)))
        for attempt in range(5):
            try:
                self.api(f"/jobs/{job_id}", clean)
            except (OSError, ValueError, AgentError) as exc:
                logger.warning("could not report job %s (%s); retrying", job_id, type(exc).__name__)
                self.sleep(min(60.0, 5.0 * (attempt + 1)))
            else:
                return

    # Jobs ----------------------------------------------------------------------------

    def run_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(job.get("kind", ""))
        raw_params = job.get("params")
        params: Mapping[str, Any] = raw_params if isinstance(raw_params, Mapping) else {}
        handlers: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]] = {
            "db_full": lambda j, p: self.db_backup("full"),
            "db_diff": lambda j, p: self.db_backup("diff"),
            "files": lambda j, p: self.files_backup(prune=bool(p.get("prune"))),
            "offsite": lambda j, p: self.offsite(
                full=bool(p.get("full")), prune=bool(p.get("prune"))
            ),
            "drill": lambda j, p: self.drill(j),
            "check": lambda j, p: self.check(),
        }
        handler = handlers.get(kind)
        if handler is None:
            raise AgentError(f"unknown job kind {kind!r}")
        if kind != "check" and not self.config.enabled:
            raise AgentError("backups are switched off in the settings")
        return handler(job, params)

    def db_backup(self, kind: str) -> dict[str, Any]:
        before = {backup["label"] for backup in self.pgbackrest_info()}
        self.must(
            f"pgbackrest backup ({kind})",
            self.pgbackrest(f"--type={kind}", "backup"),
            timeout=12 * 3600,
        )
        backups = self.pgbackrest_info()
        new = [backup for backup in backups if backup["label"] not in before]
        latest = new[-1] if new else (backups[-1] if backups else {})
        return {
            "detail": {
                "label": latest.get("label", ""),
                "type": latest.get("type", kind),
                "size": latest.get("size"),
                "repo_size": latest.get("repo_size"),
                "backups_kept": len(backups),
            },
            "size_bytes": latest.get("repo_size"),
        }

    def ensure_restic(self, s3: S3Target | None = None) -> None:
        env = self.restic_env(s3)
        if s3 is None:
            if (self.paths.restic_repo / "config").exists():
                return
            self.must("restic init (local)", self.restic("init", s3=None), env=env, timeout=300)
            return
        if self._s3_ready == s3.repository:
            return
        probe = self.run(self.restic("cat", "config", s3=s3), env=env, timeout=300)
        if probe.code != 0:
            # Same chunker parameters as the local repository, so copies deduplicate.
            self.ensure_restic()
            init = self.restic("init", "--copy-chunker-params", *self.from_local(), s3=s3)
            self.must("restic init (S3)", init, env=env, timeout=600)
        self._s3_ready = s3.repository

    def file_sources(self) -> list[str]:
        sources = self.paths.sources
        paths = [sources / "config", sources / "media-data" / "images"]
        if self.config.media:
            paths.append(sources / "media")
        return [str(path) for path in paths if path.exists()]

    def forget_args(self, *, prune: bool) -> list[str]:
        args = [
            "forget",
            "--group-by=host,tags",
            f"--keep-daily={self.config.keep_daily}",
            f"--keep-weekly={self.config.keep_weekly}",
            f"--keep-monthly={self.config.keep_monthly}",
        ]
        if prune:
            args.append("--prune")
        return args

    def files_backup(self, *, prune: bool) -> dict[str, Any]:
        self.ensure_restic()
        env = self.restic_env()
        sources = self.file_sources()
        if not sources:
            raise AgentError("nothing to back up: the sources are not mounted")
        common = ["--host=smart-iptv", "--json"]
        files = self.must(
            "restic backup (files)",
            self.restic("backup", *common, "--tag=files", "--exclude-caches", *sources),
            env=env,
            timeout=24 * 3600,
        )
        dump_command = self.as_postgres(
            [
                "pg_dump",
                f"--host={self.paths.pg_socket}",
                f"--username={self.env.pg_user}",
                "--format=custom",
                "--compress=0",
                "--no-password",
                self.env.pg_database,
            ]
        )
        dump = self.must(
            "restic backup (pg_dump)",
            self.restic(
                "backup", *common, "--tag=pgdump", "--stdin-filename=database.dump",
                "--stdin-from-command", "--", *dump_command,
            ),
            env=env,
            timeout=12 * 3600,
        )  # fmt: skip
        self.must(
            "restic forget", self.restic(*self.forget_args(prune=prune)), env=env, timeout=6 * 3600
        )
        summary = summarize_restic(files)
        dump_summary = summarize_restic(dump)
        return {
            "detail": {
                "snapshot": summary.get("snapshot_id", ""),
                "dump_snapshot": dump_summary.get("snapshot_id", ""),
                "files": summary.get("total_files_processed"),
                "media": self.config.media,
                "pruned": prune,
            },
            "size_bytes": (summary.get("data_added") or 0) + (dump_summary.get("data_added") or 0),
        }

    def offsite(self, *, full: bool, prune: bool) -> dict[str, Any]:
        s3 = self.config.s3
        if s3 is None:
            raise AgentError("no S3-compatible bucket is configured")
        self.ensure_restic(s3)
        env = self.restic_env(s3)
        repo = self.must(
            "restic backup (pgBackRest repository to S3)",
            self.restic(
                "backup",
                "--host=smart-iptv",
                "--json",
                "--tag=pgbackrest",
                str(self.paths.pgbackrest_repo),
                s3=s3,
            ),
            env=env,
            timeout=24 * 3600,
        )
        copied = False
        if full and (self.paths.restic_repo / "config").exists():
            # Several --tag filters are OR'ed: the night's files and pg_dump snapshots.
            copy = self.restic("copy", "--tag=files", "--tag=pgdump", *self.from_local(), s3=s3)
            self.must("restic copy (local snapshots to S3)", copy, env=env, timeout=24 * 3600)
            copied = True
        if full:
            self.must(
                "restic forget (S3)",
                self.restic(*self.forget_args(prune=prune), s3=s3),
                env=env,
                timeout=6 * 3600,
            )
        summary = summarize_restic(repo)
        return {
            "detail": {
                "snapshot": summary.get("snapshot_id", ""),
                "copied_files": copied,
                "bucket": s3.bucket,
                "pruned": prune and full,
            },
            "size_bytes": summary.get("data_added"),
        }

    def check(self) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        problems: list[str] = []
        if self.config.enabled:
            result = self.run(self.pgbackrest("check"), timeout=900)
            detail["pgbackrest"] = result.code == 0
            if result.code != 0:
                problems.append("pgbackrest check: " + self.scrub(tail(result.output, 10)))
        try:
            self.ensure_restic()
            probe = self.run(self.restic("cat", "config"), env=self.restic_env(), timeout=120)
            detail["local"] = probe.code == 0
        except AgentError as exc:
            detail["local"] = False
            problems.append(str(exc))
        if self.config.s3 is not None:
            try:
                self.ensure_restic(self.config.s3)
                probe = self.run(
                    self.restic("cat", "config", s3=self.config.s3),
                    env=self.restic_env(self.config.s3),
                    timeout=300,
                )
                detail["s3"] = probe.code == 0
                if probe.code != 0:
                    problems.append("S3: " + self.scrub(tail(probe.output, 10)))
            except AgentError as exc:
                detail["s3"] = False
                problems.append(str(exc))
        if problems:
            raise AgentError("\n".join(problems))
        return {"detail": detail}

    # The restore drill ---------------------------------------------------------------

    def drill(self, job: Mapping[str, Any]) -> dict[str, Any]:
        """Restore the latest backup into a throwaway PostgreSQL and read it back."""
        tables = [str(name) for name in job.get("tables", []) if TABLE_NAME.fullmatch(str(name))]
        params = job.get("params")
        timeout = (
            float(params.get("timeout_s", 3 * 3600)) if isinstance(params, Mapping) else 3 * 3600.0
        )
        self.drill_cleanup()
        try:
            return self._drill(tables, timeout)
        finally:
            self.drill_cleanup()

    def _drill(self, tables: list[str], timeout: float) -> dict[str, Any]:
        data = self.paths.drill / "pgdata"
        socket = self.paths.drill / "run"
        log = self.paths.drill / "postgres.log"
        started = self.clock()
        self.must(
            "pgbackrest restore (drill)",
            self.pgbackrest(
                f"--pg1-path={data}", "--archive-mode=off", "--type=default", "restore"
            ),
            timeout=timeout,
        )
        restored = self.clock()
        self.must(
            "prepare the drill's socket folder",
            self.as_postgres(["mkdir", "-p", "-m", "0700", str(socket)]),
        )
        # No TCP listener, its own socket, no archiving: nothing reaches it but this
        # agent, and it never writes to the repository. Settings recovery checks against
        # the primary (max_connections, ...) stay as the backup has them.
        options = (
            f"-c listen_addresses='' -c unix_socket_directories='{socket}' "
            "-c archive_mode=off -c logging_collector=off"
        )
        server = self.as_postgres(
            [
                "pg_ctl", "--pgdata", str(data), "--log", str(log), "--wait",
                f"--timeout={int(timeout)}", "--options", options, "start",
            ]
        )  # fmt: skip
        try:
            result = self.run(server, timeout=timeout + 60)
            if result.code != 0:
                raise AgentError(
                    f"the drill's PostgreSQL did not start (exit {result.code}):\n"
                    + self.scrub(tail(read_text(log), 15))
                )
            self.wait_promoted(socket, timeout=timeout)
            ready = self.clock()
            migrations = self.drill_query(
                socket,
                "SELECT coalesce(json_agg(json_build_array(app, name, applied)), '[]') "
                "FROM django_migrations",
            )
            counts, missing = self.drill_counts(socket, tables)
        finally:
            self.run(
                self.as_postgres(
                    ["pg_ctl", "--pgdata", str(data), "--mode=fast", "--wait", "stop"]
                ),
                timeout=300,
            )
        recovered_to = last_transaction(log)
        verify = self.run(self.pgbackrest("verify"), timeout=timeout)
        return {
            "detail": {
                "restore_s": round(restored - started, 1),
                "ready_s": round(ready - started, 1),
                "recovered_to": recovered_to,
                "migrations": json.loads(migrations or "[]"),
                "counts": counts,
                "missing_tables": missing,
                "verify_ok": verify.code == 0,
                "verify": self.scrub(tail(verify.output, 8)),
            },
            "duration_s": round(ready - started, 1),
        }

    def drill_cleanup(self) -> None:
        data = self.paths.drill / "pgdata"
        if (data / "postmaster.pid").exists():
            self.run(
                self.as_postgres(["pg_ctl", "--pgdata", str(data), "--mode=immediate", "stop"]),
                timeout=120,
            )
        for name in ("pgdata", "run", "postgres.log"):
            target = self.paths.drill / name
            if target.exists() or target.is_symlink():
                self.run(self.as_postgres(["rm", "-rf", "--", str(target)]), timeout=1800)

    def drill_psql(self, socket: Path, sql: str) -> list[str]:
        return self.as_postgres(
            [
                "psql", "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
                f"--host={socket}", f"--username={self.env.pg_user}",
                f"--dbname={self.env.pg_database}", "--set=ON_ERROR_STOP=1", "--command", sql,
            ]
        )  # fmt: skip

    def drill_query(self, socket: Path, sql: str) -> str:
        return self.must(
            "query the drill's database", self.drill_psql(socket, sql), timeout=600
        ).strip()

    def wait_promoted(self, socket: Path, *, timeout: float) -> None:
        deadline = self.clock() + timeout
        while True:
            result = self.run(self.drill_psql(socket, "SELECT pg_is_in_recovery()"), timeout=60)
            if result.code == 0 and result.output.strip() == "f":
                return
            if self.clock() > deadline:
                raise AgentError("the drill's PostgreSQL did not finish recovery in time")
            self.sleep(2.0)

    def drill_counts(self, socket: Path, tables: Iterable[str]) -> tuple[dict[str, int], list[str]]:
        names = list(tables)
        if not names:
            return {}, []
        present = set(
            self.drill_query(
                socket,
                "SELECT string_agg(relname, ',') FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')",
            ).split(",")
        )
        counts: dict[str, int] = {}
        missing = [name for name in names if name not in present]
        # Names match TABLE_NAME ([a-z_][a-z0-9_]*) and exist: safe to quote into SQL.
        selects = [
            f"SELECT '{name}', count(*) FROM public.\"{name}\""  # noqa: S608
            for name in names
            if name in present
        ]
        if selects:
            output = self.drill_query(socket, " UNION ALL ".join(selects))
            for line in output.splitlines():
                name, _, count = line.partition("|")
                if name and count.strip().isdigit():
                    counts[name] = int(count)
        return counts, missing

    # The loop ------------------------------------------------------------------------

    def poll_once(self) -> bool:
        """One exchange with Django; True when a job ran (poll again at once)."""
        response = self.api("/poll", {"status": self.status()})
        config = Config.parse(response.get("config"))
        try:
            self.apply_config(config)
        except AgentError as exc:
            logger.error("configuration: %s", exc)
        job = response.get("job")
        if not isinstance(job, Mapping) or not job.get("id"):
            return False
        job_id = str(job["id"])
        started = self.clock()
        logger.info("job %s (%s) started", job_id, job.get("kind"))
        try:
            outcome = self.run_job(job)
        except AgentError as exc:
            message = self.scrub(str(exc))
            logger.error("job %s failed: %s", job_id, message.splitlines()[0] if message else "")
            self.report(
                job_id,
                {
                    "status": "failed",
                    "error": message,
                    "duration_s": round(self.clock() - started, 1),
                },
            )
        except Exception as exc:  # a bug must not stop the loop
            message = self.scrub(f"{type(exc).__name__}: {exc}")
            logger.exception("job %s crashed", job_id)
            self.report(
                job_id,
                {
                    "status": "failed",
                    "error": message,
                    "duration_s": round(self.clock() - started, 1),
                },
            )
        else:
            outcome.setdefault("duration_s", round(self.clock() - started, 1))
            self.report(job_id, {"status": "succeeded", **outcome})
            logger.info("job %s succeeded", job_id)
        self.status(refresh=True)
        return True

    def loop(self, *, once: bool = False) -> None:
        while True:
            ran = False
            try:
                ran = self.poll_once()
            except (OSError, ValueError, AgentError) as exc:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                logger.warning(
                    "Django is not reachable (%s); retrying", self.scrub(str(reason))[:200]
                )
            self.paths.config.mkdir(parents=True, exist_ok=True)
            self.paths.heartbeat.touch()
            if once:
                return
            if not ran:
                self.sleep(self.env.poll_s)

    # Restore in place (runbook) ------------------------------------------------------

    def restore_db(self, target: str) -> None:
        pid = self.paths.pgdata / "postmaster.pid"
        if pid.exists():
            raise AgentError(
                "PostgreSQL is running or did not stop cleanly (postmaster.pid exists). "
                "Stop the postgres service first: docker compose ... stop postgres"
            )
        args = ["--delta", "--target-action=promote"]
        if target == "latest":
            args.append("--type=default")
        elif target.startswith("name:") and RESTORE_POINT.fullmatch(target[5:]):
            args += ["--type=name", f"--target={target[5:]}"]
        elif TIME_TARGET.fullmatch(target):
            args += ["--type=time", f"--target={target}"]
        else:
            raise AgentError(
                "--target must be latest, a time such as '2026-10-11 12:00:00+00', "
                "or name:<restore point>"
            )
        if not self.paths.pgbackrest_conf.exists():
            self.render_offline()
        self.must("pgbackrest restore", self.pgbackrest(*args, "restore"), timeout=24 * 3600)

    def render_offline(self) -> None:
        """pgbackrest.conf from the defaults and the passphrase file, without Django."""
        self.prepare_dirs()
        self.write_private(self.paths.pgbackrest_conf, self.render_pgbackrest(Config(enabled=True)))


def summarize_restic(output: str) -> dict[str, Any]:
    """The summary message of `restic backup --json` (the last JSON line of that type)."""
    for line in reversed(output.strip().splitlines()):
        with contextlib.suppress(ValueError):
            message = json.loads(line)
            if isinstance(message, dict) and message.get("message_type") == "summary":
                return message
    return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def last_transaction(log: Path) -> str | None:
    """When the restored data ends: PostgreSQL logs it as recovery completes."""
    text = read_text(log)
    found = LAST_TRANSACTION.findall(text)
    return found[-1].strip() if found else None


def heartbeat_fresh(paths: Paths, max_age_s: float, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - paths.heartbeat.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_s


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup-agent", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="poll Django for configuration and jobs")
    health = commands.add_parser("health", help="exit 0 while the loop is alive")
    health.add_argument("--max-age", type=float, default=180.0)
    restore = commands.add_parser(
        "restore-db", help="restore the database in place (PostgreSQL stopped)"
    )
    restore.add_argument("--target", default="latest")
    restore.add_argument(
        "--yes", action="store_true", help="confirm: the data directory is overwritten"
    )
    commands.add_parser("render-config", help="write pgbackrest.conf without Django")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s backup-agent %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    env = Env.from_environ()
    if args.command == "health":
        return 0 if heartbeat_fresh(env.paths, args.max_age) else 1
    agent = Agent(env)
    try:
        if args.command == "run":
            logger.info("backup agent %s started", VERSION)
            agent.loop()
        elif args.command == "restore-db":
            if not args.yes:
                parser.error("restore-db overwrites the database: add --yes")
            agent.restore_db(args.target)
            sys.stdout.write(
                "Restored. Start PostgreSQL: it replays the archived WAL, "
                "then accepts connections.\n"
            )
        elif args.command == "render-config":
            agent.render_offline()
            sys.stdout.write(f"Wrote {env.paths.pgbackrest_conf}\n")
    except AgentError as exc:
        sys.stderr.write(f"backup-agent: {agent.scrub(str(exc))}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
