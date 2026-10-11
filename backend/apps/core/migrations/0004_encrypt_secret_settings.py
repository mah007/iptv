"""Encrypt the secret settings already stored, and move the Telegram settings from the
monitoring group to the notifications group (ADR-0019)."""

from typing import Any

from django.db import migrations

from apps.core.migration_helpers import decrypt_rows, encrypt_rows, rename_rows

# Frozen at the time of this migration; later migrations pass their own lists.
SECRET_KEYS = (
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
)
RENAMES = {
    "monitoring.telegram_bot_token": "notifications.telegram_bot_token",
    "monitoring.telegram_chat_id": "notifications.telegram_chat_id",
}


def forward(apps: Any, schema_editor: Any) -> None:
    setting = apps.get_model("core", "Setting")
    rename_rows(setting, RENAMES)
    encrypt_rows(setting, SECRET_KEYS)


def backward(apps: Any, schema_editor: Any) -> None:
    setting = apps.get_model("core", "Setting")
    decrypt_rows(setting, SECRET_KEYS)
    rename_rows(setting, {new: old for old, new in RENAMES.items()})


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_setting_ciphertext"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
