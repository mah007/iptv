import time
from collections.abc import Iterator
from typing import cast
from unittest import mock

import pytest
from django.conf import settings

from apps.core import healthcheck
from apps.core.stores import state_redis
from apps.core.tasks import HEARTBEAT_KEY, heartbeat
from config.celery import app


@pytest.fixture
def clean_heartbeat() -> Iterator[None]:
    state_redis().delete(HEARTBEAT_KEY)
    yield
    state_redis().delete(HEARTBEAT_KEY)


def test_spec_queues_are_declared() -> None:
    names = {queue.name for queue in settings.CELERY_TASK_QUEUES}
    assert names == {
        "default",
        "scan",
        "metadata",
        "images",
        "notify",
        "transcode.cpu",
        "transcode.qsv",
        "transcode.vaapi",
        "transcode.nvenc",
    }
    assert settings.CELERY_TASK_DEFAULT_QUEUE == "default"


def test_beat_schedules_the_heartbeat() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["core-heartbeat"]
    assert entry["task"] == "apps.core.tasks.heartbeat"
    assert entry["task"] in app.tasks


def test_heartbeat_task_writes_a_fresh_timestamp(clean_heartbeat: None) -> None:
    heartbeat.apply()
    stored = cast("bytes | None", state_redis().get(HEARTBEAT_KEY))
    assert stored is not None
    assert abs(time.time() - int(stored)) < 5
    assert 0 < cast("int", state_redis().ttl(HEARTBEAT_KEY)) <= 300


def test_beat_healthcheck_tracks_heartbeat_age(clean_heartbeat: None) -> None:
    assert healthcheck.check_beat() is False
    heartbeat.apply()
    assert healthcheck.check_beat() is True
    state_redis().set(HEARTBEAT_KEY, int(time.time()) - healthcheck.BEAT_MAX_AGE_S - 5)
    assert healthcheck.check_beat() is False


def test_web_healthcheck_reports_connection_failures() -> None:
    with mock.patch("apps.core.healthcheck.urllib.request.urlopen", side_effect=OSError):
        assert healthcheck.check_web() is False


def test_web_healthcheck_accepts_200() -> None:
    response = mock.MagicMock()
    response.__enter__.return_value.status = 200
    with mock.patch("apps.core.healthcheck.urllib.request.urlopen", return_value=response):
        assert healthcheck.check_web() is True


@pytest.mark.parametrize(("argv", "code"), [([], 2), (["nope"], 2), (["web"], 1)])
def test_healthcheck_cli_exit_codes(argv: list[str], code: int) -> None:
    with mock.patch("apps.core.healthcheck.urllib.request.urlopen", side_effect=OSError):
        assert healthcheck.main(argv) == code
