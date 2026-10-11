"""The transcoder's retry and timeout knobs are admin settings read per job (ADR-0019),
with their old environment variables as the fallback."""

from typing import Any

import pytest

from apps.core.services import set_setting
from apps.media import conf

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]


def test_defaults_then_the_server_config_then_the_admin(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    assert (conf.max_attempts(), conf.retry_backoff_s(), conf.job_timeout_s()) == (
        3,
        60,
        12 * 3600,
    )
    monkeypatch.setenv("TRANSCODE_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("TRANSCODE_JOB_TIMEOUT_S", "7200")
    assert (conf.max_attempts(), conf.job_timeout_s()) == (4, 2 * 3600)
    set_setting("library.transcode_max_attempts", 6, actor=None)
    set_setting("library.transcode_retry_backoff_s", 0, actor=None)
    set_setting("library.transcode_job_timeout_h", 24, actor=None)
    assert (conf.max_attempts(), conf.retry_backoff_s(), conf.job_timeout_s()) == (
        6,
        0,
        24 * 3600,
    )
