"""Payment providers by code (SPEC §7.6): manual, Stripe and Moyasar (ADR-0012)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from apps.billing.providers.base import PaymentProvider
from apps.billing.providers.manual import ManualProvider
from apps.billing.providers.moyasar import MoyasarProvider
from apps.billing.providers.stripe import StripeProvider

_ALL: Final[tuple[PaymentProvider, ...]] = (ManualProvider(), StripeProvider(), MoyasarProvider())
PROVIDERS: Final[Mapping[str, PaymentProvider]] = MappingProxyType(
    {str(provider.code): provider for provider in _ALL}
)


def get_provider(code: str) -> PaymentProvider | None:
    return PROVIDERS.get(code)


def enabled_providers() -> list[PaymentProvider]:
    return [provider for provider in PROVIDERS.values() if provider.is_enabled()]
