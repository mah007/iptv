import os
from typing import Any

from celery import Celery, signals

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("smart_iptv")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@signals.setup_logging.connect
def use_django_logging(**_kwargs: Any) -> None:
    """Keep Django's LOGGING (structlog, redaction, stderr) in workers and beat.

    With a receiver on this signal Celery leaves logging alone instead of
    replacing the root handlers with its own unredacted format.
    """
