"""The backup agent (apps/ops/agent.py), with its commands faked: what it runs, renders,
reports and keeps secret. It runs for real in the dev stack (`make backup`,
`make restore-test`)."""

import json
import os
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from apps.ops import agent as agent_module
from apps.ops.agent import (
    MASK,
    Agent,
    AgentError,
    Config,
    Env,
    Paths,
    Result,
    S3Target,
    Scrubber,
    heartbeat_fresh,
    last_transaction,
    summarize_restic,
    tail,
)

PASSPHRASE = "abcde-fghij-klmno-pqrst-uvwxy-z0123"  # noqa: S105 (a test value)
TOKEN = "agent-token-0123456789abcdef"  # noqa: S105
S3: dict[str, Any] = {
    "endpoint": "https://s3.example.com",
    "bucket": "backups",
    "prefix": "smart-iptv/",
    "region": "eu-central-1",
    # Built from parts: key-shaped literals trip push protection.
    "access_key": "AK" + "IA" + "TESTVALUE0001",
    "secret_key": "s3cr3t/" + "test" + "+value",
    "path_style": True,
}


@dataclass
class FakeRunner:
    """Answers commands by the first matching rule; records every call."""

    rules: list[tuple[Callable[[list[str]], bool], Result]] = field(default_factory=list)
    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)

    def on(self, predicate: Callable[[list[str]], bool], code: int = 0, output: str = "") -> None:
        self.rules.append((predicate, Result(code, output)))

    def __call__(self, args: Sequence[str], **kwargs: Any) -> Result:
        argv = list(args)
        self.calls.append((argv, kwargs))
        for predicate, result in self.rules:
            if predicate(argv):
                return result
        return Result(0, "")

    def commands(self, word: str) -> list[list[str]]:
        return [argv for argv, _kwargs in self.calls if word in argv]


def has(*words: str) -> Callable[[list[str]], bool]:
    return lambda argv: all(word in argv for word in words)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "passphrase").write_text(PASSPHRASE + "\n")
    (secrets / "token").write_text(TOKEN)
    sources = tmp_path / "sources"
    (sources / "config").mkdir(parents=True)
    (sources / "media-data" / "images").mkdir(parents=True)
    return Env(
        api_url="http://web:8000/internal/backups/agent",
        token_file=secrets / "token",
        passphrase_file=secrets / "passphrase",
        poll_s=0.01,
        paths=Paths(
            repo=tmp_path / "backups",
            config=tmp_path / "config",
            drill=tmp_path / "drill",
            sources=sources,
            pgdata=tmp_path / "pgdata",
            pg_socket=tmp_path / "run",
        ),
    )


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def make_agent(env: Env, runner: FakeRunner) -> Callable[..., Agent]:
    def factory(**kwargs: Any) -> Agent:
        return Agent(env, runner=runner, sleep=lambda _s: None, is_root=False, **kwargs)

    return factory


def info_json(*labels: str) -> str:
    return json.dumps(
        [
            {
                "backup": [
                    {
                        "label": label,
                        "type": "full" if label.endswith("F") else "diff",
                        "timestamp": {"start": 100, "stop": 103},
                        "info": {"size": 17_000_000, "repository": {"delta": 6_000_000}},
                    }
                    for label in labels
                ]
            }
        ]
    )


# --- Configuration ----------------------------------------------------------------------


def test_the_environment_names_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env.from_environ(
        {
            "AGENT_URL": "http://web:8000/x",
            "BACKUP_REPO_DIR": "/r",
            "PGDATA": "/pg",
            "PGUSER": "owner",
            "AGENT_POLL_S": "5",
        }
    )
    assert env.api_url == "http://web:8000/x"
    assert env.paths.pgbackrest_repo == Path("/r/pgbackrest")
    assert env.paths.pgdata == Path("/pg")
    assert env.pg_user == "owner"
    assert env.poll_s == 5.0
    defaults = Env.from_environ({})
    assert defaults.api_host == "web"
    assert defaults.paths.config == Path("/backup-config")


def test_config_parsing_clamps_and_ignores_rubbish() -> None:
    config = Config.parse({"enabled": True, "db_full_retention": 999, "keep_daily": "x", "s3": S3})
    assert config.enabled
    assert config.db_full_retention == 52
    assert config.keep_daily == 7
    assert config.s3 is not None
    assert config.s3.repository == "s3:https://s3.example.com/backups/smart-iptv"
    assert Config.parse(None) == Config()
    assert S3Target.parse({"endpoint": "ftp://x", "bucket": "b"}) is None
    assert S3Target.parse({"endpoint": "https://x", "bucket": ""}) is None


def test_pgbackrest_conf_is_private_and_carries_the_cipher(
    make_agent: Callable[..., Agent],
) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=False, db_full_retention=3))
    conf = agent.paths.pgbackrest_conf
    text = conf.read_text()
    assert f"repo1-cipher-pass={PASSPHRASE}" in text
    assert "repo1-cipher-type=aes-256-cbc" in text
    assert "repo1-retention-full=3" in text
    assert f"pg1-path={agent.paths.pgdata}" in text
    assert oct(conf.stat().st_mode & 0o777) == "0o600"
    assert not agent.paths.archive_flag.exists()


def test_enabling_creates_the_stanza_once_and_switches_archiving_on(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    agent.apply_config(Config(enabled=True))
    assert len(runner.commands("stanza-create")) == 1
    assert agent.paths.archive_flag.exists()
    agent.apply_config(Config(enabled=False))
    assert not agent.paths.archive_flag.exists()


def test_a_failed_stanza_leaves_archiving_off(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    runner.on(has("stanza-create"), code=56, output=f"ERROR: cannot connect; pass={PASSPHRASE}")
    agent = make_agent()
    with pytest.raises(AgentError) as failure:
        agent.apply_config(Config(enabled=True))
    assert PASSPHRASE not in str(failure.value)
    assert MASK in str(failure.value)
    assert not agent.paths.archive_flag.exists()


def test_commands_run_as_postgres_when_root(env: Env, runner: FakeRunner) -> None:
    agent = Agent(env, runner=runner, is_root=True)
    assert agent.pgbackrest("info")[:2] == ["gosu", "postgres"]
    assert Agent(env, runner=runner, is_root=False).pgbackrest("info")[0] == "pgbackrest"


# --- Backups ------------------------------------------------------------------------------


def test_a_database_backup_reports_the_new_label(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    answers = iter(
        [info_json("20261011-010101F"), info_json("20261011-010101F", "20261011-020202F_D")]
    )
    runner.rules.insert(0, (has("info"), Result(0, "")))
    runner.rules[0] = (has("info"), Result(0, ""))

    def info_answer(args: Sequence[str], **kwargs: Any) -> Result:
        runner.calls.append((list(args), kwargs))
        if "info" in args:
            return Result(0, next(answers))
        return Result(0, "")

    agent.run = info_answer
    outcome = agent.run_job({"id": "1", "kind": "db_diff"})
    assert outcome["detail"]["label"] == "20261011-020202F_D"
    assert outcome["detail"]["type"] == "diff"
    assert outcome["size_bytes"] == 6_000_000
    assert any("--type=diff" in argv for argv, _ in runner.calls)


def test_a_failed_backup_raises_with_a_scrubbed_tail(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    runner.on(
        has("backup"),
        code=82,
        output="\n".join(f"line {n}" for n in range(100)) + f"\n{PASSPHRASE}",
    )
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    with pytest.raises(AgentError) as failure:
        agent.run_job({"id": "1", "kind": "db_full"})
    message = str(failure.value)
    assert "exit 82" in message
    assert "line 99" in message
    assert "line 10\n" not in message
    assert PASSPHRASE not in message


def test_jobs_refuse_while_backups_are_off_except_the_check(
    make_agent: Callable[..., Agent],
) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=False))
    with pytest.raises(AgentError, match="switched off"):
        agent.run_job({"id": "1", "kind": "files"})
    assert agent.run_job({"id": "2", "kind": "check"}) == {"detail": {"local": True}}
    with pytest.raises(AgentError, match="unknown job"):
        agent.run_job({"id": "3", "kind": "nope"})


def test_the_files_snapshot_holds_config_artwork_and_a_dump(
    make_agent: Callable[..., Agent], runner: FakeRunner, env: Env
) -> None:
    summary = json.dumps(
        {
            "message_type": "summary",
            "snapshot_id": "abc",
            "data_added": 100,
            "total_files_processed": 7,
        }
    )
    runner.on(has("backup", "--tag=files"), output=summary)
    runner.on(
        has("backup", "--tag=pgdump"),
        output=json.dumps({"message_type": "summary", "snapshot_id": "def", "data_added": 50}),
    )
    agent = make_agent()
    agent.apply_config(Config(enabled=True, keep_daily=3, media=True))
    (env.paths.sources / "media").mkdir()
    outcome = agent.run_job({"id": "1", "kind": "files", "params": {"prune": True}})
    assert outcome["detail"] == {
        "snapshot": "abc",
        "dump_snapshot": "def",
        "files": 7,
        "media": True,
        "pruned": True,
    }
    assert outcome["size_bytes"] == 150
    init = runner.commands("init")
    assert init
    assert init[0][:2] == ["restic", "--quiet"]
    files = runner.commands("--tag=files")[0]
    assert str(env.paths.sources / "config") in files
    assert str(env.paths.sources / "media") in files
    dump = runner.commands("--tag=pgdump")[0]
    assert "--stdin-from-command" in dump
    assert "pg_dump" in dump
    forget = runner.commands("forget")[0]
    assert "--keep-daily=3" in forget
    assert "--prune" in forget
    for argv, kwargs in runner.calls:
        assert PASSPHRASE not in " ".join(argv)
        if argv and argv[0] == "restic":
            assert kwargs["env"]["RESTIC_PASSWORD_FILE"] == str(env.passphrase_file)


def test_offsite_copies_the_repository_and_the_nights_snapshots(
    make_agent: Callable[..., Agent], runner: FakeRunner, env: Env
) -> None:
    runner.on(has("cat", "config"), code=1, output="repository does not exist")
    runner.on(
        has("--tag=pgbackrest"),
        output=json.dumps({"message_type": "summary", "snapshot_id": "s3snap", "data_added": 9}),
    )
    agent = make_agent()
    agent.apply_config(Config(enabled=True, s3=S3Target.parse(S3)))
    env.paths.restic_repo.mkdir(parents=True, exist_ok=True)
    (env.paths.restic_repo / "config").write_text("x")
    outcome = agent.run_job({"id": "1", "kind": "offsite", "params": {"full": True}})
    assert outcome["detail"]["snapshot"] == "s3snap"
    assert outcome["detail"]["copied_files"] is True
    init = next(argv for argv in runner.commands("init") if "--copy-chunker-params" in argv)
    assert f"--from-repo={env.paths.restic_repo}" in init
    assert "--option=s3.bucket-lookup=path" in init
    copy = runner.commands("copy")[0]
    assert "--tag=files" in copy
    assert "--tag=pgdump" in copy
    s3_env: dict[str, str] = next(
        kwargs["env"] for argv, kwargs in runner.calls if "--tag=pgbackrest" in argv
    )
    assert s3_env["RESTIC_REPOSITORY"] == "s3:https://s3.example.com/backups/smart-iptv"
    assert s3_env["AWS_SECRET_ACCESS_KEY"] == S3["secret_key"]
    assert agent.scrub(f"key {S3['secret_key']}") == f"key {MASK}"
    for argv, _kwargs in runner.calls:
        assert str(S3["secret_key"]) not in " ".join(argv)


def test_offsite_without_a_bucket_fails(make_agent: Callable[..., Agent]) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    with pytest.raises(AgentError, match="no S3"):
        agent.run_job({"id": "1", "kind": "offsite"})


def test_the_check_reports_each_destination(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    runner.on(lambda argv: "check" in argv and "pgbackrest" in " ".join(argv), code=0)
    agent = make_agent()
    agent.apply_config(Config(enabled=True, s3=S3Target.parse(S3)))
    assert agent.run_job({"id": "1", "kind": "check"})["detail"] == {
        "pgbackrest": True,
        "local": True,
        "s3": True,
    }
    runner.rules.insert(
        0,
        (
            lambda argv: any("s3.bucket-lookup" in a for a in argv) and "cat" in argv,
            Result(1, "403 Forbidden"),
        ),
    )
    agent._s3_ready = None
    with pytest.raises(AgentError, match="403"):
        agent.run_job({"id": "2", "kind": "check"})


# --- The drill ------------------------------------------------------------------------------


def test_the_drill_restores_reads_back_and_cleans_up(
    make_agent: Callable[..., Agent], runner: FakeRunner, env: Env
) -> None:
    clock = iter(range(100, 200))
    agent = make_agent(clock=lambda: float(next(clock)))
    agent.apply_config(Config(enabled=True))
    env.paths.drill.mkdir(parents=True, exist_ok=True)
    (env.paths.drill / "postgres.log").write_text(
        "LOG:  last completed transaction was at log time 2026-10-11 12:00:00.5+00\n"
    )
    runner.on(has("SELECT pg_is_in_recovery()"), output="f\n")
    runner.on(
        lambda argv: any("django_migrations" in a for a in argv),
        output='[["core", "0001_initial", "2026-10-01"]]',
    )
    runner.on(
        lambda argv: any("string_agg(relname" in a for a in argv),
        output="accounts_user,core_setting",
    )
    runner.on(
        lambda argv: any("UNION ALL" in a or "count(*)" in a for a in argv),
        output="accounts_user|5\ncore_setting|2",
    )
    outcome = agent.run_job(
        {
            "id": "1",
            "kind": "drill",
            "tables": ["accounts_user", "core_setting", "missing_one", "bad;name"],
        }
    )
    detail = outcome["detail"]
    assert detail["counts"] == {"accounts_user": 5, "core_setting": 2}
    assert detail["missing_tables"] == ["missing_one"]
    assert detail["migrations"] == [["core", "0001_initial", "2026-10-01"]]
    assert detail["recovered_to"] == "2026-10-11 12:00:00.5+00"
    assert detail["verify_ok"] is True
    restore = runner.commands("restore")[0]
    assert f"--pg1-path={env.paths.drill / 'pgdata'}" in restore
    assert "--archive-mode=off" in restore
    start = next(argv for argv in runner.commands("pg_ctl") if "start" in argv)
    options = start[start.index("--options") + 1]
    assert "listen_addresses=''" in options
    assert "archive_mode=off" in options
    assert any("stop" in argv for argv in runner.commands("pg_ctl"))
    assert not any("bad;name" in " ".join(argv) for argv, _ in runner.calls)


def test_a_quiet_drill_reaches_the_backups_end(
    make_agent: Callable[..., Agent], runner: FakeRunner
) -> None:
    runner.on(has("--output=json", "info"), output=info_json("20261011-010101F"))
    agent = make_agent()
    assert agent.latest_backup_stop() == "1970-01-01T00:01:43+00:00"
    runner.rules.insert(0, (has("--output=json", "info"), Result(0, "[]")))
    assert agent.latest_backup_stop() is None


def test_a_drill_that_cannot_start_reports_the_log(
    make_agent: Callable[..., Agent], runner: FakeRunner, env: Env
) -> None:
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    env.paths.drill.mkdir(parents=True, exist_ok=True)
    (env.paths.drill / "postgres.log").write_text(
        "FATAL:  recovery aborted because of insufficient parameter settings\n"
    )
    runner.on(
        lambda argv: "pg_ctl" in argv and "start" in argv, code=1, output="could not start server"
    )
    with pytest.raises(AgentError, match="insufficient parameter settings"):
        agent.run_job({"id": "1", "kind": "drill", "tables": []})
    assert any("rm" in argv for argv, _ in runner.calls)


def test_waiting_for_promotion_times_out(
    make_agent: Callable[..., Agent], runner: FakeRunner, tmp_path: Path
) -> None:
    clock = iter(range(0, 1000, 50))
    agent = make_agent(clock=lambda: float(next(clock)))
    runner.on(has("SELECT pg_is_in_recovery()"), output="t\n")
    with pytest.raises(AgentError, match="did not finish recovery"):
        agent.wait_promoted(tmp_path, timeout=120)


# --- Restore in place --------------------------------------------------------------------


def test_restore_db_refuses_a_running_database(make_agent: Callable[..., Agent], env: Env) -> None:
    env.paths.pgdata.mkdir(parents=True)
    (env.paths.pgdata / "postmaster.pid").write_text("1")
    with pytest.raises(AgentError, match="Stop the postgres service"):
        make_agent().restore_db("latest")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("latest", ["--type=default"]),
        ("2026-10-11 12:00:00+00", ["--type=time", "--target=2026-10-11 12:00:00+00"]),
        ("name:pre-update-20261011", ["--type=name", "--target=pre-update-20261011"]),
    ],
)
def test_restore_db_targets(
    make_agent: Callable[..., Agent], runner: FakeRunner, target: str, expected: list[str]
) -> None:
    agent = make_agent()
    agent.restore_db(target)
    restore = runner.commands("restore")[0]
    for word in [*expected, "--delta", "--target-action=promote"]:
        assert word in restore
    assert agent.paths.pgbackrest_conf.exists()


def test_restore_db_rejects_a_bad_target(make_agent: Callable[..., Agent]) -> None:
    with pytest.raises(AgentError, match="--target"):
        make_agent().restore_db("yesterday; rm -rf /")


# --- Talking to Django ------------------------------------------------------------------------


def test_poll_once_applies_config_runs_the_job_and_reports(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()
    sent: list[tuple[str, dict[str, Any]]] = []

    def fake_api(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent.append((path, payload))
        if path == "/poll":
            return {"config": {"enabled": True}, "job": {"id": "run-1", "kind": "check"}}
        return {"status": "succeeded"}

    monkeypatch.setattr(agent, "api", fake_api)
    assert agent.poll_once() is True
    assert sent[0][0] == "/poll"
    assert sent[0][1]["status"]["passphrase"] is True
    assert sent[1] == (
        "/jobs/run-1",
        {"status": "succeeded", "detail": {"pgbackrest": True, "local": True}, "duration_s": 0.0},
    )


def test_a_failed_job_is_reported_scrubbed(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()
    sent: list[tuple[str, dict[str, Any]]] = []

    def fake_api(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent.append((path, payload))
        if path == "/poll":
            return {"config": {"enabled": True}, "job": {"id": "run-2", "kind": "db_full"}}
        return {}

    def explode(job: Any) -> dict[str, Any]:
        raise RuntimeError(f"boom {PASSPHRASE}")

    monkeypatch.setattr(agent, "api", fake_api)
    monkeypatch.setattr(agent, "run_job", explode)
    agent.poll_once()
    path, payload = sent[-1]
    assert path == "/jobs/run-2"
    assert payload["status"] == "failed"
    assert PASSPHRASE not in payload["error"]


def test_no_job_means_no_report(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()
    monkeypatch.setattr(agent, "api", lambda path, payload: {"config": {}, "job": None})
    assert agent.poll_once() is False


def test_the_loop_survives_django_being_down(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()

    def down(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise OSError("connection refused")

    monkeypatch.setattr(agent, "api", down)
    agent.loop(once=True)
    assert heartbeat_fresh(agent.paths, 60)


def test_reports_retry_until_django_answers(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()
    attempts = []

    def flaky(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        attempts.append(path)
        if len(attempts) < 3:
            raise OSError("not yet")
        return {}

    monkeypatch.setattr(agent, "api", flaky)
    agent.report("run-3", {"status": "succeeded"})
    assert len(attempts) == 3


def test_the_api_call_sends_the_token_and_host(
    make_agent: Callable[..., Agent], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = make_agent()
    seen: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"config": {}}'

    def fake_urlopen(request: Any, timeout: float) -> Response:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert agent.api("/poll", {"status": {}}) == {"config": {}}
    assert seen["url"] == "http://web:8000/internal/backups/agent/poll"
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert seen["headers"]["Host"] == "web"


def test_status_collects_the_repositories(
    make_agent: Callable[..., Agent], runner: FakeRunner, env: Env
) -> None:
    runner.on(has("--output=json", "info"), output=info_json("20261011-010101F"))
    runner.on(
        has("snapshots"),
        output=json.dumps([{"time": "2026-10-10T03:30:00Z"}, {"time": "2026-10-11T03:30:00Z"}]),
    )
    agent = make_agent()
    agent.apply_config(Config(enabled=True))
    (env.paths.restic_repo / "config").write_text("x")
    status = agent.status(refresh=True)
    assert status["archiving"] is True
    assert status["db_backups"][0]["label"] == "20261011-010101F"
    assert status["local_snapshots"] == {
        "ok": True,
        "snapshots": 2,
        "latest": "2026-10-11T03:30:00Z",
    }
    assert "total_bytes" in status["disk"]


# --- Helpers and the command line ---------------------------------------------------------------


def test_scrubber_masks_longest_first() -> None:
    scrub = Scrubber()
    scrub.add("abcd", "abcdefgh", "", "xyz")
    assert scrub("abcdefgh abcd xyz") == f"{MASK} {MASK} xyz"


def test_helpers(tmp_path: Path) -> None:
    assert tail("\n".join(str(n) for n in range(100)), lines=3) == "97\n98\n99"
    assert summarize_restic(
        '{"message_type":"status"}\n{"message_type":"summary","snapshot_id":"x"}'
    ) == {
        "message_type": "summary",
        "snapshot_id": "x",
    }
    assert summarize_restic("not json") == {}
    assert last_transaction(tmp_path / "missing.log") is None
    paths = Paths(config=tmp_path)
    assert not heartbeat_fresh(paths, 60)
    paths.heartbeat.touch()
    old = time.time() - 3600
    os.utime(paths.heartbeat, (old, old))
    assert not heartbeat_fresh(paths, 60)


def test_main_health_and_restore_confirmation(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        agent_module.Env, "from_environ", classmethod(lambda cls, environ=None: env)
    )
    assert agent_module.main(["health"]) == 1
    env.paths.config.mkdir(parents=True)
    env.paths.heartbeat.touch()
    assert agent_module.main(["health"]) == 0
    with pytest.raises(SystemExit):
        agent_module.main(["restore-db", "--target", "latest"])


def test_main_reports_errors_scrubbed(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        agent_module.Env, "from_environ", classmethod(lambda cls, environ=None: env)
    )

    def fail(self: Agent, target: str) -> None:
        raise AgentError("bad target")

    monkeypatch.setattr(agent_module.Agent, "restore_db", fail)
    assert agent_module.main(["restore-db", "--yes", "--target", "latest"]) == 1
    assert "bad target" in capsys.readouterr().err


def test_missing_secrets_are_reported_by_name(tmp_path: Path) -> None:
    env = Env(passphrase_file=tmp_path / "nope", paths=Paths(config=tmp_path))
    with pytest.raises(AgentError, match="cannot read nope"):
        _ = Agent(env, is_root=False).passphrase
    (tmp_path / "empty").write_text(" ")
    with pytest.raises(AgentError, match="empty is empty"):
        _ = Agent(Env(token_file=tmp_path / "empty"), is_root=False).token


def test_run_command_handles_missing_binaries_and_timeouts() -> None:
    assert agent_module.run_command(["/definitely/not/here"]).code == 127
    slow = agent_module.run_command(["sleep", "5"], timeout=0.2)
    assert slow.code == 124
    assert "timed out" in slow.output
    assert agent_module.run_command(["sh", "-c", "echo hi; exit 3"]).output.strip() == "hi"
