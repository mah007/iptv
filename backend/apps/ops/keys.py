"""Key rotation helpers (SPEC §16 M14, ADR-0020): what `scripts/rotate-keys.sh` asks
Django to do. The script owns the files (.env, secrets/); Django re-encrypts what is
stored, records each rotation in the audit log (`keys.rotate`, target `keys/<kind>`,
which the admin's System page lists), and says when an overlap may end.
"""

import base64
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from apps.accounts import crypto
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.audit.services import AuditTarget
from apps.core.services import get_setting

KINDS: Final = ("media_token", "secret_key", "field_encryption", "db", "valkey")
AUDIT_ACTION: Final = "keys.rotate"
AUDIT_TARGET: Final = "keys"
#: Every model field holding Fernet ciphertext (apps.accounts.crypto), besides the
#: settings' own (`apps.core.services.reencrypt_secret_settings`). A test checks that
#: no encrypted field is missing.
ENCRYPTED_FIELDS: Final = (
    ("accounts.MfaTotp", "secret_encrypted"),
    ("live.LiveChannel", "source_encrypted"),
    ("live.LiveIntegration", "credentials_encrypted"),
    ("live.EpgSource", "url_encrypted"),
    # Imports (ADR-0025): source credentials and direct links.
    ("imports.ImportSource", "credentials_encrypted"),
    ("imports.ImportJob", "url_encrypted"),
)
#: Signed media URLs accept this much clock difference past their expiry (tokens.py).
CLOCK_SKEW: Final = timedelta(seconds=30)


def record(kind: str, phase: str, **detail: str) -> AuditLog:
    if kind not in KINDS:
        msg = f"unknown key kind {kind!r}"
        raise ValueError(msg)
    return audit.record(
        AUDIT_ACTION,
        actor=None,
        target=AuditTarget(AUDIT_TARGET, kind),
        after={"phase": phase, **detail},
    )


def last(kind: str, phase: str | None = None) -> AuditLog | None:
    entries = AuditLog.objects.filter(action=AUDIT_ACTION, target_type=AUDIT_TARGET, target_id=kind)
    if phase is not None:
        entries = entries.filter(after__phase=phase)
    return entries.order_by("-at").first()


def longest_token_lifetime() -> timedelta:
    seconds = max(
        int(get_setting("playback.token_ttl_vod_s")), int(get_setting("playback.token_ttl_live_s"))
    )
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class FinishCheck:
    ready: bool
    not_before: datetime | None


def media_token_finish(now: datetime | None = None) -> FinishCheck:
    """The old media key may go once every URL signed with it has expired."""
    now = now or timezone.now()
    switched = last("media_token", "signing")
    if switched is None:
        return FinishCheck(ready=True, not_before=None)
    not_before = switched.at + longest_token_lifetime() + CLOCK_SKEW
    return FinishCheck(ready=now >= not_before, not_before=not_before)


@dataclass(frozen=True, slots=True)
class Reencrypted:
    fields: dict[str, int]
    settings: int

    @property
    def total(self) -> int:
        return sum(self.fields.values()) + self.settings


def reencrypt_all() -> Reencrypted:
    """Re-encrypt every stored secret with the first FIELD_ENCRYPTION_KEY (rotation:
    the new key first, this, then the old key dropped). One transaction."""
    from apps.core.services import reencrypt_secret_settings  # noqa: PLC0415

    counts: dict[str, int] = {}
    with transaction.atomic():
        for label, field in ENCRYPTED_FIELDS:
            model = apps.get_model(label)
            count = 0
            for row in (
                model._default_manager.select_for_update().exclude(**{field: ""}).only("pk", field)
            ):
                setattr(row, field, crypto.rotate(getattr(row, field)))
                row.save(update_fields=[field])
                count += 1
            counts[f"{label}.{field}"] = count
        settings_count = reencrypt_secret_settings()
    return Reencrypted(counts, settings_count)


def undecryptable_with_first_key() -> list[str]:
    """Stored secrets the first key alone cannot read (what dropping the others would break)."""
    from cryptography.fernet import Fernet, InvalidToken  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415

    from apps.core.models import Setting  # noqa: PLC0415

    first = Fernet(str(settings.FIELD_ENCRYPTION_KEY).split(",")[0].strip())
    broken: list[str] = []

    def check(where: str, token: str) -> None:
        try:
            first.decrypt(token.encode("ascii"))
        except (InvalidToken, UnicodeError):
            broken.append(where)

    for label, field in ENCRYPTED_FIELDS:
        model = apps.get_model(label)
        for pk, token in model._default_manager.exclude(**{field: ""}).values_list("pk", field):
            check(f"{label}.{field}:{pk}", token)
    for key, token in Setting.objects.exclude(ciphertext="").values_list("key", "ciphertext"):
        check(f"core.Setting:{key}", token)
    return broken


# --- The media token key file (ADR-0007): {"current": {...}, "previous": {...} | null} ---
#
# Django signs with `current`; the edges and stream-auth accept `current` and `previous`.
# A rotation never refuses a URL:
#   stage   {current: old, previous: new}  every edge learns the new key; Django still
#                                           signs with the old one
#   promote {current: new, previous: old}  Django signs with the new key; URLs signed
#                                           with the old one keep working
#   retire  {current: new}                 once they have all expired (media_token_finish)

_KID_NUMBER = re.compile(r"^k(\d+)$")
SECRET_BYTES: Final = 32


def _entry(kid: str) -> dict[str, str]:
    secret = base64.urlsafe_b64encode(secrets.token_bytes(SECRET_BYTES)).rstrip(b"=").decode()
    return {"kid": kid, "secret": secret}


def _validated(data: dict[str, Any]) -> dict[str, Any]:
    from apps.playback.tokens import parse_keyset  # noqa: PLC0415

    parse_keyset(data)  # raises KeySetError naming the problem, never a secret
    return data


def next_kid(data: dict[str, Any]) -> str:
    numbers = [0]
    for slot in ("current", "previous"):
        entry = data.get(slot)
        if isinstance(entry, dict) and (match := _KID_NUMBER.match(str(entry.get("kid", "")))):
            numbers.append(int(match[1]))
    return f"k{max(numbers) + 1}"


def stage(data: dict[str, Any]) -> dict[str, Any]:
    _validated(data)
    return _validated({"current": data["current"], "previous": _entry(next_kid(data))})


def promote(data: dict[str, Any]) -> dict[str, Any]:
    _validated(data)
    if not data.get("previous"):
        msg = "nothing staged: run the start of the rotation first"
        raise ValueError(msg)
    return _validated({"current": data["previous"], "previous": data["current"]})


def retire(data: dict[str, Any]) -> dict[str, Any]:
    _validated(data)
    return _validated({"current": data["current"]})


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"
