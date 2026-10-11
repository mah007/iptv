"""Key rotation (apps/ops/keys.py, `manage.py key_rotation`; ADR-0020)."""

import io
import json
import sys
from datetime import timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.utils import timezone

from apps.accounts import crypto
from apps.audit.models import AuditLog
from apps.core.services import get_secret, set_setting
from apps.ops import keys
from apps.playback import tokens

pytestmark = pytest.mark.django_db

SESSION = "ab" * 16
TITLE = "01920000-0000-7000-8000-0000000000aa"


def mint(keyset: tokens.KeySet, exp: int) -> str:
    return tokens.sign(keyset, session=SESSION, title=TITLE, rendition="compat", exp=exp)


def verdict(keyset: tokens.KeySet, token: str, now: int) -> str:
    try:
        tokens.verify(keyset, token, now=now)
    except tokens.TokenError as error:
        return error.reason.value
    return "ok"


# --- Media token keys: no signed URL is ever refused ------------------------------------


def test_the_media_key_overlap() -> None:
    start = keys.dumps({"current": {"kid": "k1", "secret": "A" * 43}})
    original = json.loads(start)
    before = tokens.parse_keyset(original)
    now = 1_800_000_000
    old_url = mint(before, now + 7200)

    staged = keys.stage(original)
    assert staged["current"] == original["current"]
    assert staged["previous"]["kid"] == "k2"
    staged_set = tokens.parse_keyset(staged)
    # Django still signs with k1; the edges already accept k2.
    assert mint(staged_set, now + 60).startswith("k1.")
    assert verdict(staged_set, old_url, now) == "ok"

    promoted = keys.promote(staged)
    promoted_set = tokens.parse_keyset(promoted)
    new_url = mint(promoted_set, now + 7200)
    assert new_url.startswith("k2.")
    assert verdict(promoted_set, old_url, now) == "ok"  # still accepted
    assert verdict(staged_set, new_url, now) == "ok"  # an edge one step behind accepts it

    retired = keys.retire(promoted)
    retired_set = tokens.parse_keyset(retired)
    assert "previous" not in retired
    assert verdict(retired_set, new_url, now) == "ok"
    assert verdict(retired_set, old_url, now) == "unknown_kid"


def test_next_kid_and_refusals() -> None:
    assert keys.next_kid({"current": {"kid": "k9"}, "previous": {"kid": "k12"}}) == "k13"
    assert keys.next_kid({"current": {"kid": "edge"}}) == "k1"
    with pytest.raises(ValueError, match="nothing staged"):
        keys.promote({"current": {"kid": "k1", "secret": "A" * 43}})
    with pytest.raises(tokens.KeySetError):
        keys.stage({"current": {"kid": "k1", "secret": "short"}})


def test_the_command_transforms_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"current": {"kid": "k1", "secret": "A" * 43}}))
    )
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    call_command("key_rotation", "media-keys", "stage")
    assert json.loads(out.getvalue())["previous"]["kid"] == "k2"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    with pytest.raises(CommandError, match="can't take this step"):
        call_command("key_rotation", "media-keys", "promote")


def test_the_previous_key_goes_after_the_longest_token_lifetime() -> None:
    assert keys.media_token_finish().ready
    entry = keys.record("media_token", "signing", kid="k2")
    lifetime = keys.longest_token_lifetime()
    assert lifetime == timedelta(hours=6)  # live tokens outlive VOD ones by default
    check = keys.media_token_finish(entry.at + lifetime)
    assert not check.ready
    assert check.not_before == entry.at + lifetime + keys.CLOCK_SKEW
    assert keys.media_token_finish(entry.at + lifetime + timedelta(minutes=1)).ready


def test_rotations_are_audited_for_the_system_page(capsys: pytest.CaptureFixture[str]) -> None:
    call_command("key_rotation", "record", "db", "done", "--detail", "role=iptv_app")
    entry = AuditLog.objects.get(action="keys.rotate")
    assert (entry.target_type, entry.target_id) == ("keys", "db")
    assert entry.after == {"phase": "done", "role": "iptv_app"}
    assert entry.actor is None
    with pytest.raises(ValueError, match="unknown key kind"):
        keys.record("nope", "x")
    keys.record("media_token", "signing")
    with pytest.raises(SystemExit) as stopped:
        call_command("key_rotation", "media-token-finish")
    assert stopped.value.code == 3
    assert keys.last("db") == entry
    assert keys.last("db", "start") is None


# --- The field-encryption key -------------------------------------------------------------


def test_every_encrypted_field_is_rotated() -> None:
    """A new *_encrypted or ciphertext field must join ENCRYPTED_FIELDS (or the settings')."""
    listed = {(label.lower(), field) for label, field in keys.ENCRYPTED_FIELDS}
    found = set()
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, models.TextField | models.CharField) and (
                field.name.endswith("_encrypted") or field.name == "ciphertext"
            ):
                found.add((model._meta.label_lower, field.name))
    assert found - {("core.setting", "ciphertext")} == listed


def test_reencrypting_moves_every_secret_to_the_new_key(settings: Any, owner: Any) -> None:
    old = settings.FIELD_ENCRYPTION_KEY.split(",")[0]
    from apps.accounts.models import MfaTotp  # noqa: PLC0415

    totp = MfaTotp.objects.create(user=owner, secret_encrypted=crypto.encrypt("JBSWY3DPEHPK3PXP"))
    set_setting("backups.s3_secret_key", "very-secret-value", actor=None)
    new = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY = f"{new},{old}"
    assert keys.undecryptable_with_first_key()  # still under the old key
    out = io.StringIO()
    call_command("key_rotation", "reencrypt", stdout=out)
    assert "accounts.MfaTotp.secret_encrypted: 1" in out.getvalue()
    assert keys.undecryptable_with_first_key() == []
    settings.FIELD_ENCRYPTION_KEY = new
    totp.refresh_from_db()
    assert crypto.decrypt(totp.secret_encrypted) == "JBSWY3DPEHPK3PXP"
    assert get_secret("backups.s3_secret_key") == "very-secret-value"
    call_command("key_rotation", "check-first-key", stdout=io.StringIO())


def test_check_first_key_fails_while_old_values_remain(settings: Any) -> None:
    set_setting("backups.s3_secret_key", "very-secret-value", actor=None)
    settings.FIELD_ENCRYPTION_KEY = (
        f"{Fernet.generate_key().decode()},{settings.FIELD_ENCRYPTION_KEY}"
    )
    with pytest.raises(CommandError, match="need an older key"):
        call_command("key_rotation", "check-first-key")


def test_now_is_aware() -> None:
    assert timezone.is_aware(timezone.now())
