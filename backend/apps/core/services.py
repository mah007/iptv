"""Typed runtime settings: read, change and audit them (SPEC §6 ops, §8.3 Settings).

Reads are cached so changes apply without a restart:
- `settings:version` in redis-cache holds an opaque token, replaced on every change
  (after the transaction commits, so no reader can cache the old row under it);
- `settings:values:<version>` caches the database overrides for that version;
- each process memoises the overrides of the version it last saw, so a read costs
  one small Redis GET. If redis-cache is unavailable, reads fall back to Postgres.

The values of one version never change, so a stale memo is impossible: a new
version means a new token and a fresh load.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import redis
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core import registry
from apps.core.ids import uuid7
from apps.core.models import Setting
from apps.core.redaction import MASK
from apps.core.registry import SettingDef, SettingKind, SettingValue

logger = logging.getLogger(__name__)

SETTINGS_VERSION_KEY = "settings:version"
SETTINGS_VALUES_TTL_S = 3600
SETTING_AUDIT_TARGET = "core.setting"

type Overrides = dict[str, object]

# (version, overrides) of the last version this process loaded.
_memo: tuple[str, Overrides] | None = None


@dataclass(frozen=True, slots=True)
class SettingState:
    """A setting's definition with its effective value, for the admin API."""

    definition: SettingDef
    value: SettingValue
    is_default: bool
    updated_at: datetime | None = None
    updated_by: str | None = None


def _values_key(version: str) -> str:
    return f"settings:values:{version}"


def _load_overrides() -> Overrides:
    keys = [definition.key for definition in registry.definitions()]
    return dict(Setting.objects.filter(key__in=keys).values_list("key", "value"))


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


def _effective(definition: SettingDef, overrides: Overrides) -> tuple[SettingValue, bool]:
    """(value, is_default). An override that no longer validates falls back to the default."""
    if definition.key not in overrides:
        return definition.default, True
    try:
        return definition.clean(overrides[definition.key]), False
    except ValidationError:
        logger.warning("invalid stored value for setting %s; using the default", definition.key)
        return definition.default, True


def get_setting(key: str) -> SettingValue:
    """The effective value of a registered setting. Raises UnknownSettingError."""
    definition = registry.get_definition(key)
    value, _is_default = _effective(definition, _cached_overrides())
    return value


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
    cache.delete(SETTINGS_VERSION_KEY)


def _states(definitions: list[SettingDef]) -> list[SettingState]:
    """States straight from the database (the admin must see what's stored). One query."""
    rows = {
        row.key: row
        for row in Setting.objects.select_related("updated_by").filter(
            key__in=[definition.key for definition in definitions]
        )
    }
    overrides: Overrides = {key: row.value for key, row in rows.items()}
    states = []
    for definition in definitions:
        value, is_default = _effective(definition, overrides)
        row = rows.get(definition.key)
        states.append(
            SettingState(
                definition=definition,
                value=value,
                is_default=is_default,
                updated_at=row.updated_at if row else None,
                updated_by=_username(row.updated_by) if row else None,
            )
        )
    return states


def list_settings() -> list[SettingState]:
    """Every registered setting with its effective value, in registry order. One query."""
    return _states(registry.definitions())


def setting_state(key: str) -> SettingState:
    """One setting's state. Raises UnknownSettingError."""
    return _states([registry.get_definition(key)])[0]


def _audit_value(definition: SettingDef, value: object) -> object:
    return MASK if definition.sensitive else value


def set_setting(
    key: str, value: object, *, actor: User | None, ip: str | None = None
) -> SettingState:
    """Validate, store and audit a new value; other processes see it after commit.

    Raises UnknownSettingError for an unregistered key and ValidationError for a
    value of the wrong type or outside the setting's constraints. Setting the
    current value again changes nothing and records nothing.
    """
    definition = registry.get_definition(key)
    cleaned = definition.clean(value)
    with transaction.atomic():
        row, created = Setting.objects.select_for_update().get_or_create(
            key=key, defaults={"value": cleaned, "updated_by": actor}
        )
        if created:
            before: object = definition.default
        else:
            before = row.value
            if before == cleaned and type(before) is type(cleaned):
                return SettingState(
                    definition, cleaned, False, row.updated_at, _username(row.updated_by)
                )
            row.value = cleaned
            row.updated_by = actor
            row.save(update_fields=["value", "updated_by", "updated_at"])
        audit.record(
            "setting.update",
            actor=actor,
            target=AuditTarget(SETTING_AUDIT_TARGET, key),
            before={"value": _audit_value(definition, before)},
            after={"value": _audit_value(definition, cleaned)},
            ip=ip,
        )
        transaction.on_commit(bump_settings_version)
    return SettingState(definition, cleaned, False, row.updated_at, _username(actor))


def _username(user: User | None) -> str | None:
    return user.username if user is not None else None
