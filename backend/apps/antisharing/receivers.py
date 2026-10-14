"""Signal receivers of the anti-sharing app (ADR-0021)."""

from typing import Any

from celery.signals import worker_ready


@worker_ready.connect(dispatch_uid="antisharing.geoip_on_worker_start")
def geoip_on_worker_start(sender: Any = None, **kwargs: Any) -> None:
    """A new installation (or a lost volume) gets its GeoIP databases on first start,
    without waiting for the daily check (`install.sh --update` also asks for them)."""
    from apps.antisharing.tasks import update_geoip  # noqa: PLC0415

    try:
        update_geoip.delay(only_if_missing=True)
    except Exception:
        return
