from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        from apps.accounts.checks import check_field_encryption_key  # noqa: PLC0415

        checks.register(check_field_encryption_key, checks.Tags.security)
