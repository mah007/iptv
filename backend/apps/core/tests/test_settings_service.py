"""Settings take effect without a restart, are validated, and every change is audited."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from types import MappingProxyType
from typing import Any
from unittest import mock

import pytest
import redis
from django.core.cache import cache
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core import registry, services
from apps.core.models import Setting
from apps.core.registry import SettingDef, SettingKind, UnknownSettingError
from apps.core.services import (
    SETTINGS_VERSION_KEY,
    get_setting,
    is_enabled,
    list_settings,
    set_setting,
    setting_state,
)

pytestmark = pytest.mark.django_db

type CaptureOnCommit = Callable[..., AbstractContextManager[list[Callable[[], None]]]]

SENSITIVE_KEY = "metadata.tmdb_api_key"


@pytest.fixture
def with_sensitive_setting(monkeypatch: pytest.MonkeyPatch) -> SettingDef:
    definition = SettingDef(
        SENSITIVE_KEY, SettingKind.STR, "", "TMDB API key.", "metadata", sensitive=True
    )
    monkeypatch.setattr(
        registry, "REGISTRY", MappingProxyType({**registry.REGISTRY, SENSITIVE_KEY: definition})
    )
    return definition


def test_defaults_apply_until_an_admin_changes_them() -> None:
    assert get_setting("playback.token_ttl_vod_s") == 7200
    assert get_setting("branding.service_name_ar") == "سمارت IPTV"
    with pytest.raises(UnknownSettingError):
        get_setting("playback.nope")


def test_a_change_is_stored_audited_and_visible_after_commit(
    staff_user: User, django_capture_on_commit_callbacks: CaptureOnCommit
) -> None:
    assert get_setting("playback.token_ttl_vod_s") == 7200
    with django_capture_on_commit_callbacks(execute=True):
        state = set_setting("playback.token_ttl_vod_s", 3600, actor=staff_user, ip="203.0.113.7")

    assert state.value == 3600
    assert state.is_default is False
    assert state.updated_by == "staff"
    assert get_setting("playback.token_ttl_vod_s") == 3600
    row = Setting.objects.get(key="playback.token_ttl_vod_s")
    assert row.value == 3600
    assert row.updated_by == staff_user
    entry = AuditLog.objects.get()
    assert entry.action == "setting.update"
    assert entry.actor == staff_user
    assert entry.actor_ip == "203.0.113.7"
    assert (entry.target_type, entry.target_id) == ("core.setting", "playback.token_ttl_vod_s")
    assert entry.before == {"value": 7200}
    assert entry.after == {"value": 3600}


def test_other_processes_pick_up_changes_without_a_restart(staff_user: User) -> None:
    # This process has read (and memoised) the current version.
    assert get_setting("features.include_vod_in_m3u") is True
    # Another process stores a change and bumps the version after its commit.
    Setting.objects.create(key="features.include_vod_in_m3u", value=False, updated_by=staff_user)
    services.bump_settings_version()
    assert get_setting("features.include_vod_in_m3u") is False


def test_the_version_is_bumped_only_after_the_commit(
    staff_user: User, django_capture_on_commit_callbacks: CaptureOnCommit
) -> None:
    get_setting("billing.grace_days")
    version = cache.get(SETTINGS_VERSION_KEY)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        set_setting("billing.grace_days", 5, actor=staff_user)
    # Not committed yet: readers keep the old version, so nobody caches the new row early.
    assert cache.get(SETTINGS_VERSION_KEY) == version
    assert callbacks == [services.bump_settings_version]


def test_setting_the_same_value_records_nothing(
    staff_user: User, django_capture_on_commit_callbacks: CaptureOnCommit
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        set_setting("billing.grace_days", 5, actor=staff_user)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        state = set_setting("billing.grace_days", 5, actor=staff_user)
    assert state.value == 5
    assert callbacks == []
    assert AuditLog.objects.count() == 1


def test_invalid_values_are_rejected_before_anything_is_stored(staff_user: User) -> None:
    with pytest.raises(ValidationError):
        set_setting("xtream.port", "eighty", actor=staff_user)
    with pytest.raises(ValidationError):
        set_setting("billing.vat_rate", 2, actor=staff_user)
    with pytest.raises(UnknownSettingError):
        set_setting("billing.nope", 1, actor=staff_user)
    assert not Setting.objects.exists()
    assert not AuditLog.objects.exists()


def test_sensitive_values_are_never_audited(
    staff_user: User, with_sensitive_setting: SettingDef
) -> None:
    set_setting(SENSITIVE_KEY, "tmdb-secret-123", actor=staff_user)
    set_setting(SENSITIVE_KEY, "tmdb-secret-456", actor=staff_user)
    first, second = AuditLog.objects.order_by("at", "id")
    assert first.before == {"value": "***"}
    assert first.after == {"value": "***"}
    assert second.before == {"value": "***"}
    assert get_setting(SENSITIVE_KEY) == "tmdb-secret-456"


def test_a_stored_value_that_no_longer_validates_falls_back_to_the_default() -> None:
    Setting.objects.create(key="xtream.port", value="not a port")
    services.bump_settings_version()
    assert get_setting("xtream.port") == 80
    assert setting_state("xtream.port").is_default is True


def test_reads_fall_back_to_the_database_when_the_cache_is_down(staff_user: User) -> None:
    Setting.objects.create(key="billing.grace_days", value=9, updated_by=staff_user)
    with mock.patch.object(cache, "get", side_effect=redis.ConnectionError):
        assert get_setting("billing.grace_days") == 9


def test_a_failed_bump_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    with mock.patch.object(cache, "set", side_effect=redis.ConnectionError):
        services.bump_settings_version()
    assert "could not bump the settings version" in caplog.text


def test_values_are_cached_per_version() -> None:
    get_setting("billing.grace_days")
    version = cache.get(SETTINGS_VERSION_KEY)
    assert cache.get(f"settings:values:{version}") == {}
    # The memo answers without touching the values key again.
    with mock.patch.object(cache, "get", wraps=cache.get) as spy:
        get_setting("billing.grace_days")
    assert [call.args[0] for call in spy.call_args_list] == [SETTINGS_VERSION_KEY]


def test_feature_flags() -> None:
    assert is_enabled("features.include_vod_in_m3u") is True
    assert is_enabled("features.subtitle_download") is False
    with pytest.raises(TypeError, match="not a boolean"):
        is_enabled("billing.grace_days")


def test_list_settings_reads_everything_in_one_query(
    staff_user: User, django_assert_num_queries: Any
) -> None:
    set_setting("billing.grace_days", 5, actor=staff_user)
    set_setting("branding.accent_color", "#112233", actor=staff_user)
    with django_assert_num_queries(1):
        states = list_settings()
    assert [state.definition.key for state in states] == list(registry.REGISTRY)
    by_key = {state.definition.key: state for state in states}
    assert by_key["billing.grace_days"].value == 5
    assert by_key["billing.grace_days"].updated_by == "staff"
    assert by_key["billing.vat_rate"].is_default is True
    assert by_key["billing.vat_rate"].updated_at is None


def test_system_changes_have_no_actor(django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        state = set_setting("billing.grace_days", 4, actor=None)
    assert state.updated_by is None
    assert AuditLog.objects.get().actor is None


def test_reads_use_the_database_when_the_cache_keeps_no_version(staff_user: User) -> None:
    Setting.objects.create(key="billing.grace_days", value=6, updated_by=staff_user)
    with (
        mock.patch.object(cache, "get", return_value=None),
        mock.patch.object(cache, "add"),
    ):
        assert get_setting("billing.grace_days") == 6


def test_a_setting_reads_as_its_key() -> None:
    assert str(Setting(key="billing.grace_days", value=3)) == "billing.grace_days"
