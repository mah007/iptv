"""The typed settings registry (SPEC §8.3 Settings; plan §2.5 keys and defaults)."""

import json

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError

from apps.core import registry
from apps.core.registry import (
    GROUPS,
    SettingDef,
    SettingKind,
    UnknownSettingError,
    build_registry,
    get_definition,
)

EXPECTED_DEFAULTS = {
    "branding.service_name_en": "Smart IPTV",
    "branding.service_name_ar": "سمارت IPTV",
    "branding.accent_color": "#0F766E",
    "branding.support_email": "",
    "xtream.server_url": "",
    "xtream.port": 80,
    "xtream.https_port": 443,
    "xtream.password_min_length": 8,
    "playback.token_ttl_vod_s": 7200,
    "playback.token_ttl_live_s": 21600,
    "playback.ip_binding": False,
    "playback.realtime_transcode_enabled": False,
    "playback.realtime_transcode_max": 2,
    "metadata.match_auto_accept": 0.85,
    "metadata.match_margin": 0.10,
    "metadata.certification_country": "SA",
    "metadata.genre_category_map": json.dumps(
        dict(registry.DEFAULT_GENRE_CATEGORY_MAP), separators=(",", ":")
    ),
    "library.watcher_stable_s": 60,
    "billing.vat_rate": 0.15,
    "billing.currency": "SAR",
    "billing.grace_days": 3,
    "trials.duration_hours": 24,
    "trials.limit_per_phone": 1,
    "security.admin_idle_timeout_min": 30,
    "features.subtitle_download": False,
    "features.machine_translation": False,
    "features.include_vod_in_m3u": True,
    "features.approve_new_devices": False,
}


def test_registry_declares_the_planned_keys_and_defaults() -> None:
    assert {key: definition.default for key, definition in registry.REGISTRY.items()} == (
        EXPECTED_DEFAULTS
    )


def test_every_definition_is_consistent() -> None:
    for definition in registry.definitions():
        assert definition.group in GROUPS
        assert definition.key.startswith(f"{definition.group}.")
        assert definition.description
        assert definition.clean(definition.default) == definition.default
        assert not definition.sensitive


def test_unknown_keys_raise() -> None:
    with pytest.raises(UnknownSettingError):
        get_definition("branding.nope")


@pytest.mark.parametrize(
    ("kind", "good", "bad"),
    [
        (SettingKind.BOOL, [True, False], [1, 0, "true", None]),
        (SettingKind.INT, [0, 7, -3], [True, 1.5, "7", None]),
        (SettingKind.FLOAT, [0.5, 1, 0], [True, "0.5", None]),
        (SettingKind.STR, ["", "x"], [1, True, None, ["x"]]),
    ],
)
def test_values_must_match_the_kind(
    kind: SettingKind, good: list[object], bad: list[object]
) -> None:
    definition = SettingDef("features.test", kind, good[0], "Test.", "features")  # type: ignore[arg-type]
    for value in good:
        definition.clean(value)
    for value in bad:
        with pytest.raises(ValidationError, match="Expected a value of type"):
            definition.clean(value)


def test_float_settings_store_floats() -> None:
    cleaned = get_definition("billing.vat_rate").clean(1)
    assert cleaned == 1.0
    assert isinstance(cleaned, float)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("xtream.port", 0, "at least 1"),
        ("xtream.port", 70000, "at most 65535"),
        ("billing.vat_rate", 1.5, "at most 1"),
        ("playback.token_ttl_vod_s", 59, "at least 60"),
        ("branding.accent_color", "teal", "#RRGGBB"),
        ("branding.accent_color", "#0F766", "#RRGGBB"),
        ("branding.support_email", "not-an-email", "valid email"),
        ("xtream.server_url", "ftp://tv.example.com", "valid URL"),
        ("billing.currency", "sar", "ISO 4217"),
        ("metadata.certification_country", "SAU", "ISO 3166-1"),
        ("metadata.genre_category_map", "not json", "JSON object"),
        ("metadata.genre_category_map", "[1, 2]", "JSON object"),
        ("metadata.genre_category_map", '{"action": "action"}', "JSON object"),
        ("metadata.genre_category_map", '{"28": "Not A Slug"}', "JSON object"),
        ("library.watcher_stable_s", 1, "at least 5"),
    ],
)
def test_constraints_and_validators(key: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        get_definition(key).clean(value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("branding.support_email", ""),
        ("branding.support_email", "help@example.com"),
        ("xtream.server_url", ""),
        ("xtream.server_url", "https://tv.example.com"),
        ("branding.accent_color", "#a1b2c3"),
        ("metadata.genre_category_map", '{"28": "action", "10765": "sci-fi"}'),
        ("metadata.genre_category_map", "{}"),
    ],
)
def test_valid_values_pass(key: str, value: object) -> None:
    assert get_definition(key).clean(value) == value


def test_choices_are_enforced() -> None:
    definition = SettingDef(
        "features.mode", SettingKind.STR, "a", "Mode.", "features", choices=("a", "b")
    )
    assert definition.clean("b") == "b"
    with pytest.raises(ValidationError, match="one of: a, b"):
        definition.clean("c")


def test_build_registry_refuses_mistakes() -> None:
    ok = SettingDef("features.x", SettingKind.BOOL, False, "X.", "features")
    with pytest.raises(ImproperlyConfigured, match="declared twice"):
        build_registry([ok, ok])
    with pytest.raises(ImproperlyConfigured, match="group"):
        build_registry([SettingDef("features.x", SettingKind.BOOL, False, "X.", "branding")])
    with pytest.raises(ImproperlyConfigured, match="group"):
        build_registry([SettingDef("nope.x", SettingKind.BOOL, False, "X.", "nope")])
    with pytest.raises(ImproperlyConfigured, match="invalid default"):
        build_registry([SettingDef("xtream.port", SettingKind.INT, 0, "P.", "xtream", min_value=1)])
