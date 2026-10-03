"""Scheduled jobs of the playback app (beat entry in config.settings.base)."""

from celery import shared_task

from apps.playback import services


@shared_task(name="apps.playback.tasks.sweep_sessions", ignore_result=True)
def sweep_sessions() -> int:
    """Every 60 s on the default queue: end idle sessions and keep open rows in step
    with redis-state (SPEC §7.4 session close). Returns the sessions it closed."""
    result = services.sweep()
    return result.reaped + result.orphans
