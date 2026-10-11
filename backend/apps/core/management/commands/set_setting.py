"""`manage.py set_setting KEY`: change a registered setting from a script (the installer).

The value is never an argument (other users can read command lines): it comes from
standard input (`--stdin`) or from an environment variable (`--env NAME`). It is
validated and audited like a change in the admin's Settings page, and a sensitive
value is never printed. `--if-unset` leaves a setting the admin already changed alone.

    printf '%s' "ops@example.com" | python manage.py set_setting monitoring.alert_emails --stdin
    TOKEN=... python manage.py set_setting monitoring.telegram_bot_token --env TOKEN
"""

import os
import sys
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.registry import SettingKind, UnknownSettingError, get_definition
from apps.core.services import set_setting, setting_state

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
MAX_VALUE_LENGTH = 10_000


def parse_value(kind: SettingKind, raw: str) -> object:
    """Text into the setting's type; the registry checks the rest."""
    text = raw.strip() if kind != SettingKind.STR else raw.rstrip("\r\n")
    if kind == SettingKind.BOOL:
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        msg = "Expected true or false."
        raise ValidationError(msg)
    try:
        if kind == SettingKind.INT:
            return int(text)
        if kind == SettingKind.FLOAT:
            return float(text)
    except ValueError:
        msg = f"Expected a value of type {kind.value}."
        raise ValidationError(msg) from None
    return text


class Command(BaseCommand):
    help = "Set a registered setting; the value comes from stdin or an environment variable."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("key", help="The setting's key, e.g. monitoring.alert_emails.")
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--stdin", action="store_true", help="Read the value from stdin.")
        source.add_argument("--env", metavar="NAME", help="Read the value from this variable.")
        parser.add_argument(
            "--if-unset",
            action="store_true",
            help="Only when the setting still has its default value.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        key = options["key"]
        try:
            definition = get_definition(key)
        except UnknownSettingError:
            raise CommandError(f"Unknown setting: {key}") from None
        if options["stdin"]:
            raw = sys.stdin.read(MAX_VALUE_LENGTH + 1)
        else:
            raw = os.environ.get(options["env"], "")
        if len(raw) > MAX_VALUE_LENGTH:
            raise CommandError("The value is too long.")
        if options["if_unset"] and not setting_state(key).is_default:
            self.stdout.write(f"{key} was already changed; left as it is.")
            return
        try:
            set_setting(key, parse_value(definition.kind, raw), actor=None)
        except ValidationError as error:
            raise CommandError(f"{key}: {' '.join(error.messages)}") from None
        shown = "(hidden)" if definition.sensitive else repr(setting_state(key).value)
        self.stdout.write(f"{key} set to {shown}.")
