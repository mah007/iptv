"""Typed runtime settings: read, change and audit them (SPEC §6 ops, §8.3 Settings).

Reads are cached so changes apply without a restart:
- `settings:version` in redis-cache holds an opaque token, replaced on every change
  (after the transaction commits, so no reader can cache the old row under it);
- `settings:values:v2:<version>` caches the stored rows for that version;
- each process memoises the rows of the version it last saw, so a read costs
  one small Redis GET. If redis-cache is unavailable, reads fall back to Postgres.

The values of one version never change, so a stale memo is impossible: a new
version means a new token and a fresh load.

Effective value (ADR-0019): the admin's stored value, else the setting's server-config
variables (`SettingDef.env`, from `.env`), else the registry default. Secrets
(sensitive settings) are stored as Fernet ciphertext, cached as ciphertext, decrypted
only by `get_secret`, never returned by the API and audited without values.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Final

import redis
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts import crypto
from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core import registry
from apps.core.ids import uuid7
from apps.core.models import Setting
from apps.core.registry import SettingDef, SettingKind, SettingValue

logger = logging.getLogger(__name__)

SETTINGS_VERSION_KEY = "settings:version"
SETTINGS_VALUES_TTL_S = 3600
SETTING_AUDIT_TARGET = "core.setting"

# What a stored row means, as cached: ("v", value) a plain value; ("c", token) a
# secret's ciphertext ("" = set to empty); ("p", text) a secret stored in plaintext
# before it became sensitive (read with a warning until a migration encrypts it).
type Stored = tuple[str, object]
type Overrides = dict[str, Stored]

_PLAIN: Final = "v"
_CIPHER: Final = "c"
_LEGACY: Final = "p"
_MISSING: Final = object()

# (version, overrides) of the last version this process loaded.
_memo: tuple[str, Overrides] | None = None
# Problems already logged by this process (each once, never with a value).
_warned: set[str] = set()


class SettingSource(StrEnum):
    """Where a setting's effective value comes from."""

    DATABASE = "database"
    ENVIRONMENT = "environment"
    DEFAULT = "default"


class SensitiveSettingError(TypeError):
    """A secret was read with `get_setting`; secrets are read with `get_secret`."""


class SettingsValidationError(ValidationError):
    """Some values of a bulk change are invalid; `errors` maps each key to its messages."""

    def __init__(self, errors: Mapping[str, list[str]]) -> None:
        self.errors = dict(errors)
        first = next(iter(self.errors.values()))
        super().__init__(first[0] if first else "Invalid settings.", code="invalid")


@dataclass(frozen=True, slots=True)
class Resolved:
    """A setting's effective value (plaintext, secrets included: never serialize it)."""

    value: SettingValue
    source: SettingSource
    undecryptable: bool = False


@dataclass(frozen=True, slots=True)
class SettingState:
    """A setting's definition with its effective value, for the admin API.

    `value` is "" for a secret: its plaintext never leaves `get_secret`.
    """

    definition: SettingDef
    value: SettingValue
    source: SettingSource
    is_set: bool
    updated_at: datetime | None = None
    updated_by: str | None = None
    undecryptable: bool = False

    @property
    def is_default(self) -> bool:
        """Nothing stored by an admin (the value is the server config's or the default)."""
        return self.source is not SettingSource.DATABASE


# --- Server config (the fallback) --------------------------------------------------------------


def server_config(name: str) -> object | None:
    """A server-config variable: the Django setting of that name when the settings
    module defines it, else the environment variable; None (or "") means unset."""
    value = getattr(django_settings, name, _MISSING)
    if value is _MISSING:
        value = os.environ.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _warn_once(problem: str, message: str, *args: object) -> None:
    if problem not in _warned:
        _warned.add(problem)
        logger.warning(message, *args)


def _from_environment(definition: SettingDef) -> SettingValue | None:
    if not definition.env:
        return None
    raw = {name: server_config(name) for name in definition.env}
    try:
        return definition.from_server_config(raw)
    except ValidationError:
        fingerprint = sha256(repr(sorted(raw.items())).encode()).hexdigest()[:12]
        _warn_once(
            f"env:{definition.key}:{fingerprint}",
            "the server config value of setting %s is invalid; using its default",
            definition.key,
        )
        return None


# --- Reading -------------------------------------------------------------------------------------


def _values_key(version: str) -> str:
    return f"settings:values:v2:{version}"


def _stored(definition: SettingDef | None, value: object, ciphertext: str) -> Stored:
    if definition is not None and definition.sensitive:
        if value is None:
            return (_CIPHER, ciphertext)
        return (_LEGACY, value)
    return (_PLAIN, value)


def _load_overrides() -> Overrides:
    keys = [definition.key for definition in registry.definitions()]
    rows = Setting.objects.filter(key__in=keys).values_list("key", "value", "ciphertext")
    return {key: _stored(registry.REGISTRY.get(key), value, cipher) for key, value, cipher in rows}


def _cached_overrides() -> Overrides:
    global _memo  # noqa: PLW0603 (process-local memo, replaced atomically)
    try:
        version = cache.get(SETTINGS_VERSION_KEY)
        if version is None:
            # First read, or the key was evicted (redis-cache is LRU): start a new version.
            cache.add(SETTINGS_VERSION_KEY, uuid7().hex, timeout=None)
            version = cache.get(SETTINGS_VERSION_KEY)
        if version is None:
            return _load_overrides()
        memo = _memo
        if memo is not None and memo[0] == version:
            return memo[1]
        overrides: Overrides | None = cache.get(_values_key(version))
        if overrides is None:
            overrides = _load_overrides()
            cache.set(_values_key(version), overrides, timeout=SETTINGS_VALUES_TTL_S)
    except redis.RedisError:
        logger.warning("settings cache unavailable; reading from the database")
        return _load_overrides()
    _memo = (version, overrides)
    return overrides


def _resolve(definition: SettingDef, stored: Stored | None) -> Resolved:
    """The effective value: stored, else server config, else default. A stored value
    that no longer validates is ignored; an undecryptable secret reads as empty."""
    if stored is not None:
        tag, raw = stored
        try:
            if tag == _CIPHER:
                if raw == "":
                    return Resolved("", SettingSource.DATABASE)
                try:
                    plain: object = crypto.decrypt(str(raw))
                except crypto.DecryptionError:
                    _warn_once(
                        f"decrypt:{definition.key}",
                        "setting %s cannot be decrypted with FIELD_ENCRYPTION_KEY; "
                        "it reads as empty until it is set again",
                        definition.key,
                    )
                    return Resolved("", SettingSource.DATABASE, undecryptable=True)
                return Resolved(definition.clean(plain), SettingSource.DATABASE)
            if tag == _LEGACY:
                _warn_once(
                    f"legacy:{definition.key}",
                    "secret setting %s is stored unencrypted; save it again to encrypt it",
                    definition.key,
                )
            return Resolved(definition.clean(raw), SettingSource.DATABASE)
        except ValidationError:
            _warn_once(
                f"invalid:{definition.key}",
                "invalid stored value for setting %s; using the default",
                definition.key,
            )
    from_env = _from_environment(definition)
    if from_env is not None:
        return Resolved(from_env, SettingSource.ENVIRONMENT)
    return Resolved(definition.default, SettingSource.DEFAULT)


def resolve(key: str) -> Resolved:
    """A setting's effective value and source (secrets in plaintext: internal use only)."""
    definition = registry.get_definition(key)
    return _resolve(definition, _cached_overrides().get(key))


def get_setting(key: str) -> SettingValue:
    """The effective value of a registered setting. Raises UnknownSettingError, and
    SensitiveSettingError for a secret (read those with `get_secret`)."""
    definition = registry.get_definition(key)
    if definition.sensitive:
        msg = f"{key} is a secret: read it with get_secret"
        raise SensitiveSettingError(msg)
    return _resolve(definition, _cached_overrides().get(key)).value


def get_secret(key: str) -> str:
    """The plaintext of a secret setting ("" when not set): the one way to read secrets.

    Never log, serialize or audit what it returns.
    """
    definition = registry.get_definition(key)
    if not definition.sensitive:
        msg = f"{key} is not a secret: read it with get_setting"
        raise TypeError(msg)
    return str(_resolve(definition, _cached_overrides().get(key)).value)


def setting_source(key: str) -> SettingSource:
    return resolve(key).source


def is_enabled(flag: str) -> bool:
    """True when the boolean setting (feature flag) `flag` is on."""
    definition = registry.get_definition(flag)
    if definition.kind is not SettingKind.BOOL:
        msg = f"{flag} is not a boolean setting"
        raise TypeError(msg)
    return get_setting(flag) is True


def bump_settings_version() -> None:
    """Make every process reload settings on its next read."""
    try:
        cache.set(SETTINGS_VERSION_KEY, uuid7().hex, timeout=None)
    except redis.RedisError:
        logger.exception("could not bump the settings version; other processes keep old values")


def reset_settings_cache() -> None:
    """Forget cached settings everywhere (tests, and after restoring a database)."""
    global _memo  # noqa: PLW0603
    _memo = None
    _warned.clear()
    cache.delete(SETTINGS_VERSION_KEY)


# --- States for the admin API ------------------------------------------------------------------


def _is_set(definition: SettingDef, value: SettingValue) -> bool:
    return value != "" if definition.kind is SettingKind.STR else True


def _state(definition: SettingDef, row: Setting | None) -> SettingState:
    stored = _stored(definition, row.value, row.ciphertext) if row is not None else None
    resolved = _resolve(definition, stored)
    return SettingState(
        definition=definition,
        value="" if definition.sensitive else resolved.value,
        source=resolved.source,
        is_set=_is_set(definition, resolved.value),
        updated_at=row.updated_at if row is not None else None,
        updated_by=_username(row.updated_by) if row is not None else None,
        undecryptable=resolved.undecryptable,
    )


def _states(definitions: list[SettingDef]) -> list[SettingState]:
    """States straight from the database (the admin must see what's stored). One query."""
    rows = {
        row.key: row
        for row in Setting.objects.select_related("updated_by").filter(
            key__in=[definition.key for definition in definitions]
        )
    }
    return [_state(definition, rows.get(definition.key)) for definition in definitions]


def list_settings() -> list[SettingState]:
    """Every registered setting with its effective value, in registry order. One query."""
    return _states(registry.definitions())


def setting_states(keys: list[str]) -> list[SettingState]:
    """These settings' states, in the order given. Raises UnknownSettingError."""
    return _states([registry.get_definition(key) for key in keys])


def setting_state(key: str) -> SettingState:
    """One setting's state. Raises UnknownSettingError."""
    return _states([registry.get_definition(key)])[0]


# --- Writing -----------------------------------------------------------------------------------


def _audit_snapshot(definition: SettingDef, resolved: Resolved) -> dict[str, object]:
    """What the audit log keeps: a plain setting's value; for a secret only whether it
    is set and where it comes from, never the value or a mask of it."""
    if definition.sensitive:
        return {"configured": resolved.value != "", "source": resolved.source.value}
    return {"value": resolved.value}


def _row_resolved(definition: SettingDef, row: Setting | None) -> Resolved:
    stored = _stored(definition, row.value, row.ciphertext) if row is not None else None
    return _resolve(definition, stored)


def _forget_local() -> None:
    """Read-your-writes inside the writing transaction: this process forgets its memo
    and the current version's cached rows. Other processes reload after the commit,
    when the version token changes."""
    global _memo  # noqa: PLW0603
    _memo = None
    try:
        version = cache.get(SETTINGS_VERSION_KEY)
        if version is not None:
            cache.delete(_values_key(version))
    except redis.RedisError:
        logger.warning("settings cache unavailable; other processes update after the commit")


def _store(
    definition: SettingDef,
    cleaned: SettingValue,
    *,
    actor: User | None,
    ip: str | None,
    action: str = "setting.update",
) -> SettingState:
    """Store an already validated value; must run inside a transaction."""
    row = Setting.objects.select_for_update().filter(key=definition.key).first()
    before = _row_resolved(definition, row)
    if (
        row is not None
        and before.source is SettingSource.DATABASE
        and not before.undecryptable
        and before.value == cleaned
        and type(before.value) is type(cleaned)
    ):
        return _state(definition, row)
    if definition.sensitive:
        value, ciphertext = None, crypto.encrypt(str(cleaned)) if cleaned != "" else ""
    else:
        value, ciphertext = cleaned, ""
    if row is None:
        row = Setting.objects.create(
            key=definition.key, value=value, ciphertext=ciphertext, updated_by=actor
        )
    else:
        row.value, row.ciphertext, row.updated_by = value, ciphertext, actor
        row.save(update_fields=["value", "ciphertext", "updated_by", "updated_at"])
    _forget_local()
    audit.record(
        action,
        actor=actor,
        target=AuditTarget(SETTING_AUDIT_TARGET, definition.key),
        before=_audit_snapshot(definition, before),
        after=_audit_snapshot(definition, Resolved(cleaned, SettingSource.DATABASE)),
        ip=ip,
    )
    transaction.on_commit(bump_settings_version)
    return _state(definition, row)


def _reset(definition: SettingDef, *, actor: User | None, ip: str | None) -> SettingState:
    """Delete the stored value; must run inside a transaction."""
    row = Setting.objects.select_for_update().filter(key=definition.key).first()
    if row is None:
        return _state(definition, None)
    before = _row_resolved(definition, row)
    row.delete()
    _forget_local()
    after = _row_resolved(definition, None)
    audit.record(
        "setting.reset",
        actor=actor,
        target=AuditTarget(SETTING_AUDIT_TARGET, definition.key),
        before=_audit_snapshot(definition, before),
        after=_audit_snapshot(definition, after),
        ip=ip,
    )
    transaction.on_commit(bump_settings_version)
    return _state(definition, None)


def set_setting(
    key: str, value: object, *, actor: User | None, ip: str | None = None
) -> SettingState:
    """Validate, store and audit a new value; other processes see it after commit.

    Raises UnknownSettingError for an unregistered key and ValidationError for a
    value of the wrong type or outside the setting's constraints. Setting the
    stored value again changes nothing and records nothing. A secret is encrypted.
    """
    definition = registry.get_definition(key)
    cleaned = definition.clean(value)
    with transaction.atomic():
        return _store(definition, cleaned, actor=actor, ip=ip)


def reset_setting(key: str, *, actor: User | None, ip: str | None = None) -> SettingState:
    """Forget the admin's value: the server config's (or the default) applies again."""
    definition = registry.get_definition(key)
    with transaction.atomic():
        return _reset(definition, actor=actor, ip=ip)


def adopt_setting(key: str, *, actor: User | None, ip: str | None = None) -> SettingState:
    """Copy the value the server config provides into the settings (encrypted for a
    secret), so `.env` no longer needs it. Raises ValidationError when there is none."""
    definition = registry.get_definition(key)
    from_env = _from_environment(definition)
    if from_env is None:
        msg = "The server config holds no value for this setting."
        raise ValidationError(msg, code="no_server_config")
    with transaction.atomic():
        return _store(definition, from_env, actor=actor, ip=ip, action="setting.adopt")


def set_settings(
    changes: Mapping[str, object | None], *, actor: User | None, ip: str | None = None
) -> list[SettingState]:
    """Change several settings at once, all or nothing. `None` resets a setting.

    Every value is validated first; raises UnknownSettingError, or
    SettingsValidationError with the messages per key.
    """
    planned: list[tuple[SettingDef, SettingValue | None]] = []
    errors: dict[str, list[str]] = {}
    for key, value in changes.items():
        definition = registry.get_definition(key)
        if value is None:
            planned.append((definition, None))
            continue
        try:
            planned.append((definition, definition.clean(value)))
        except ValidationError as exc:
            errors[key] = exc.messages
    if errors:
        raise SettingsValidationError(errors)
    with transaction.atomic():
        return [
            _reset(definition, actor=actor, ip=ip)
            if cleaned is None
            else _store(definition, cleaned, actor=actor, ip=ip)
            for definition, cleaned in planned
        ]


def reencrypt_secret_settings() -> int:
    """Re-encrypt every stored secret with the first FIELD_ENCRYPTION_KEY (key rotation:
    put the new key first, run this, then drop the old key). Returns how many."""
    count = 0
    with transaction.atomic():
        for row in Setting.objects.select_for_update().exclude(ciphertext=""):
            row.ciphertext = crypto.rotate(row.ciphertext)
            row.save(update_fields=["ciphertext", "updated_at"])
            count += 1
        transaction.on_commit(bump_settings_version)
    return count


def from_server_config() -> list[SettingState]:
    """The settings whose effective value comes from the server config (`.env`)."""
    return [state for state in list_settings() if state.source is SettingSource.ENVIRONMENT]


def _username(user: User | None) -> str | None:
    return user.username if user is not None else None
