"""The admin's Integrations page (CFG, ADR-0019): one card per outside service, built
from the settings registry, with its status and a server-side Test.

A card's status: `configured` (everything it needs is stored in the settings),
`server_config` (complete, but at least one value still comes from `.env`),
`partial` (something it needs is missing) or `not_set` (nothing it needs is set).
Tests use the saved settings only ("Save, then Test"), run with short timeouts and
answer a code and a message without any secret.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import redis
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core.errors import ErrorCode, ProblemError
from apps.core.integration import TestResult
from apps.core.services import SettingSource, SettingState, setting_states
from config.origins import origin

TESTS_PER_MINUTE: Final = 5


class IntegrationName(StrEnum):
    TMDB = "tmdb"
    TVDB = "tvdb"
    EMAIL = "email"
    STRIPE = "stripe"
    MOYASAR = "moyasar"
    TELEGRAM = "telegram"
    # M15 (ADR-0022): the S3 origin and the Bunny CDN.
    S3_ORIGIN = "s3_origin"
    BUNNY = "bunny"


class IntegrationStatus(StrEnum):
    CONFIGURED = "configured"
    SERVER_CONFIG = "server_config"
    PARTIAL = "partial"
    NOT_SET = "not_set"


@dataclass(frozen=True, slots=True)
class Integration:
    name: IntegrationName
    #: The settings on its card, in order.
    keys: tuple[str, ...]
    #: What it needs: every group must have at least one key set.
    required: tuple[tuple[str, ...], ...]
    #: The switch that offers it to customers (payment providers).
    enabled_key: str | None = None
    #: The provider slug of its webhook (https://api.<domain>/api/v1/webhooks/<slug>).
    webhook: str | None = None


INTEGRATIONS: Final[tuple[Integration, ...]] = (
    Integration(
        IntegrationName.TMDB,
        keys=(
            "metadata.tmdb_read_access_token",
            "metadata.tmdb_api_key",
            "metadata.tmdb_language",
            "metadata.tmdb_rate_limit_per_s",
            "metadata.tmdb_cache_ttl_s",
        ),
        required=(("metadata.tmdb_read_access_token", "metadata.tmdb_api_key"),),
    ),
    Integration(
        IntegrationName.TVDB,
        keys=("metadata.tvdb_api_key", "metadata.tvdb_pin"),
        required=(("metadata.tvdb_api_key",),),
    ),
    Integration(
        IntegrationName.EMAIL,
        keys=(
            "email.smtp_host",
            "email.smtp_port",
            "email.smtp_security",
            "email.smtp_username",
            "email.smtp_password",
            "email.from_address",
            "email.timeout_s",
        ),
        required=(("email.smtp_host",),),
    ),
    Integration(
        IntegrationName.STRIPE,
        keys=(
            "billing.stripe_secret_key",
            "billing.stripe_webhook_secret",
            "billing.stripe_enabled",
        ),
        required=(("billing.stripe_secret_key",), ("billing.stripe_webhook_secret",)),
        enabled_key="billing.stripe_enabled",
        webhook="stripe",
    ),
    Integration(
        IntegrationName.MOYASAR,
        keys=(
            "billing.moyasar_secret_key",
            "billing.moyasar_webhook_secret",
            "billing.moyasar_enabled",
        ),
        required=(("billing.moyasar_secret_key",), ("billing.moyasar_webhook_secret",)),
        enabled_key="billing.moyasar_enabled",
        webhook="moyasar",
    ),
    Integration(
        IntegrationName.TELEGRAM,
        keys=("notifications.telegram_bot_token", "notifications.telegram_chat_id"),
        required=(("notifications.telegram_bot_token",), ("notifications.telegram_chat_id",)),
    ),
    Integration(
        IntegrationName.S3_ORIGIN,
        keys=(
            "storage.s3_enabled",
            "storage.s3_endpoint",
            "storage.s3_region",
            "storage.s3_bucket",
            "storage.s3_prefix",
            "storage.s3_path_style",
            "storage.s3_access_key",
            "storage.s3_secret_key",
            "storage.s3_edge_access_key",
            "storage.s3_edge_secret_key",
        ),
        required=(
            ("storage.s3_endpoint",),
            ("storage.s3_bucket",),
            ("storage.s3_access_key",),
            ("storage.s3_secret_key",),
        ),
        enabled_key="storage.s3_enabled",
    ),
    Integration(
        IntegrationName.BUNNY,
        keys=("cdn.bunny_token_key", "cdn.origin_secret", "cdn.bunny_ip_binding"),
        required=(("cdn.bunny_token_key",), ("cdn.origin_secret",)),
    ),
)
_BY_NAME: Final = {integration.name.value: integration for integration in INTEGRATIONS}


@dataclass(frozen=True, slots=True)
class IntegrationCard:
    integration: Integration
    status: IntegrationStatus
    enabled: bool | None
    webhook_url: str | None
    settings: list[SettingState]

    @property
    def name(self) -> str:
        return self.integration.name.value


def webhook_url(provider: str) -> str:
    base = origin(str(settings.PUBLIC_SCHEME), str(settings.API_HOST), int(settings.PUBLIC_PORT))
    return f"{base}/api/v1/webhooks/{provider}"


def status_of(integration: Integration, states: dict[str, SettingState]) -> IntegrationStatus:
    satisfied = [
        [states[key] for key in group if states[key].is_set] for group in integration.required
    ]
    met = sum(1 for group in satisfied if group)
    if met == 0:
        return IntegrationStatus.NOT_SET
    if met < len(integration.required):
        return IntegrationStatus.PARTIAL
    from_env = any(
        state.source is SettingSource.ENVIRONMENT for group in satisfied for state in group
    )
    return IntegrationStatus.SERVER_CONFIG if from_env else IntegrationStatus.CONFIGURED


def cards() -> list[IntegrationCard]:
    """Every integration with its settings and status; one query."""
    keys = [key for integration in INTEGRATIONS for key in integration.keys]
    states = {state.definition.key: state for state in setting_states(keys)}
    return [
        IntegrationCard(
            integration=integration,
            status=status_of(integration, states),
            enabled=(
                states[integration.enabled_key].value is True if integration.enabled_key else None
            ),
            webhook_url=webhook_url(integration.webhook) if integration.webhook else None,
            settings=[states[key] for key in integration.keys],
        )
        for integration in INTEGRATIONS
    ]


def get_integration(name: str) -> Integration:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ProblemError(ErrorCode.NOT_FOUND, f"Unknown integration {name}.") from None


def _tester(  # noqa: PLR0911 (one tester per integration)
    integration: Integration, actor: User
) -> Callable[[], TestResult]:
    # Imports stay local: each one pulls in its app (billing, metadata, notifications).
    match integration.name:
        case IntegrationName.TMDB:
            from apps.metadata.checks import check_tmdb  # noqa: PLC0415

            return check_tmdb
        case IntegrationName.TVDB:
            from apps.metadata.checks import check_tvdb  # noqa: PLC0415

            return check_tvdb
        case IntegrationName.EMAIL:
            from apps.notifications.mail import send_test_email  # noqa: PLC0415

            return lambda: send_test_email(actor.email)
        case IntegrationName.STRIPE:
            from apps.billing.providers.stripe import StripeProvider  # noqa: PLC0415

            return lambda: StripeProvider().check(webhook_url("stripe"))
        case IntegrationName.MOYASAR:
            from apps.billing.providers.moyasar import MoyasarProvider  # noqa: PLC0415

            return lambda: MoyasarProvider().check(webhook_url("moyasar"))
        case IntegrationName.TELEGRAM:
            from apps.notifications.telegram import send_test_message  # noqa: PLC0415

            return send_test_message
        case IntegrationName.S3_ORIGIN:
            from apps.media.checks import check_origin  # noqa: PLC0415

            return check_origin
        case IntegrationName.BUNNY:
            from apps.edges.checks import check_bunny  # noqa: PLC0415

            return check_bunny


def _limit(actor: User, name: str) -> None:
    """At most TESTS_PER_MINUTE tests per admin and integration (they send messages)."""
    key = f"integration-test:{actor.pk}:{name}"
    try:
        cache.add(key, 0, timeout=60)
        count = cache.incr(key)
    except (redis.RedisError, ValueError):
        return
    if count > TESTS_PER_MINUTE:
        raise ProblemError(
            ErrorCode.RATE_LIMITED, "Too many tests in a minute; wait a little and try again."
        )


def run_test(name: str, *, actor: User, ip: str | None) -> TestResult:
    """Run an integration's Test with the saved settings; audited with its outcome."""
    integration = get_integration(name)
    _limit(actor, name)
    result = _tester(integration, actor)()
    with transaction.atomic():
        audit.record(
            "integration.test",
            actor=actor,
            target=AuditTarget("integration", name),
            after={"ok": result.ok, "code": result.code.value},
            ip=ip,
        )
    return result
