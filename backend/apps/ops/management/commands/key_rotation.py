"""Key rotation steps that need Django (`scripts/rotate-keys.sh`; ADR-0020).

manage.py key_rotation record KIND PHASE [--detail key=value ...]
    audit a rotation step as keys.rotate (KIND: media_token, secret_key,
    field_encryption, db, valkey)
manage.py key_rotation media-token-finish
    exit 0 when the previous media key may be dropped, 3 (and when) if not yet
manage.py key_rotation reencrypt
    re-encrypt every stored secret with the first FIELD_ENCRYPTION_KEY
manage.py key_rotation check-first-key
    exit 0 when the first FIELD_ENCRYPTION_KEY alone reads every stored secret
"""

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.ops import keys


class Command(BaseCommand):
    help = "Key rotation steps: record, media-token-finish, reencrypt, check-first-key."

    def add_arguments(self, parser: CommandParser) -> None:
        steps = parser.add_subparsers(dest="step", required=True)
        record = steps.add_parser("record", help="audit a rotation step")
        record.add_argument("kind", choices=keys.KINDS)
        record.add_argument("phase")
        record.add_argument("--detail", action="append", default=[], metavar="KEY=VALUE")
        steps.add_parser("media-token-finish", help="may the previous media key go now?")
        steps.add_parser("reencrypt", help="re-encrypt stored secrets with the first key")
        steps.add_parser("check-first-key", help="does the first key read every secret?")
        media = steps.add_parser("media-keys", help="the media key file's next step")
        media.add_argument("action", choices=("stage", "promote", "retire"))

    def handle(self, *args: Any, **options: Any) -> None:
        step = options["step"]
        if step == "record":
            detail = dict(item.split("=", 1) for item in options["detail"] if "=" in item)
            keys.record(options["kind"], options["phase"], **detail)
            self.stdout.write(f"Recorded keys.rotate {options['kind']} ({options['phase']}).")
        elif step == "media-token-finish":
            check = keys.media_token_finish()
            if not check.ready:
                until = check.not_before.isoformat() if check.not_before else "?"
                self.stdout.write(f"Not yet: signed URLs may use the previous key until {until}.")
                sys.exit(3)
            self.stdout.write("Ready: no signed URL uses the previous key any more.")
        elif step == "media-keys":
            try:
                data = json.loads(sys.stdin.read())
                action = {"stage": keys.stage, "promote": keys.promote, "retire": keys.retire}
                new_keys = action[options["action"]](data)
            except (ValueError, KeyError, TypeError) as exc:
                msg = f"The key file can't take this step: {exc}"
                raise CommandError(msg) from None
            sys.stdout.write(keys.dumps(new_keys))
        elif step == "reencrypt":
            result = keys.reencrypt_all()
            for field, count in result.fields.items():
                self.stdout.write(f"  {field}: {count}")
            self.stdout.write(f"  core.Setting (secrets): {result.settings}")
            self.stdout.write(f"Re-encrypted {result.total} values with the first key.")
        else:
            broken = keys.undecryptable_with_first_key()
            if broken:
                msg = f"{len(broken)} values need an older key: " + ", ".join(broken[:10])
                raise CommandError(msg)
            self.stdout.write("Every stored secret reads with the first key alone.")
