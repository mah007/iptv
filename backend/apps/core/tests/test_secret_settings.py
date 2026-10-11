"""Secret settings and the server-config fallback (CFG, ADR-0019): encrypted at rest,
never cached or logged in plaintext, audited without values, precedence and sources,
moving `.env` values in, bulk changes, key rotation, and pickup without a restart."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection

from apps.accounts import crypto
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core import services
from apps.core.migration_helpers import decrypt_rows, encrypt_rows, rename_rows
from apps.core.models import Setting
from apps.core.services import (
    SETTINGS_VERSION_KEY,
    SensitiveSettingError,
    SettingSource,
    adopt_setting,
    get_secret,
    get_setting,
    reencrypt_secret_settings,
    reset_setting,
    set_setting,
    set_settings,
    setting_state,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

type CaptureOnCommit = Callable[..., AbstractContextManager[list[Callable[[], None]]]]

PASSWORD = "smtp-plain-Pa55word!"  # noqa: S105 (a fake secret)
OTHER = "smtp-other-Pa55word?"


def raw_row(key: str) -> tuple[Any, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT value::text, ciphertext FROM core_setting WHERE key = %s", [key])
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1]


# --- Encryption at rest ----------------------------------------------------------------


def test_a_secret_is_encrypted_at_rest(staff_user: User) -> None:
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    value, ciphertext = raw_row("email.smtp_password")
    assert value is None
    assert PASSWORD not in ciphertext
    assert crypto.decrypt(ciphertext) == PASSWORD
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core_setting WHERE row_to_json(core_setting)::text LIKE %s",
            [f"%{PASSWORD}%"],
        )
        assert cursor.fetchone() == (0,)
    assert get_secret("email.smtp_password") == PASSWORD


def test_the_settings_cache_holds_ciphertext_only(staff_user: User) -> None:
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    services.bump_settings_version()
    assert get_secret("email.smtp_password") == PASSWORD
    cached = cache.get(f"settings:values:v2:{cache.get(SETTINGS_VERSION_KEY)}")
    assert cached is not None
    assert "email.smtp_password" in cached
    assert PASSWORD not in repr(cached)


def test_an_explicitly_empty_secret_is_stored_without_a_token(staff_user: User) -> None:
    set_setting("email.smtp_password", "", actor=staff_user)
    assert raw_row("email.smtp_password") == (None, "")
    state = setting_state("email.smtp_password")
    assert (state.source, state.is_set) == (SettingSource.DATABASE, False)


def test_secrets_and_plain_settings_have_their_own_accessors() -> None:
    with pytest.raises(SensitiveSettingError):
        get_setting("email.smtp_password")
    with pytest.raises(TypeError):
        get_secret("email.smtp_host")


def test_an_undecryptable_secret_reads_as_empty(caplog: pytest.LogCaptureFixture) -> None:
    Setting.objects.create(key="email.smtp_password", value=None, ciphertext="not-a-token")
    services.bump_settings_version()
    assert get_secret("email.smtp_password") == ""
    state = setting_state("email.smtp_password")
    assert state.undecryptable is True
    assert state.is_set is False
    assert "cannot be decrypted" in caplog.text


def test_a_legacy_plaintext_secret_still_reads_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    Setting.objects.create(key="email.smtp_password", value="legacy-plain-1")
    services.bump_settings_version()
    assert get_secret("email.smtp_password") == "legacy-plain-1"
    assert "stored unencrypted" in caplog.text
    assert "legacy-plain-1" not in caplog.text


# --- Masking, audit and logs ---------------------------------------------------------------


def test_states_never_hold_a_secret(staff_user: User) -> None:
    state = set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    assert state.value == ""
    assert state.is_set is True
    assert setting_state("email.smtp_password").value == ""


def test_secret_changes_are_audited_without_values(staff_user: User) -> None:
    set_setting("email.smtp_password", PASSWORD, actor=staff_user, ip="203.0.113.9")
    set_setting("email.smtp_password", OTHER, actor=staff_user)
    reset_setting("email.smtp_password", actor=staff_user)
    entries = list(AuditLog.objects.order_by("at", "id"))
    assert [entry.action for entry in entries] == [
        "setting.update",
        "setting.update",
        "setting.reset",
    ]
    assert entries[0].before == {"configured": False, "source": "default"}
    assert entries[0].after == {"configured": True, "source": "database"}
    assert entries[2].after == {"configured": False, "source": "default"}
    dumped = repr([(entry.before, entry.after) for entry in entries])
    assert PASSWORD not in dumped
    assert OTHER not in dumped


def test_secrets_never_reach_the_logs(
    staff_user: User, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    services.bump_settings_version()
    get_secret("email.smtp_password")
    setting_state("email.smtp_password")
    reset_setting("email.smtp_password", actor=staff_user)
    captured = capsys.readouterr()
    assert PASSWORD not in caplog.text + captured.out + captured.err


def test_setting_the_same_secret_again_records_nothing(staff_user: User) -> None:
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    token = raw_row("email.smtp_password")[1]
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    assert AuditLog.objects.count() == 1
    assert raw_row("email.smtp_password")[1] == token


# --- Precedence: stored, else server config, else default ------------------------------------


def test_the_server_config_is_the_fallback(staff_user: User, settings: Any) -> None:
    assert setting_state("email.smtp_host").source is SettingSource.DEFAULT
    settings.EMAIL_HOST = "smtp.env.example.com"
    state = setting_state("email.smtp_host")
    assert (state.value, state.source, state.is_default) == (
        "smtp.env.example.com",
        SettingSource.ENVIRONMENT,
        True,
    )
    assert get_setting("email.smtp_host") == "smtp.env.example.com"
    set_setting("email.smtp_host", "smtp.admin.example.com", actor=staff_user)
    assert get_setting("email.smtp_host") == "smtp.admin.example.com"
    # An explicit empty value turns the .env value off.
    set_setting("email.smtp_host", "", actor=staff_user)
    assert get_setting("email.smtp_host") == ""
    reset_setting("email.smtp_host", actor=staff_user)
    assert setting_state("email.smtp_host").source is SettingSource.ENVIRONMENT


def test_a_secret_from_the_server_config_is_never_sent_either(settings: Any) -> None:
    settings.EMAIL_HOST_PASSWORD = PASSWORD
    state = setting_state("email.smtp_password")
    assert (state.value, state.source, state.is_set) == ("", SettingSource.ENVIRONMENT, True)
    assert get_secret("email.smtp_password") == PASSWORD


def test_environment_variables_count_when_django_settings_lack_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCODE_MAX_ATTEMPTS", "5")
    assert get_setting("library.transcode_max_attempts") == 5
    assert setting_state("library.transcode_max_attempts").source is SettingSource.ENVIRONMENT


def test_an_invalid_server_config_value_falls_back_to_the_default(
    settings: Any, caplog: pytest.LogCaptureFixture
) -> None:
    settings.EMAIL_PORT = 99999
    assert get_setting("email.smtp_port") == 587
    assert setting_state("email.smtp_port").source is SettingSource.DEFAULT
    assert "server config value of setting email.smtp_port is invalid" in caplog.text
    assert "99999" not in caplog.text


def test_derived_server_config(settings: Any) -> None:
    settings.EMAIL_USE_TLS = False
    assert get_setting("email.smtp_security") == "none"
    settings.EMAIL_USE_SSL = True
    assert get_setting("email.smtp_security") == "ssl"


def test_the_settings_from_server_config_are_listed(settings: Any) -> None:
    settings.TMDB_READ_ACCESS_TOKEN = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20
    keys = [state.definition.key for state in services.from_server_config()]
    assert keys == ["metadata.tmdb_read_access_token"]


# --- Moving .env values in, resets and bulk changes -------------------------------------------


def test_a_server_config_secret_is_moved_into_the_settings(staff_user: User, settings: Any) -> None:
    settings.EMAIL_HOST_PASSWORD = PASSWORD
    state = adopt_setting("email.smtp_password", actor=staff_user, ip="198.51.100.1")
    assert (state.source, state.is_set) == (SettingSource.DATABASE, True)
    assert crypto.decrypt(raw_row("email.smtp_password")[1]) == PASSWORD
    settings.EMAIL_HOST_PASSWORD = None
    assert get_secret("email.smtp_password") == PASSWORD
    entry = AuditLog.objects.get()
    assert entry.action == "setting.adopt"
    assert entry.before == {"configured": True, "source": "environment"}
    assert PASSWORD not in repr(entry.after)


def test_nothing_to_move_is_an_error(staff_user: User) -> None:
    with pytest.raises(ValidationError, match="holds no value"):
        adopt_setting("email.smtp_password", actor=staff_user)
    assert not Setting.objects.exists()


def test_resetting_an_unset_setting_records_nothing(staff_user: User) -> None:
    reset_setting("email.smtp_host", actor=staff_user)
    assert not AuditLog.objects.exists()


def test_bulk_changes_are_all_or_nothing(staff_user: User) -> None:
    with pytest.raises(services.SettingsValidationError) as caught:
        set_settings(
            {"email.smtp_host": "smtp.example.com", "email.smtp_port": 0}, actor=staff_user
        )
    assert caught.value.errors == {"email.smtp_port": ["Must be at least 1."]}
    assert not Setting.objects.exists()

    states = set_settings(
        {
            "email.smtp_host": "smtp.example.com",
            "email.smtp_port": 2525,
            "email.smtp_password": PASSWORD,
        },
        actor=staff_user,
    )
    assert [state.definition.key for state in states] == [
        "email.smtp_host",
        "email.smtp_port",
        "email.smtp_password",
    ]
    assert get_setting("email.smtp_port") == 2525
    set_settings({"email.smtp_port": None, "email.smtp_password": None}, actor=staff_user)
    assert get_setting("email.smtp_port") == 587
    assert get_secret("email.smtp_password") == ""


# --- Key rotation and migrations --------------------------------------------------------------


def test_secrets_are_re_encrypted_with_a_new_key(staff_user: User, settings: Any) -> None:
    set_setting("email.smtp_password", PASSWORD, actor=staff_user)
    set_setting("email.smtp_host", "smtp.example.com", actor=staff_user)
    old_token = raw_row("email.smtp_password")[1]
    new_key = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY = f"{new_key},{settings.FIELD_ENCRYPTION_KEY}"
    assert reencrypt_secret_settings() == 1
    token = raw_row("email.smtp_password")[1]
    assert token != old_token
    assert Fernet(new_key.encode()).decrypt(token.encode()).decode() == PASSWORD


def test_migration_helpers_encrypt_decrypt_and_rename() -> None:
    Setting.objects.create(key="monitoring.telegram_bot_token", value="123:plain")
    Setting.objects.create(key="monitoring.telegram_chat_id", value="-100")
    rename_rows(
        Setting,
        {
            "monitoring.telegram_bot_token": "notifications.telegram_bot_token",
            "monitoring.telegram_chat_id": "notifications.telegram_chat_id",
        },
    )
    assert encrypt_rows(Setting, ["notifications.telegram_bot_token"]) == 1
    assert encrypt_rows(Setting, ["notifications.telegram_bot_token"]) == 0  # idempotent
    row = Setting.objects.get(key="notifications.telegram_bot_token")
    assert row.value is None
    assert crypto.decrypt(row.ciphertext) == "123:plain"
    assert Setting.objects.get(key="notifications.telegram_chat_id").value == "-100"
    assert decrypt_rows(Setting, ["notifications.telegram_bot_token"]) == 1
    assert Setting.objects.get(key="notifications.telegram_bot_token").value == "123:plain"
    # A row already under the new key wins over the old one.
    Setting.objects.create(key="monitoring.telegram_chat_id", value="-200")
    rename_rows(Setting, {"monitoring.telegram_chat_id": "notifications.telegram_chat_id"})
    assert not Setting.objects.filter(key="monitoring.telegram_chat_id").exists()
    assert Setting.objects.get(key="notifications.telegram_chat_id").value == "-100"


# --- Pickup without a restart ---------------------------------------------------------------


def test_another_process_s_secret_change_applies_without_a_restart(staff_user: User) -> None:
    assert get_secret("email.smtp_password") == ""  # this process memoises the version
    # Another process stores a secret and bumps the version after its commit.
    Setting.objects.create(
        key="email.smtp_password", value=None, ciphertext=crypto.encrypt(PASSWORD)
    )
    assert get_secret("email.smtp_password") == ""  # same version: the memo still answers
    services.bump_settings_version()
    assert get_secret("email.smtp_password") == PASSWORD


def test_the_writer_reads_its_own_change_before_the_commit(
    staff_user: User, django_capture_on_commit_callbacks: CaptureOnCommit
) -> None:
    assert get_setting("email.smtp_host") == ""
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        set_setting("email.smtp_host", "smtp.example.com", actor=staff_user)
        assert get_setting("email.smtp_host") == "smtp.example.com"
    assert services.bump_settings_version in callbacks


# --- The installer's command -----------------------------------------------------------------


def test_the_command_stores_a_secret_encrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PASSWORD", PASSWORD)
    call_command("set_setting", "email.smtp_password", "--env", "SMTP_PASSWORD")
    value, ciphertext = raw_row("email.smtp_password")
    assert value is None
    assert crypto.decrypt(ciphertext) == PASSWORD
