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
    "xtream.auth_failures_per_username": 30,
    "xtream.auth_failures_per_ip": 60,
    "xtream.auth_failure_window_s": 900,
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
    # T1 (ADR-0014): media processing.
    "library.hls_enabled": True,
    "library.uhd_enabled": True,
    "library.uhd_cpu_encode": False,
    "library.rendition_retention_days": 7,
    "billing.vat_rate": 0.15,
    "billing.currency": "SAR",
    "billing.grace_days": 3,
    "trials.duration_hours": 24,
    "trials.limit_per_phone": 1,
    "security.admin_idle_timeout_min": 30,
    # C1 (ADR-0013): customer sign-in and web playback progress.
    "security.customer_session_days": 30,
    "security.web_device_idle_days": 30,
    "security.customer_access_token_ttl_s": 600,
    "security.customer_refresh_token_days": 30,
    "security.customer_auth_requests_per_ip": 60,
    "security.password_reset_ttl_min": 60,
    "security.password_invite_ttl_days": 7,
    "security.password_reset_emails_per_hour": 3,
    "playback.progress_min_interval_s": 5,
    "playback.watched_ratio": 0.9,
    # B1 (ADR-0012): billing and trials.
    "billing.prices_include_vat": True,
    "billing.invoice_prefix": "INV",
    "billing.vat_number": "",
    "billing.seller_address": "",
    "billing.manual_instructions_en": (
        "Transfer the amount to our bank account and send us the receipt; "
        "quote the reference below."
    ),
    "billing.manual_instructions_ar": (
        "حوّل المبلغ إلى حسابنا البنكي وأرسل لنا الإيصال مع ذكر الرقم المرجعي أدناه."
    ),
    "billing.stripe_enabled": False,
    "billing.moyasar_enabled": False,
    "billing.checkout_ttl_hours": 24,
    "trials.require_approval": True,
    "features.subtitle_download": False,
    "features.machine_translation": False,
    "features.include_vod_in_m3u": True,
    "features.approve_new_devices": False,
    "live.idle_stop_s": 150,
    "live.max_running_channels": 20,
    "live.realtime_transcode_max": 1,
    "live.catchup_max_days": 7,
    "live.timeshift_max_minutes": 480,
    "live.epg_past_days": 7,
    "live.epg_future_days": 14,
    "live.archive_max_gb": 20,
    "live.epg_max_document_mb": 200,
    "monitoring.alerts_enabled": True,
    "monitoring.alert_emails": "",
    "monitoring.alert_email_severities": "all",
    "monitoring.telegram_severities": "all",
    "monitoring.edge_capacity_mbps": 1000,
    "monitoring.session_hours": 8,
    # CFG (ADR-0019): integrations, email, branding and former environment variables.
    "monitoring.runbook_base_url": "https://github.com/mah007/iptv/blob/main/",
    "branding.support_phone": "",
    "branding.support_whatsapp": "",
    "branding.logo_light": "",
    "branding.logo_dark": "",
    "branding.default_language": "ar",
    "branding.time_zone": "Asia/Riyadh",
    "library.watcher_polling": False,
    "library.transcode_max_attempts": 3,
    "library.transcode_retry_backoff_s": 60,
    "library.transcode_job_timeout_h": 12,
    "metadata.tmdb_read_access_token": "",
    "metadata.tmdb_api_key": "",
    "metadata.tmdb_language": "en-US",
    "metadata.tmdb_rate_limit_per_s": 35,
    "metadata.tmdb_cache_ttl_s": 86400,
    "metadata.tvdb_api_key": "",
    "metadata.tvdb_pin": "",
    "metadata.tvdb_rate_limit_per_s": 10,
    "billing.stripe_secret_key": "",
    "billing.stripe_webhook_secret": "",
    "billing.moyasar_secret_key": "",
    "billing.moyasar_webhook_secret": "",
    "email.smtp_host": "",
    "email.smtp_port": 587,
    "email.smtp_security": "starttls",
    "email.smtp_username": "",
    "email.smtp_password": "",
    "email.from_address": "",
    "email.timeout_s": 20,
    "notifications.telegram_bot_token": "",
    "notifications.telegram_chat_id": "",
    # H2 (ADR-0020): backups, personal-data retention and CrowdSec.
    "backups.enabled": True,
    "backups.schedule_time": "03:30",
    "backups.schedule_timezone": "UTC",
    "backups.full_weekday": 6,
    "backups.db_full_retention": 2,
    "backups.files_keep_daily": 7,
    "backups.files_keep_weekly": 4,
    "backups.files_keep_monthly": 6,
    "backups.media_enabled": False,
    "backups.drill_enabled": True,
    "backups.drill_weekday": 0,
    "backups.drill_time": "05:00",
    "backups.s3_enabled": False,
    "backups.s3_endpoint": "",
    "backups.s3_region": "",
    "backups.s3_bucket": "",
    "backups.s3_prefix": "smart-iptv",
    "backups.s3_path_style": False,
    "backups.s3_access_key": "",
    "backups.s3_secret_key": "",
    "backups.offsite_interval_minutes": 60,
    "privacy.session_ip_days": 30,
    "privacy.session_retention_days": 365,
    "privacy.watch_history_days": 365,
    "privacy.resume_days": 730,
    "privacy.audit_retention_days": 730,
    "privacy.audit_billing_retention_days": 2190,
    "privacy.outbox_retention_days": 90,
    "privacy.auth_log_retention_days": 90,
    "privacy.webhook_payload_days": 365,
    "security.crowdsec_enforce": True,
    "security.crowdsec_allowlist": "",
    # M14a (ADR-0021): step-up, WebAuthn, the admin allowlist, CSP, rate limits, GeoIP,
    # anti-sharing.
    "security.admin_step_up_window_min": 5,
    "security.customer_step_up_window_min": 10,
    "security.webauthn_required_for": "none",
    "security.admin_ip_allowlist": "",
    "security.csp_extra_media_origins": "",
    "security.rate_search_per_minute": 60,
    "security.rate_playback_start_per_minute": 30,
    "security.rate_device_credentials_per_hour": 10,
    "security.rate_checkout_per_hour": 10,
    "security.rate_admin_tmdb_search_per_minute": 30,
    "security.rate_backup_runs_per_hour": 6,
    "geoip.enabled": True,
    "geoip.auto_update": True,
    "geoip.unknown_country_policy": "deny",
    "antisharing.enabled": True,
    "antisharing.max_asns_1h": 2,
    "antisharing.max_asns_24h": 3,
    "antisharing.max_countries_24h": 1,
    "antisharing.max_networks_per_credential_1h": 2,
    "antisharing.travel_speed_kmh": 900,
    "antisharing.travel_min_km": 500,
    "antisharing.flag_score": 4,
    "antisharing.notify_admins": True,
    "privacy.antisharing_retention_days": 90,
    # M15 (ADR-0022): edges, the S3 origin, Bunny and the read replica.
    "edges.stale_after_s": 60,
    "edges.max_utilisation_pct": 90,
    "storage.s3_enabled": False,
    "storage.s3_endpoint": "",
    "storage.s3_region": "",
    "storage.s3_bucket": "",
    "storage.s3_prefix": "renditions",
    "storage.s3_path_style": True,
    "storage.s3_access_key": "",
    "storage.s3_secret_key": "",
    "storage.s3_edge_access_key": "",
    "storage.s3_edge_secret_key": "",
    "storage.publish_sources": True,
    "storage.publish_mbps": 0,
    "storage.old_generation_hours": 8,
    "cdn.bunny_token_key": "",
    "cdn.origin_secret": "",
    "cdn.bunny_ip_binding": False,
    "database.replica_reads": True,
    "database.replica_max_lag_s": 30,
    # Q1 (ADR-0023): capped compat MP4s by remux.
    "library.capped_compat_mp4": "auto",
    # Q1 (ADR-0023): live ingest sign-ins through /internal/mediamtx-auth.
    "live.ingest_publish_user": "publisher",
    "live.ingest_publish_password": "",
    "live.ingest_read_user": "reader",
    "live.ingest_read_password": "",
    "live.ingest_auth_max_failures": 20,
    "live.ingest_auth_block_minutes": 15,
    # Q1 (ADR-0023): the legal pages start as templates for a lawyer to review.
    "legal.privacy_en": registry.legal_template("privacy", "en"),
    "legal.privacy_ar": registry.legal_template("privacy", "ar"),
    "legal.terms_en": registry.legal_template("terms", "en"),
    "legal.terms_ar": registry.legal_template("terms", "ar"),
}


SENSITIVE_KEYS = {
    "metadata.tmdb_read_access_token",
    "metadata.tmdb_api_key",
    "metadata.tvdb_api_key",
    "metadata.tvdb_pin",
    "billing.stripe_secret_key",
    "billing.stripe_webhook_secret",
    "billing.moyasar_secret_key",
    "billing.moyasar_webhook_secret",
    "email.smtp_password",
    "notifications.telegram_bot_token",
    "backups.s3_access_key",
    "backups.s3_secret_key",
    "storage.s3_access_key",
    "storage.s3_secret_key",
    "storage.s3_edge_access_key",
    "storage.s3_edge_secret_key",
    "cdn.bunny_token_key",
    "cdn.origin_secret",
    "live.ingest_publish_password",
    "live.ingest_read_password",
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
        # Secrets are the exception (masked in the API and the audit log).
        assert not definition.sensitive or definition.key in SENSITIVE_KEYS


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
        ("billing.invoice_prefix", "inv-", "capital letters"),
        ("billing.vat_number", "12", "5 to 20"),
        # CFG (ADR-0019).
        ("email.smtp_host", "smtp example.com", "host name"),
        ("email.smtp_port", 0, "at least 1"),
        ("email.smtp_security", "tls", "one of"),
        ("email.from_address", "Smart IPTV <not an address>", "address"),
        ("email.from_address", "a@example.com\nBcc: x@example.com", "address"),
        ("metadata.tmdb_read_access_token", "abc", "eyJ"),
        ("metadata.tmdb_api_key", "XYZ", "32 letters"),
        ("metadata.tmdb_language", "english", "en-US"),
        ("billing.stripe_secret_key", "pk_" + "test_0123456789abcdef", "sk_test_"),
        ("billing.stripe_webhook_secret", "secret", "whsec_"),
        ("billing.moyasar_secret_key", "pk_" + "live_0123456789", "sk_test_"),
        ("billing.moyasar_webhook_secret", "has space here", "without spaces"),
        ("notifications.telegram_bot_token", "12345", "BotFather"),
        ("notifications.telegram_chat_id", "@channel", "chat id"),
        ("branding.support_phone", "0501234567", "country code"),
        ("branding.time_zone", "Mars/Olympus", "IANA"),
        ("branding.default_language", "fr", "one of"),
        ("branding.logo_light", "../../etc/passwd", "Upload"),
        ("monitoring.runbook_base_url", "ftp://example.com/", "valid URL"),
        ("library.transcode_max_attempts", 0, "at least 1"),
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
        ("email.smtp_host", "smtp.example.com"),
        ("email.smtp_host", "192.0.2.10"),
        ("email.from_address", "Smart IPTV <no-reply@example.com>"),
        ("email.from_address", "no-reply@example.com"),
        ("metadata.tmdb_api_key", "0123456789abcdef0123456789abcdef"),
        ("billing.stripe_secret_key", "sk_" + "test_" + "51ABCDEFGHIJKLMNOP"),
        ("billing.stripe_webhook_secret", "whsec_" + "ABCDEFGHIJKLMNOP"),
        ("billing.moyasar_secret_key", "sk_" + "live_ABCDEFGHIJKL"),
        ("branding.support_whatsapp", "+966501234567"),
        ("branding.time_zone", "Europe/London"),
        ("branding.logo_dark", "images/branding/logo-dark-0123456789ab.png"),
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


def test_build_registry_refuses_bad_secrets_env_names_and_widgets() -> None:
    with pytest.raises(ImproperlyConfigured, match="sensitive but not a string"):
        build_registry(
            [SettingDef("features.x", SettingKind.BOOL, False, "X.", "features", sensitive=True)]
        )
    with pytest.raises(ImproperlyConfigured, match="invalid environment variable"):
        build_registry(
            [SettingDef("features.x", SettingKind.STR, "", "X.", "features", env=("bad-name",))]
        )
    with pytest.raises(ImproperlyConfigured, match="env_resolve without env"):
        build_registry(
            [
                SettingDef(
                    "features.x", SettingKind.STR, "", "X.", "features", env_resolve=lambda _: None
                )
            ]
        )
    with pytest.raises(ImproperlyConfigured, match="unknown widget"):
        build_registry(
            [SettingDef("features.x", SettingKind.STR, "", "X.", "features", widget="colour")]
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"EMAIL_USE_SSL": None, "EMAIL_USE_TLS": None}, None),
        ({"EMAIL_USE_SSL": True, "EMAIL_USE_TLS": True}, "ssl"),
        ({"EMAIL_USE_SSL": "false", "EMAIL_USE_TLS": "true"}, "starttls"),
        ({"EMAIL_USE_SSL": None, "EMAIL_USE_TLS": False}, "none"),
        ({"EMAIL_USE_SSL": "maybe", "EMAIL_USE_TLS": None}, None),
    ],
)
def test_smtp_security_comes_from_django_s_pair(raw: dict[str, object], expected: object) -> None:
    assert get_definition("email.smtp_security").from_server_config(raw) == expected


def test_server_config_values_are_typed_and_ranged_but_not_format_checked() -> None:
    port = get_definition("email.smtp_port")
    assert port.from_server_config({"EMAIL_PORT": "2525"}) == 2525
    with pytest.raises(ValidationError):
        port.from_server_config({"EMAIL_PORT": "0"})
    # A key set in .env before the format check existed keeps working.
    stripe = get_definition("billing.stripe_secret_key")
    fixture = "sk_" + "test_fixture"
    assert stripe.from_server_config({"STRIPE_SECRET_KEY": fixture}) == fixture
    timeout = get_definition("library.transcode_job_timeout_h")
    assert timeout.from_server_config({"TRANSCODE_JOB_TIMEOUT_S": "5400"}) == 2
    assert timeout.from_server_config({"TRANSCODE_JOB_TIMEOUT_S": None}) is None
